"""
课程调整 Agent。

职责（细粒度逐门操作，与冲突检测的"调课"一键清空不同）：
    1. 退课：删除指定辅修课程
    2. 换课：退选一门 + 加选一门，带冲突预检
    3. 展示当前辅修课表

多轮交互流程：
    IDLE → 用户输入"退课" → 展示辅修课表 → AWAIT_COURSE_ADJUSTMENT
    → 用户输入"退 FIN201" → 确认 → 执行 → IDLE
    → 用户输入"换 FIN201 换 CS201" → 预检冲突 → 确认 → 执行 → IDLE

与 conflict_checker 中"调课"的关系：
    - 调课 (adjust_schedule): 一键清空所有辅修，重新来过
    - 退课/换课 (course_adjust): 逐门操作，带确认和冲突预检
    - 触发关键词不同（"调课" ≠ "退课"），流程独立
"""

import logging
import re

from src.graph.state import AgentState, ConversationPhase
from src.storage.sqlite_client import (
    get_timetable, get_course, get_user, upsert_user,
    delete_timetable_entry,
)
from src.tools.course_edit_tools import swap_course, validate_schedule

logger = logging.getLogger(__name__)

# 换课格式: "换 FIN201 换 DS201" 或 "换 FIN201 → DS201"（忽略大小写）
SWAP_RE = re.compile(
    r'(?:换|退选|删除)\s*(?P<drop>[A-Z]{2,4}\d{3,4})\s*'
    r'(?:换|→|->|改成|改为)\s*(?P<add>[A-Z]{2,4}\d{3,4})',
    re.IGNORECASE,
)


