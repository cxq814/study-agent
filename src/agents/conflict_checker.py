"""
冲突检测 Agent。

职责：
  1. 时间重叠算法 — 比对主修/辅修课表，检测上课时段与考试时间冲突
  2. 冲突分类 — course_overlap / exam_overlap，critical / warning / info
  3. 生成优化建议 — 调课方向、时间调整方案
  4. 多轮交互 — 检测后等待用户决策（调课/退课/忽略）
  5. 冲突记录写入 SQLite conflict_history

依赖：
  - SQLite: user_timetable, conflict_history, courses
  - Redis: 会话状态（phase 控制、冲突暂存）
"""

import json
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime

from src.graph.state import AgentState, ConversationPhase
from src.storage.sqlite_client import (
    get_timetable, add_conflict_record, get_course, get_user,
)
from src.storage.redis_client import save_session_state

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
#  冲突检测 Agent
# ══════════════════════════════════════════════════════

class ConflictCheckerAgent:
    """冲突检测 Agent —— 时间重叠算法 + 冲突分类 + 决策交互。"""

    def run(self, state: AgentState) -> AgentState:
        """
        主入口：根据 conversation_phase 分发。
        """
        phase = state.get("conversation_phase", "")

        if phase == ConversationPhase.AWAIT_CONFLICT_DECISION.value:
            return self._handle_decision(state)
        else:
            return self._detect_conflicts(state)

    # ── 冲突检测核心 ──────────────────────────────────

    def _detect_conflicts(self, state: AgentState) -> AgentState:
        """
        时间重叠检测主流程。

        1. 加载主修 + 辅修课表
        2. 逐对比较 → 检测上课时间 / 考试时间重叠
        3. 分类（类型 + 严重程度）
        4. 写入 conflict_history
        5. 输出格式化结果 + 优化建议
        6. phase → AWAIT_CONFLICT_DECISION（等待用户决定）
        """
        user_id = state["user_id"]
        logger.info(f"ConflictChecker: 检测冲突 user={user_id}")

        # 1. 加载课表
        major_courses = get_timetable(user_id, course_type="major")
        minor_courses = get_timetable(user_id, course_type="minor")

        if not major_courses:
            state["final_response"] = (
                "⚠️ 未找到主修课表。请先录入主修课程信息。\n"
                "输入「录入课表」开始。"
            )
            state["conversation_phase"] = ConversationPhase.IDLE.value
            return state

        if not minor_courses:
            state["final_response"] = (
                "⚠️ 未找到辅修课表。请先选择辅修方案。\n"
                "输入「推荐辅修」开始。"
            )
            state["conversation_phase"] = ConversationPhase.IDLE.value
            return state

        # 2. 逐对检测
        conflicts = []
        seen_pairs = set()  # 去重：(course_a, course_b)
        for major in major_courses:
            for minor in minor_courses:
                conflict = self._check_pair(major, minor)
                if conflict:
                    pair_key = tuple(sorted([conflict["course_a"], conflict["course_b"]]))
                    if pair_key not in seen_pairs:
                        seen_pairs.add(pair_key)
                        conflicts.append(conflict)

        # 3. 排序（critical 优先）
        severity_order = {"critical": 0, "warning": 1, "info": 2}
        conflicts.sort(key=lambda c: severity_order.get(c["severity"], 9))

        # 4. 写入 SQLite
        for c in conflicts:
            try:
                add_conflict_record({
                    "user_id": user_id,
                    "conflict_type": c["conflict_type"],
                    "severity": c["severity"],
                    "course_a": c["course_a"],
                    "course_b": c["course_b"],
                    "overlap_detail": json.dumps(c.get("overlap_detail", {}), ensure_ascii=False),
                    "suggestion": c.get("suggestion", ""),
                })
            except Exception as e:
                logger.warning(f"Failed to save conflict record: {e}")

        # 5. 冲突数量判断：无冲突直接结束，不进入决策状态
        if len(conflicts) == 0:
            state["current_conflicts"] = []
            state["conversation_phase"] = ConversationPhase.IDLE.value

            save_session_state(user_id, state["session_id"], {
                "conversation_phase": state["conversation_phase"],
                "has_minor_course": state["has_minor_course"],
                "current_conflicts": [],
            })

            state["final_response"] = self._format_conflict_output(conflicts, major_courses, minor_courses)
            state["intent"] = "check_conflict"
            return state

        # 6. 暂存 + 设置等待阶段
        state["current_conflicts"] = conflicts
        state["conversation_phase"] = ConversationPhase.AWAIT_CONFLICT_DECISION.value

        save_session_state(user_id, state["session_id"], {
            "conversation_phase": state["conversation_phase"],
            "has_minor_course": state["has_minor_course"],
            "current_conflicts": conflicts,
        })

        # 7. 格式化输出
        state["final_response"] = self._format_conflict_output(conflicts, major_courses, minor_courses)
        state["intent"] = "check_conflict"
        return state

    def _check_pair(self, major: dict, minor: dict) -> Optional[Dict]:
        """
        比对一对主修-辅修课程，检测所有冲突维度。

        返回：冲突详情 dict，无冲突返回 None。
        """
        conflicts_found = []

        # ── 维度 1：上课时间重叠 ──
        same_day = major.get("day_of_week") == minor.get("day_of_week")
        if same_day:
            period_overlap = self._period_overlap(
                major.get("period_start", 0), major.get("period_end", 0),
                minor.get("period_start", 0), minor.get("period_end", 0),
            )
            if period_overlap > 0:
                conflicts_found.append({
                    "dimension": "course_time",
                    "detail": (
                        f"周{major['day_of_week']} "
                        f"第{major['period_start']}-{major['period_end']}节（主修）"
                        f" 与 "
                        f"第{minor['period_start']}-{minor['period_end']}节（辅修）"
                        f"重叠 {period_overlap} 节"
                    ),
                    "overlap_periods": period_overlap,
                })

        # ── 维度 2：考试时间重叠 ──
        major_exam = major.get("exam_time", "")
        minor_exam = minor.get("exam_time", "")
        if major_exam and minor_exam:
            exam_conflict = self._check_exam_conflict(major_exam, minor_exam)
            if exam_conflict:
                conflicts_found.append({
                    "dimension": "exam_time",
                    "detail": exam_conflict,
                    "overlap_periods": 0,
                })

        if not conflicts_found:
            return None

        # ── 判定严重程度 ──
        max_overlap = max((c.get("overlap_periods", 0) for c in conflicts_found), default=0)
        has_exam_conflict = any(c["dimension"] == "exam_time" for c in conflicts_found)

        severity = self._classify_severity(max_overlap, has_exam_conflict)

        course_a_name = major.get("course_code", "?")
        course_b_name = minor.get("course_code", "?")

        # 获取课程名
        try:
            a_info = get_course(course_a_name)
            b_info = get_course(course_b_name)
            a_label = f"{course_a_name} {a_info.get('course_name','')}" if a_info else course_a_name
            b_label = f"{course_b_name} {b_info.get('course_name','')}" if b_info else course_b_name
        except Exception:
            a_label = course_a_name
            b_label = course_b_name

        # ── 生成建议 ──
        suggestion = self._generate_suggestion(
            severity, conflicts_found, major, minor
        )

        return {
            "conflict_type": "exam_overlap" if has_exam_conflict and max_overlap == 0 else "course_overlap",
            "course_a": course_a_name,
            "course_b": course_b_name,
            "course_a_label": a_label,
            "course_b_label": b_label,
            "severity": severity,
            "conflicts": conflicts_found,
            "overlap_detail": {
                "major_schedule": f"周{major.get('day_of_week','?')} 第{major.get('period_start','?')}-{major.get('period_end','?')}节",
                "minor_schedule": f"周{minor.get('day_of_week','?')} 第{minor.get('period_start','?')}-{minor.get('period_end','?')}节",
            },
            "suggestion": suggestion,
        }

    # ── 时间重叠算法 ──────────────────────────────────

    @staticmethod
    def _period_overlap(ps1: int, pe1: int, ps2: int, pe2: int) -> int:
        """
        计算两段节次的重叠节数。

        例：(1,3) vs (3,5) → 第3节重叠 → 返回 1
            (1,3) vs (2,4) → 第2,3节重叠 → 返回 2
            (1,3) vs (4,6) → 无重叠 → 返回 0
        """
        overlap_start = max(ps1, ps2)
        overlap_end = min(pe1, pe2)
        if overlap_start <= overlap_end:
            return overlap_end - overlap_start + 1  # 包含两端
        return 0

    @staticmethod
    def _check_exam_conflict(exam_a: str, exam_b: str) -> Optional[str]:
        """
        检测两个考试时间是否冲突。

        支持格式：
          - "2025-12-22T09:00"
          - "2025秋期末"
          - "第16周"

        严格匹配：仅当两者都是完整日期时间格式且重叠时才算冲突。
        """
        # 尝试解析 ISO 日期
        try:
            dt_a = datetime.fromisoformat(exam_a)
            dt_b = datetime.fromisoformat(exam_b)
            # 考试通常持续 2 小时
            a_end = dt_a.replace(hour=dt_a.hour + 2)
            b_end = dt_b.replace(hour=dt_b.hour + 2)

            if dt_a.date() == dt_b.date():
                # 同一天，检查时间窗口
                if dt_a < b_end and dt_b < a_end:
                    return (
                        f"主修考试 {exam_a} 与辅修考试 {exam_b} "
                        f"在同一天且时间重叠"
                    )
        except (ValueError, TypeError):
            pass

        # 当两者都是模糊日期（如"2025秋期末"）且内容相同 → 标记为潜在冲突
        if exam_a.strip() == exam_b.strip() and not any(
            c.isdigit() and len(c) >= 10 for c in [exam_a, exam_b]
        ):
            return f"两门课程考试时间相同：{exam_a}，可能冲突"

        return None

    @staticmethod
    def _classify_severity(overlap_periods: int, has_exam_conflict: bool) -> str:
        """
        冲突严重程度分类。

        critical:
          - 3 节及以上上课时间重叠
          - 考试直接冲突（同日期+时间重叠）
        warning:
          - 1-2 节上课时间部分重叠
        info:
          - 无实际冲突（正常状态，不应出现）
        """
        if has_exam_conflict:
            return "critical"
        if overlap_periods >= 3:
            return "critical"
        if overlap_periods >= 1:
            return "warning"
        return "info"

    @staticmethod
    def _generate_suggestion(severity: str, conflict_details: List[Dict],
                             major: dict, minor: dict) -> str:
        """
        根据冲突类型和严重程度生成优化建议。
        """
        parts = []

        for cf in conflict_details:
            dim = cf.get("dimension", "")

            if dim == "course_time":
                overlap = cf.get("overlap_periods", 0)
                day = major.get("day_of_week", "?")

                if severity == "critical":
                    parts.append(
                        f"⚠️ 严重冲突：建议更换 {minor.get('course_code','')} 的上课时间，"
                        f"或将 {major.get('course_code','')} 调整到其它时段。"
                        f"当前周{day}已重叠 {overlap} 节课。"
                    )
                elif severity == "warning":
                    parts.append(
                        f"💡 轻微冲突：周{day}仅重叠 {overlap} 节，"
                        f"可尝试与任课教师协商调整，或确认是否允许部分重叠选课。"
                    )

            elif dim == "exam_time":
                parts.append(
                    f"📅 考试冲突警告：建议尽早与教务沟通，"
                    f"申请其中一门缓考或调整考试时间。"
                )

        if not parts:
            parts.append("未检测到需要处理的冲突。")

        return " ".join(parts)

    # ── 决策处理 ──────────────────────────────────────

    def _handle_decision(self, state: AgentState) -> AgentState:
        """
        处理用户对冲突的决策。

        支持：
          - "忽略冲突" / "暂不处理" → 记录决策，phase → IDLE
          - "调课 FIN201" / "换时间" → 提示调整操作（Phase 5 完善）
          - "退课 FIN201" → 提示退课操作
          - 其他 → 提示用户明确选择
        """
        user_input = state.get("user_input", "")
        conflicts = state.get("current_conflicts", [])
        user_id = state["user_id"]

        logger.info(f"ConflictChecker: 决策处理 input='{user_input[:50]}'")

        # ── 全局指令拦截：命中则跳过冲突逻辑，直接回到空闲 ──
        _GLOBAL_COMMANDS = ["推荐辅修", "推荐资源", "检测冲突", "生成报告", "录入课表"]
        if any(cmd in user_input for cmd in _GLOBAL_COMMANDS):
            state["conversation_phase"] = ConversationPhase.IDLE.value
            state["current_conflicts"] = []
            state["intent"] = "check_conflict"
            return state

        # ── 调课：一键清空辅修（快捷操作），回到空闲状态 ──
        # 注意：此处的「调课」是冲突检测上下文中的快捷清空，触发词为「调课/调整/换时间/改时间」。
        # 与课程调整 Agent (course_adjuster) 的「退课/换课」（精细化逐门操作）是两套独立功能：
        #   - 冲突检测快捷清空：一键删除全部辅修数据，重新来过
        #   - 课程调整 Agent：逐门退课/换课，带确认流程和冲突预检
        # 两者触发关键词不同（"调课" ≠ "退课"），路由引擎通过 adjust_schedule vs course_adjust 意图分流。
        if any(kw in user_input for kw in ["调课", "调整", "换时间", "改时间"]):
            from src.storage.sqlite_client import clear_user_minor_data
            clear_user_minor_data(user_id)
            state["has_minor_course"] = False
            state["current_conflicts"] = []
            state["final_response"] = (
                "🔄 已为你清空当前辅修选择，请重新发起推荐辅修，挑选合适方案"
            )
            state["conversation_phase"] = ConversationPhase.IDLE.value
            state["intent"] = "check_conflict"
            return state

        # 解析决策
        decision = self._parse_decision(user_input, conflicts)

        if decision == "ignore":
            state["final_response"] = (
                "✅ 已记录您的选择：**暂不处理冲突**。\n\n"
                "冲突记录已保存，您可以在学期中随时重新检测。\n"
                "💡 如后续出现课程时间调整，输入「检测冲突」重新扫描。"
            )
            state["conversation_phase"] = ConversationPhase.IDLE.value
            state["current_conflicts"] = []

        elif decision == "adjust":
            state["final_response"] = (
                "🔧 **课程调整指引**\n\n"
                "建议操作步骤：\n"
                "1. 确认冲突课程是否有多时段可选（向开课院系咨询）\n"
                "2. 输入「退课 [课程代码]」退选冲突课程\n"
                "3. 输入「推荐辅修」重新规划\n\n"
                "或直接联系教务老师协调处理。"
            )
            state["conversation_phase"] = ConversationPhase.IDLE.value
            state["current_conflicts"] = []

        elif decision == "drop":
            state["final_response"] = (
                "如需退课，请输入「退课 [课程代码]」。\n"
                "例如：「退课 FIN201」"
            )
            # 保持当前 phase，让 course_adjuster 处理
            state["conversation_phase"] = ConversationPhase.IDLE.value
            state["current_conflicts"] = []

        else:
            # 无法识别，展示选项
            critical_count = sum(1 for c in conflicts if c.get("severity") == "critical")
            warn_count = sum(1 for c in conflicts if c.get("severity") == "warning")

            lines = [
                "🤔 请告诉我您打算如何处理这些冲突：",
                "",
            ]
            if critical_count > 0:
                lines.append(f"  ⚠️ 您有 {critical_count} 个严重冲突需要优先处理。")
            if warn_count > 0:
                lines.append(f"  💡 您有 {warn_count} 个轻微冲突可以关注。")
            lines.extend([
                "",
                "请选择操作：",
                "  • 输入「忽略」— 暂不处理，保存记录",
                "  • 输入「调课」— 查看调整建议",
                "  • 输入「退课 [课程代码]」— 退选冲突辅修课",
            ])
            state["final_response"] = "\n".join(lines)

        # 持久化
        save_session_state(user_id, state["session_id"], {
            "conversation_phase": state["conversation_phase"],
            "has_minor_course": state["has_minor_course"],
            "current_conflicts": state.get("current_conflicts", []),
        })

        state["intent"] = "check_conflict"
        return state

    @staticmethod
    def _parse_decision(user_input: str, conflicts: List[Dict]) -> str:
        """
        解析用户决策。

        返回: "ignore" / "adjust" / "drop" / ""（无法识别）
        """
        text = user_input.strip()

        # 忽略
        if any(kw in text for kw in ["忽略", "暂不处理", "不管", "跳过", "知道了", "好的"]):
            return "ignore"

        # 调课
        if any(kw in text for kw in ["调课", "调整", "换时间", "改时间", "怎么办"]):
            return "adjust"

        # 退课
        if any(kw in text for kw in ["退课", "退选", "取消", "不修"]):
            return "drop"

        return ""

    # ── 输出格式化 ────────────────────────────────────

    @staticmethod
    def _format_conflict_output(conflicts: List[Dict],
                                major_courses: List[Dict],
                                minor_courses: List[Dict]) -> str:
        """格式化冲突检测结果为美观文本。"""
        lines = [
            "📋 **课程冲突检测报告**",
            "",
            f"  主修课程：{len(major_courses)} 门",
            f"  辅修课程：{len(minor_courses)} 门",
            f"  检测到冲突：{len(conflicts)} 个",
            "",
        ]

        if not conflicts:
            lines.append("✅ **未检测到时间冲突！**")
            lines.append("")
            lines.append("您的辅修课程安排与主修课表没有时间上的冲突。")
            lines.append("建议继续保持当前选课方案 🎉")
            return "\n".join(lines)

        # 分类展示（去重后合计最多展示前 10 项明细）
        critical = [c for c in conflicts if c.get("severity") == "critical"]
        warning = [c for c in conflicts if c.get("severity") == "warning"]

        MAX_SHOW = 10
        show_critical = critical[:MAX_SHOW]
        remaining = MAX_SHOW - len(show_critical)
        show_warning = warning[:remaining] if remaining > 0 else []

        if critical:
            lines.append("━" * 50)
            lines.append(f"🔴 **严重冲突（{len(critical)} 个）** — 需要立即处理")
            lines.append("━" * 50)
            lines.append("")
            for i, c in enumerate(show_critical, 1):
                lines.append(f"**{i}. {c['course_a_label']} ⚡ {c['course_b_label']}**")
                for cf in c.get("conflicts", []):
                    lines.append(f"   └─ {cf.get('detail', '')}")
                lines.append(f"   💬 建议：{c.get('suggestion', '')}")
                lines.append("")
            if len(critical) > len(show_critical):
                lines.append(f"  …及其他 {len(critical) - len(show_critical)} 项严重冲突")
                lines.append("")

        if warning:
            lines.append("━" * 50)
            lines.append(f"🟡 **轻微冲突（{len(warning)} 个）** — 建议关注")
            lines.append("━" * 50)
            lines.append("")
            for i, c in enumerate(show_warning, 1):
                idx = i + len(show_critical)
                lines.append(f"**{idx}. {c['course_a_label']} ↔ {c['course_b_label']}**")
                for cf in c.get("conflicts", []):
                    lines.append(f"   └─ {cf.get('detail', '')}")
                lines.append(f"   💬 建议：{c.get('suggestion', '')}")
                lines.append("")
            if len(warning) > len(show_warning):
                lines.append(f"  …及其他 {len(warning) - len(show_warning)} 项轻微冲突")
                lines.append("")

        lines.append("━" * 50)
        lines.append("")
        lines.append("📌 **下一步操作：**")
        lines.append("  • 输入「忽略」— 暂不处理冲突")
        lines.append("  • 输入「调课」— 查看具体调整方案")
        lines.append("  • 输入「退课 [课程代码]」— 退选冲突辅修课程")

        return "\n".join(lines)
