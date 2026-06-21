"""
数据管理 Agent。

职责：
    1. 查看课表（主修 + 辅修，按星期排列）
    2. 录入主修课表（逐门添加，解析自然语言课程信息）
    3. 修改个人信息（年级、可用时段、兴趣标签）
    4. 删除课表条目

多轮交互流程：
    IDLE → 用户输入"录入课表" → AWAIT_DATA_INPUT
    → 用户输入课程信息 → 添加 → 询问是否继续
    → 用户输入"完成" → IDLE
"""

import logging
import re
from typing import Optional

from src.graph.state import AgentState, ConversationPhase
from src.storage.sqlite_client import (
    get_timetable, add_timetable_entry, delete_timetable_entry,
    upsert_user, get_user,
)
from src.tools.base_tools import format_markdown_table, get_current_user

logger = logging.getLogger(__name__)

# 课程信息解析正则
COURSE_PARSE_RE = re.compile(
    r'(?P<code>[A-Z]{2,4}\d{3,4})\s*'
    r'周(?P<day>[一二三四五六日1-7])'
    r'(?P<period_start>\d+)[-~至](?P<period_end>\d+)节'
    r'(?:\s*(?P<location>.+?))?\s*'
    r'(?:考试[:：](?P<exam>.+?))?\s*$',
    re.IGNORECASE,
)

DAY_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7,
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
}