class CourseAdjusterAgent:
    """课程调整 Agent — 退课/换课细粒度操作。"""

    def run(self, state: AgentState) -> AgentState:
        user_id = state.get("user_id", "")
        phase = state.get("conversation_phase", "")
        user_input = state.get("_raw_input", state.get("user_input", ""))

        # 首次进入 → 展示辅修课表，等待选择
        if phase != ConversationPhase.AWAIT_COURSE_ADJUSTMENT.value:
            return self._entry(state, user_id)

        # 已在 AWAIT_COURSE_ADJUSTMENT → 处理退课/换课指令
        return self._handle_adjustment(state, user_id, user_input)

    def _entry(self, state: AgentState, user_id: str) -> AgentState:
        """首次进入：展示辅修课表并提示操作。"""
        minor_courses = get_timetable(user_id, course_type="minor")

        if not minor_courses:
            state["final_response"] = (
                "📭 你当前没有辅修课程。\n\n"
                "输入「**推荐辅修**」开始规划辅修方案。"
            )
            state["conversation_phase"] = ConversationPhase.IDLE.value
            return state

        lines = ["📋 **当前辅修课程**\n"]
        for c in minor_courses:
            lines.append(
                f"  - `{c.get('course_code', '?')}` "
                f"周{c.get('day_of_week', '?')} "
                f"第{c.get('period_start', '?')}-{c.get('period_end', '?')}节"
            )

        lines.append("")
        lines.append("**可用操作:**")
        lines.append("- `退课 课程代码` — 退选指定课程")
        lines.append("- `换课 旧代码 换 新代码` — 换选课程（自动预检冲突）")
        lines.append("  示例: `退课 FIN201` 或 `换课 FIN201 换 ACC201`")
        lines.append("")
        lines.append("输入「**取消**」退出调整模式。")

        state["final_response"] = "\n".join(lines)
        state["conversation_phase"] = ConversationPhase.AWAIT_COURSE_ADJUSTMENT.value
        return state

    def _handle_adjustment(self, state: AgentState, user_id: str,
                           user_input: str) -> AgentState:
        """处理用户的退课/换课指令。"""
        if user_input.strip() in ("取消", "算了", "不换了", "不退了"):
            state["final_response"] = "👌 已取消课程调整。"
            state["conversation_phase"] = ConversationPhase.IDLE.value
            return state

        # 先尝试匹配换课格式
        swap_match = SWAP_RE.search(user_input)
        if swap_match:
            drop_code = swap_match.group("drop")
            add_code = swap_match.group("add")
            return self._do_swap(state, user_id, drop_code, add_code)

        # 再尝试匹配退课格式
        drop_match = re.search(
            r'(?:退|退课|退选|删除)\s*([A-Z]{2,4}\d{3,4})',
            user_input, re.IGNORECASE,
        )
        if drop_match:
            drop_code = drop_match.group(1)
            return self._do_drop(state, user_id, drop_code)

        # 无法识别
        state["final_response"] = (
            "❓ 无法识别操作。请使用:\n"
            "- `退课 课程代码` 退选课程\n"
            "- `换课 旧代码 换 新代码` 换选课程\n"
            "或输入「**取消**」退出。"
        )
        return state

    def _do_drop(self, state: AgentState, user_id: str, drop_code: str) -> AgentState:
        """执行退课操作。"""
        minor_courses = get_timetable(user_id, course_type="minor")
        target = None
        for c in minor_courses:
            if c.get("course_code") == drop_code:
                target = c
                break

        if not target:
            state["final_response"] = (
                f"❌ 未找到辅修课程 `{drop_code}`。\n\n"
                f"请确认课程代码正确。输入「退课」查看当前辅修列表。"
            )
            return state

        # 执行删除
        delete_timetable_entry(target["id"])

        # 检查剩余辅修课程数 → 如果为 0，清标记
        remaining = get_timetable(user_id, course_type="minor")
        if not remaining:
            user = get_user(user_id)
            if user:
                upsert_user(
                    user_id=user_id,
                    student_name=user.get("student_name", ""),
                    major=user.get("major", ""),
                    has_minor=0,
                    minor_program=None,
                )
            state["has_minor_course"] = False

        state["final_response"] = (
            f"✅ 已退选 `{drop_code}`。\n"
            f"当前辅修剩余 {len(remaining)} 门课程。"
        )
        state["conversation_phase"] = ConversationPhase.IDLE.value
        return state

    def _do_swap(self, state: AgentState, user_id: str,
                 drop_code: str, add_code: str) -> AgentState:
        """执行换课操作（含冲突预检）。"""
        # 检查新课程是否存在
        new_course = get_course(add_code)
        if not new_course:
            state["final_response"] = (
                f"❌ 课程 `{add_code}` 不在数据库中。\n"
                "请确认课程代码正确。"
            )
            state["conversation_phase"] = ConversationPhase.IDLE.value
            return state

        # 冲突预检
        pre_check = validate_schedule(user_id, add_code)
        conflict_warning = ""
        if pre_check.get("has_conflict"):
            conflict_list = pre_check.get("conflicts", [])
            conflict_warning = "\n\n⚠️ **冲突预检警告**:\n"
            for c in conflict_list:
                conflict_warning += (
                    f"  - {c.get('detail', '时间重叠')} "
                    f"({c.get('severity', 'warning')})\n"
                )
            conflict_warning += "\n此操作仍可继续，但可能产生新的时间冲突。"

        # 执行换课
        result = swap_course(user_id, drop_code, add_code)
        dropped = result.get("dropped")
        added = result.get("added")
        warnings = result.get("warnings", [])

        lines = []
        if dropped and added:
            lines.append(f"✅ 已将 `{dropped}` 替换为 `{added}`。")
        elif dropped:
            lines.append(f"✅ 已退选 `{dropped}`。")
            if not added:
                lines.append(f"⚠️ `{add_code}` 添加失败: {'; '.join(warnings)}")
        else:
            lines.append(f"⚠️ 换课部分失败: {'; '.join(warnings)}")

        if conflict_warning:
            lines.append(conflict_warning)

        state["final_response"] = "\n".join(lines)
        state["conversation_phase"] = ConversationPhase.IDLE.value
        return state
