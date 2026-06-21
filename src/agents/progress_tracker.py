"""
进度跟踪 Agent。

职责：
  1. 查询学习进度 → 表格化展示成绩、考勤、作业完成状态
  2. 录入学习进度 → 解析用户输入 → 写入 SQLite
  3. 计算统计指标 → 出勤率、平均分、落后课程标记

依赖：
  - SQLite: study_progress 表
  - 不依赖 RAG / Redis
"""

import re
import logging
from typing import List, Dict, Optional

from src.graph.state import AgentState, ConversationPhase
from src.storage.sqlite_client import (
    get_study_progress, add_study_progress,
    get_timetable, get_course, get_user,
)
from src.storage.redis_client import save_session_state

logger = logging.getLogger(__name__)


class ProgressTrackerAgent:
    """进度跟踪 Agent。"""

    def run(self, state: AgentState) -> AgentState:
        """
        主入口：根据用户输入判断「查询」还是「录入」。
        """
        user_input = state.get("user_input", "")
        phase = state.get("conversation_phase", "")

        # 如果处于等待录入阶段
        if phase == ConversationPhase.AWAIT_PROGRESS_INPUT.value:
            return self._handle_record_input(state)

        # 判断意图：录入 vs 查询
        if self._is_record_intent(user_input):
            return self._handle_record_prompt(state)
        else:
            return self._handle_query(state)

    # ── 查询进度 ──────────────────────────────────────

    def _handle_query(self, state: AgentState) -> AgentState:
        """
        查询学习进度：从 SQLite 读取 → 表格化展示 → 统计汇总。
        """
        user_id = state["user_id"]
        user_input = state.get("user_input", "")
        logger.info(f"ProgressTracker: 查询进度 user={user_id}")

        user = get_user(user_id)

        # 尝试从输入中提取课程代码
        course_code = self._extract_course_code(user_input)

        # 查询进度
        records = get_study_progress(user_id, course_code=course_code)

        if not records:
            state["final_response"] = (
                "📊 **学习进度查询**\n\n"
                "暂无学习进度记录。\n\n"
                "💡 您可以：\n"
                "  • 输入「录入成绩 FIN101 第5周 作业88 考勤正常」记录进度\n"
                "  • 输入「录入成绩 85」按引导录入"
            )
            return state

        # 按课程分组
        by_course: Dict[str, List[Dict]] = {}
        for r in records:
            code = r.get("course_code", "")
            if code not in by_course:
                by_course[code] = []
            by_course[code].append(r)

        # 获取课程名称映射
        course_names = {}
        for code in by_course:
            info = get_course(code)
            course_names[code] = info.get("course_name", code) if info else code
            course_type = "主修" if self._is_major_course(user_id, code) else "辅修"

        # 计算统计数据
        stats = self._calculate_stats(records)

        # 格式化输出
        lines = ["📊 **学习进度总览**", ""]

        if user:
            lines.append(f"👤 {user.get('student_name', '')} | "
                        f"主修：{user.get('major', '')} | "
                        f"辅修：{user.get('minor_program', '未选')}")
            lines.append("")

        for code, recs in by_course.items():
            cname = course_names.get(code, code)
            ctype = "🔵" if self._is_major_course(user_id, code) else "🟠"
            lines.append(f"  {ctype} **{code} {cname}**")

            # 表头
            lines.append(f"  {'周次':<6} {'考勤':<8} {'作业':<8} {'小测':<8} {'备注'}")
            lines.append(f"  {'─'*5} {'─'*7} {'─'*7} {'─'*7} {'─'*10}")

            for r in sorted(recs, key=lambda x: x.get("week", 0)):
                week = r.get("week", "?")
                att = r.get("attendance", "-") or "-"
                hw = r.get("homework_score", "-")
                hw_str = f"{hw:.0f}" if isinstance(hw, (int, float)) else str(hw) if hw else "-"
                qz = r.get("quiz_score", "-")
                qz_str = f"{qz:.0f}" if isinstance(qz, (int, float)) else str(qz) if qz else "-"
                note = (r.get("note", "") or "")[:15]

                # 状态图标
                att_icon = {"present": "✅", "absent": "❌", "late": "⚠️"}.get(att, "❓")
                hw_warn = " ⚠️" if isinstance(hw, (int, float)) and hw < 70 else ""
                qz_warn = " ⚠️" if isinstance(qz, (int, float)) and qz < 60 else ""

                lines.append(
                    f"  {str(week):<6} {att_icon}{att:<6} "
                    f"{hw_str}{hw_warn:<7} {qz_str}{qz_warn:<7} {note}"
                )

            # 单课程统计
            course_stats = self._calculate_stats(recs)
            att_rate = course_stats.get("attendance_rate", 0)
            avg_hw = course_stats.get("avg_homework", 0)
            avg_qz = course_stats.get("avg_quiz", 0)
            status_icon = "✅" if avg_hw >= 80 else ("⚠️" if avg_hw >= 70 else "🔴")
            lines.append(
                f"  📈 出勤率：{att_rate:.0%} | 作业均分：{avg_hw:.1f} | "
                f"小测均分：{avg_qz:.1f} | 状态：{status_icon}"
            )
            lines.append("")

        # 全局汇总
        lines.append("─" * 50)
        lines.append(f"📈 **综合统计**")
        lines.append(f"  总出勤率：{stats.get('attendance_rate', 0):.0%}")
        lines.append(f"  作业均分：{stats.get('avg_homework', 0):.1f}")
        lines.append(f"  小测均分：{stats.get('avg_quiz', 0):.1f}")
        lines.append(f"  记录周数：{stats.get('total_weeks', 0)}")

        # 风险提示
        risks = self._check_risks(by_course, records)
        if risks:
            lines.append("")
            lines.append("⚠️ **风险提示：**")
            for risk in risks:
                lines.append(f"  • {risk}")
        else:
            lines.append("")
            lines.append("✅ 当前学习状态良好，请继续保持！")

        state["final_response"] = "\n".join(lines)
        state["intent"] = "track_progress"
        state["conversation_phase"] = ConversationPhase.IDLE.value
        return state

    # ── 录入进度 ──────────────────────────────────────

    def _handle_record_prompt(self, state: AgentState) -> AgentState:
        """
        用户表达了录入进度的意图，但信息可能不完整。
        尝试解析，信息齐全则直接录入，否则引导用户补充。
        """
        user_input = state.get("user_input", "")
        parsed = self._parse_progress_input(user_input, state["user_id"])

        if parsed and parsed.get("course_code") and parsed.get("week"):
            # 信息齐全，直接录入
            return self._do_record(state, parsed)
        else:
            # 信息不全，引导用户
            state["conversation_phase"] = ConversationPhase.AWAIT_PROGRESS_INPUT.value
            save_session_state(state["user_id"], state["session_id"], {
                "conversation_phase": state["conversation_phase"],
            })
            state["final_response"] = (
                "📝 **录入学习进度**\n\n"
                "请按以下格式提供信息：\n"
                "  `课程代码 第X周 作业XX分 考勤正常/缺席/迟到 小测XX分`\n\n"
                "示例：\n"
                "  `FIN101 第5周 作业88 考勤正常 小测85`\n"
                "  `CS301 第3周 作业76 考勤缺席`\n\n"
                "💡 课程代码可选：FIN101, FIN201, FIN301, DS101, DS201, LAW101, LAW201, CS201, CS301, CS202"
            )
            return state

    def _handle_record_input(self, state: AgentState) -> AgentState:
        """处于 AWAIT_PROGRESS_INPUT 阶段的输入处理。"""
        user_input = state.get("user_input", "")
        parsed = self._parse_progress_input(user_input, state["user_id"])

        if parsed and parsed.get("course_code") and parsed.get("week"):
            return self._do_record(state, parsed)
        else:
            state["final_response"] = (
                "🤔 未能解析到完整的录入信息，请重试。\n\n"
                "格式：`课程代码 第X周 作业XX分 考勤正常 小测XX分`\n"
                "示例：`FIN101 第5周 作业88 考勤正常`"
            )
            return state

    def _do_record(self, state: AgentState, parsed: Dict) -> AgentState:
        """执行数据库写入。"""
        user_id = state["user_id"]
        entry = {
            "user_id": user_id,
            "course_code": parsed["course_code"],
            "week": parsed["week"],
            "homework_score": parsed.get("homework_score"),
            "attendance": parsed.get("attendance", "present"),
            "quiz_score": parsed.get("quiz_score"),
            "note": parsed.get("note", ""),
        }
        add_study_progress(entry)

        course = get_course(parsed["course_code"])
        cname = course.get("course_name", parsed["course_code"]) if course else parsed["course_code"]

        state["conversation_phase"] = ConversationPhase.IDLE.value
        save_session_state(user_id, state["session_id"], {
            "conversation_phase": "idle",
        })

        lines = [
            "✅ **进度已录入！**",
            "",
            f"  📚 课程：{parsed['course_code']} {cname}",
            f"  📅 第 {parsed['week']} 周",
        ]
        if parsed.get("homework_score") is not None:
            lines.append(f"  📝 作业：{parsed['homework_score']} 分")
        if parsed.get("attendance"):
            att_map = {"present": "✅ 正常", "absent": "❌ 缺席", "late": "⚠️ 迟到"}
            lines.append(f"  🏫 考勤：{att_map.get(parsed['attendance'], parsed['attendance'])}")
        if parsed.get("quiz_score") is not None:
            lines.append(f"  📋 小测：{parsed['quiz_score']} 分")

        lines.append("")
        lines.append("💡 输入「查看进度」查看最新学习状态。")
        state["final_response"] = "\n".join(lines)
        state["intent"] = "track_progress"
        return state

    # ── 输入解析 ──────────────────────────────────────

    def _is_record_intent(self, text: str) -> bool:
        """判断用户输入是否为录入意图。"""
        record_keywords = ["录入", "记录", "提交", "添加"]
        return any(kw in text for kw in record_keywords)

    def _extract_course_code(self, text: str) -> Optional[str]:
        """从文本中提取课程代码（如 FIN101）。"""
        m = re.search(r'([A-Za-z]{2,4}\d{3})', text.upper())
        return m.group(1) if m else None

    def _parse_progress_input(self, text: str, user_id: str) -> Optional[Dict]:
        """
        解析用户的进度录入输入。

        支持格式：
          FIN101 第5周 作业88 考勤正常 小测85
          CS301 第3周 作业76 考勤缺席
          第3周 FIN101 88分 正常
        """
        text = text.strip().upper()
        result = {}

        # 提取课程代码
        code_m = re.search(r'([A-Z]{2,4}\d{3})', text)
        if code_m:
            result["course_code"] = code_m.group(1)
        else:
            # 尝试从用户课表推断
            timetable = get_timetable(user_id)
            for entry in timetable:
                code = entry.get("course_code", "")
                if code in text or code.lower() in text.lower():
                    result["course_code"] = code
                    break

        # 提取周次
        week_m = re.search(r'第\s*(\d+)\s*周', text)
        if week_m:
            result["week"] = int(week_m.group(1))

        # 提取作业分数
        hw_m = re.search(r'作业\s*(\d+\.?\d*)', text)
        if not hw_m:
            hw_m = re.search(r'(\d+\.?\d*)\s*分', text)
        if hw_m:
            result["homework_score"] = float(hw_m.group(1))

        # 提取考勤
        if "缺席" in text or "缺勤" in text:
            result["attendance"] = "absent"
        elif "迟到" in text:
            result["attendance"] = "late"
        elif "正常" in text or "出勤" in text:
            result["attendance"] = "present"

        # 提取小测分数
        quiz_m = re.search(r'小测\s*(\d+\.?\d*)', text)
        if quiz_m:
            result["quiz_score"] = float(quiz_m.group(1))

        return result

    # ── 统计工具 ──────────────────────────────────────

    def _calculate_stats(self, records: List[Dict]) -> Dict:
        """计算学习统计数据。"""
        if not records:
            return {
                "attendance_rate": 0,
                "avg_homework": 0,
                "avg_quiz": 0,
                "total_weeks": 0,
            }

        total = len(records)
        present = sum(1 for r in records if r.get("attendance") == "present")
        hw_scores = [r["homework_score"] for r in records
                     if r.get("homework_score") is not None]
        qz_scores = [r["quiz_score"] for r in records
                     if r.get("quiz_score") is not None]

        return {
            "attendance_rate": present / total if total > 0 else 0,
            "avg_homework": sum(hw_scores) / len(hw_scores) if hw_scores else 0,
            "avg_quiz": sum(qz_scores) / len(qz_scores) if qz_scores else 0,
            "total_weeks": len(set(r.get("week") for r in records)),
        }

    def _check_risks(self, by_course: Dict[str, List[Dict]],
                     all_records: List[Dict]) -> List[str]:
        """检查学业风险。"""
        risks = []

        for code, recs in by_course.items():
            stats = self._calculate_stats(recs)
            course = get_course(code)
            cname = course.get("course_name", code) if course else code

            if stats["attendance_rate"] < 0.85:
                risks.append(f"{cname} 出勤率偏低（{stats['attendance_rate']:.0%}），注意不要缺课。")
            if stats["avg_homework"] < 70:
                risks.append(f"{cname} 作业均分偏低（{stats['avg_homework']:.1f}），建议加强练习。")
            if stats["avg_quiz"] < 60:
                risks.append(f"{cname} 小测均分危险（{stats['avg_quiz']:.1f}），需重点复习。")

        # 缺席统计
        absent_count = sum(1 for r in all_records if r.get("attendance") == "absent")
        if absent_count >= 3:
            risks.append(f"已累计缺席 {absent_count} 次，超过警戒线（3 次），可能影响平时成绩。")

        return risks

    def _is_major_course(self, user_id: str, course_code: str) -> bool:
        """判断课程是否为主修课程。"""
        timetable = get_timetable(user_id, course_type="major")
        return any(e.get("course_code") == course_code for e in timetable)