class DataManagerAgent:
    """数据管理 Agent — 课表 CRUD + 个人信息编辑。"""

    def run(self, state: AgentState) -> AgentState:
        user_id = state.get("user_id", "")
        phase = state.get("conversation_phase", "")
        user_input = state.get("user_input", "")

        # 首次进入 → 识别子命令
        if phase != ConversationPhase.AWAIT_DATA_INPUT.value:
            return self._entry(state, user_id, user_input)

        # 已在 AWAIT_DATA_INPUT → 继续数据录入或退出
        return self._continue_input(state, user_id, user_input)

    def _entry(self, state: AgentState, user_id: str, user_input: str) -> AgentState:
        """首次进入数据管理，识别子功能并分发。"""
        # 录入课表（必须在"查看/课表"之前检查，因为"录入课表"也包含"课表"）
        if any(kw in user_input for kw in ["录入", "添加", "新增"]):
            state["conversation_phase"] = ConversationPhase.AWAIT_DATA_INPUT.value
            state["final_response"] = (
                "📝 **数据录入模式**\n\n"
                "请按以下格式输入主修课程信息（每次一门）：\n\n"
                "`课程代码 周X 第N-N节 教室位置 考试:时间`\n\n"
                "示例: `CS101 周一1-3节 教三楼201 考试:第18周`\n\n"
                "输入「**完成**」或「**取消**」退出录入模式。"
            )
            return state

        # 查看课表
        if any(kw in user_input for kw in ["查看", "我的课表", "课表", "看看", "显示"]):
            return self._show_timetable(state, user_id)

        # 修改个人信息
        if any(kw in user_input for kw in ["修改", "编辑", "更新信息", "我的信息"]):
            return self._show_and_prompt_info_edit(state, user_id)

        # 删除
        if any(kw in user_input for kw in ["删除", "移除"]):
            return self._show_delete_prompt(state, user_id)

        # 默认：展示课表
        return self._show_timetable(state, user_id)

    def _continue_input(self, state: AgentState, user_id: str,
                        user_input: str) -> AgentState:
        """在 AWAIT_DATA_INPUT 阶段，解析课程信息或退出。"""
        # Check for profile edit command
        PROFILE_EDIT_RE = re.compile(
            r'修改信息\s+(姓名|主修|年级|兴趣|name|major|grade|interests)\s+(.+)', re.I
        )

        profile_match = PROFILE_EDIT_RE.match(user_input)
        if profile_match:
            field = profile_match.group(1)
            value = profile_match.group(2).strip()
            return self._handle_profile_edit(user_id, field, value, state)

        if user_input.strip() in ("完成", "结束", "好了", "取消", "退出"):
            state["final_response"] = "✅ 数据录入完成。"
            state["conversation_phase"] = ConversationPhase.IDLE.value
            return state

        parsed = self._parse_course_input(user_input)
        if parsed:
            try:
                add_timetable_entry({
                    "user_id": user_id,
                    "course_code": parsed["code"],
                    "course_type": "major",
                    "day_of_week": parsed["day"],
                    "period_start": parsed["period_start"],
                    "period_end": parsed["period_end"],
                    "location": parsed.get("location", ""),
                    "exam_time": parsed.get("exam", ""),
                    "week_start": 1,
                    "week_end": 16,
                })

                # 标记主修课表已录入
                state["has_major_timetable"] = True
                existing = get_user(user_id) or {}
                upsert_user(
                    user_id=user_id,
                    student_name=existing.get("student_name", "未命名"),
                    major=existing.get("major", "未设置"),
                    grade=existing.get("grade"),
                    available_slots=existing.get("available_slots"),
                    interests=existing.get("interests"),
                    has_minor=existing.get("has_minor", 0),
                    minor_program=existing.get("minor_program"),
                )

                state["final_response"] = (
                    f"✅ 已添加 {parsed['code']}（周{parsed['day']} "
                    f"第{parsed['period_start']}-{parsed['period_end']}节）\n\n"
                    "继续添加下一门课程，或输入「**完成**」退出。"
                )
            except Exception as e:
                logger.error("Failed to add timetable entry: %s", e)
                state["final_response"] = (
                    f"⚠️ 添加失败: {e}\n\n请检查格式后重试。"
                )
        else:
            state["final_response"] = (
                "❓ 无法识别课程信息。请使用格式:\n\n"
                "`课程代码 周X 第N-N节 教室位置`\n\n"
                "例如: `CS101 周一1-3节 教三楼201`\n"
                "或输入「**完成**」退出。"
            )

        return state

    def _handle_profile_edit(self, user_id: str, field: str, value: str,
                              state: AgentState) -> AgentState:
        """处理单字段个人信息修改。"""
        FIELD_MAP = {
            "姓名": "student_name", "name": "student_name",
            "主修": "major", "major": "major",
            "年级": "grade", "grade": "grade",
            "兴趣": "interests", "interests": "interests",
        }
        db_field = FIELD_MAP.get(field)
        if not db_field:
            state["final_response"] = f"⚠️ 不支持的字段：{field}。可修改：姓名、主修、年级、兴趣"
            state["conversation_phase"] = ConversationPhase.IDLE.value
            return state

        existing = get_user(user_id) or {}
        updates = {
            "student_name": existing.get("student_name", ""),
            "major": existing.get("major", ""),
            "grade": existing.get("grade"),
            "available_slots": existing.get("available_slots"),
            "interests": existing.get("interests"),
            "has_minor": existing.get("has_minor", 0),
            "minor_program": existing.get("minor_program"),
        }
        updates[db_field] = value

        upsert_user(user_id=user_id, **updates)

        state["final_response"] = f"✅ 已更新 {field} 为：{value}"
        state["conversation_phase"] = ConversationPhase.IDLE.value
        return state

    def _show_timetable(self, state: AgentState, user_id: str) -> AgentState:
        """展示用户完整课表（按星期排列）。"""
        major_courses = get_timetable(user_id, course_type="major")
        minor_courses = get_timetable(user_id, course_type="minor")

        # 按星期分组
        by_day = {}
        for c in major_courses + minor_courses:
            day = c.get("day_of_week", 0)
            if day not in by_day:
                by_day[day] = []
            by_day[day].append(c)

        day_names = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}
        lines = ["📅 **我的课表**\n"]

        for day_idx in sorted(by_day):
            lines.append(f"**{day_names.get(day_idx, f'周{day_idx}')}**")
            for c in sorted(by_day[day_idx],
                           key=lambda x: x.get("period_start", 0)):
                course_type = "辅修" if c.get("course_type") == "minor" else "主修"
                lines.append(
                    f"  - {c.get('course_code', '?')} [{course_type}] "
                    f"第{c.get('period_start', '?')}-{c.get('period_end', '?')}节 "
                    f"{c.get('location', '') or ''}"
                )
            lines.append("")

        if not by_day:
            lines.append("暂无课程数据。输入「**录入课表**」开始添加。")

        # 添加操作提示
        lines.append("---")
        lines.append("📌 可用操作: `录入课表` | `删除课程` | `修改信息`")

        state["final_response"] = "\n".join(lines)
        state["conversation_phase"] = ConversationPhase.IDLE.value
        return state

    def _show_delete_prompt(self, state: AgentState, user_id: str) -> AgentState:
        """展示可删除的课表条目。"""
        major_courses = get_timetable(user_id, course_type="major")
        minor_courses = get_timetable(user_id, course_type="minor")

        if not major_courses and not minor_courses:
            state["final_response"] = "📭 课表为空，没有可删除的课程。"
            state["conversation_phase"] = ConversationPhase.IDLE.value
            return state

        lines = ["🗑️ **删除课程**\n请指定要删除的课程代码：\n"]
        for c in major_courses + minor_courses:
            course_type = "辅修" if c.get("course_type") == "minor" else "主修"
            lines.append(
                f"  - `{c.get('course_code', '?')}` [{course_type}] "
                f"({c.get('location', '无地点')})"
            )

        lines.append("\n输入课程代码确认删除，例如：`删除 CS101`")

        state["final_response"] = "\n".join(lines)
        state["conversation_phase"] = ConversationPhase.IDLE.value
        return state

    def _show_and_prompt_info_edit(self, state: AgentState, user_id: str) -> AgentState:
        """展示当前个人信息并提供编辑指引。"""
        user = get_current_user(user_id)

        lines = [
            "👤 **我的信息**\n",
            f"  姓名: {user.get('student_name', '未设置')}",
            f"  主修: {user.get('major', '未设置')}",
            f"  年级: {user.get('grade', '未设置')}",
            f"  兴趣: {user.get('interests', '[]')}",
            "",
            "---",
            "📝 修改信息请使用: `修改信息 字段 新值`",
            "可用字段: `年级` `专业` `兴趣`",
            "例如: `修改信息 年级 大二`",
        ]

        state["final_response"] = "\n".join(lines)
        state["conversation_phase"] = ConversationPhase.IDLE.value
        return state

    @staticmethod
    def _parse_course_input(text: str) -> Optional[dict]:
        """解析"CS101 周一1-3节 教三楼201" 格式的课程输入。"""
        # 先尝试完整正则
        m = COURSE_PARSE_RE.match(text.strip())
        if m:
            day_str = m.group("day")
            day = DAY_MAP.get(day_str)
            if day is None:
                return None
            try:
                ps = int(m.group("period_start"))
                pe = int(m.group("period_end"))
            except (ValueError, TypeError):
                return None
            return {
                "code": m.group("code"),
                "day": day,
                "period_start": ps,
                "period_end": pe,
                "location": (m.group("location") or "").strip(),
                "exam": (m.group("exam") or "").strip(),
            }

        # 宽松匹配：课程代码 + 时间信息
        code_match = re.search(r'[A-Z]{2,4}\d{3,4}', text, re.IGNORECASE)
        if not code_match:
            return None
        code = code_match.group()

        day_match = re.search(r'周([一二三四五六日1-7])', text)
        if not day_match:
            return None
        day = DAY_MAP.get(day_match.group(1))
        if day is None:
            return None

        period_match = re.search(r'(\d+)[-~至](\d+)节', text)
        if not period_match:
            return None
        ps = int(period_match.group(1))
        pe = int(period_match.group(2))

        location_match = re.search(
            r'(?:在|位置[:：]?\s*)?(.+?)(?:考试|$)', text
        )
        location = ""
        if location_match:
            loc_candidate = location_match.group(1).strip()
            if not re.match(r'^周\d', loc_candidate):
                location = loc_candidate

        exam_match = re.search(r'考试[:：]\s*(.+?)$', text)

        return {
            "code": code,
            "day": day,
            "period_start": ps,
            "period_end": pe,
            "location": location,
            "exam": exam_match.group(1) if exam_match else "",
        }
