"""
LangGraph AgentState + ConversationPhase 定义。

State 是图执行期间的共享工作内存。每个节点读写 State，
不重复查询 SQLite / Redis（除首次恢复会话外）。
"""

from typing import TypedDict, List, Optional, Annotated
from enum import Enum
import operator


class ConversationPhase(str, Enum):
    """
    对话阶段 —— 控制多轮交互的流转。

    路由层先检查 phase：
      - IDLE → 正常意图分类
      - 其他 → 跳过分类，直接路由到对应 Agent 继续上一轮交互
    """
    IDLE = "idle"
    AWAIT_COURSE_SELECTION = "await_course_selection"      # 等待用户从推荐中选课
    AWAIT_NEW_INTERESTS = "await_new_interests"             # 等待用户输入新兴趣方向（重新推荐）
    AWAIT_PROGRESS_INPUT = "await_progress_input"           # 等待录入成绩/考勤
    AWAIT_DATA_INPUT = "await_data_input"                   # 等待录入课表/编辑个人信息
    AWAIT_CONFLICT_DECISION = "await_conflict_decision"     # 等待用户对冲突处理做决定
    AWAIT_RESOURCE_FEEDBACK = "await_resource_feedback"     # 等待用户反馈资源是否有用
    AWAIT_COURSE_ADJUSTMENT = "await_course_adjustment"     # 等待用户选择退课/换课方案


class AgentState(TypedDict):
    """LangGraph 共享 State —— 每次图执行的临时工作内存。"""

    # ── 身份标识 ──
    user_id: str
    session_id: str

    # ── 路由相关 ──
    intent: str                                   # 意图枚举值（Intent.value）
    conversation_phase: str                       # ConversationPhase.value

    # ── 权限控制（从 SQLite / Redis 加载后缓存于此） ──
    has_minor_course: bool
    has_major_timetable: bool

    # ── 用户输入 ──
    user_input: str                               # 本轮用户输入（清洗后）
    _raw_input: str                               # 本轮用户输入（原始，含符号/emoji）

    # ── 当前请求的中间产出（不含历史全量） ──
    current_recommendations: List[dict]
    current_conflicts: List[dict]
    current_resources: List[dict]

    # ── 对话消息（用 Annotated + operator.add 实现累加） ──
    messages: Annotated[list, operator.add]

    # ── 最终输出 ──
    final_response: str                           # 返回给用户的内容


# ── 创建初始 State 的工厂函数 ──

def create_initial_state(user_id: str, session_id: str,
                         user_input: str,
                         has_minor: bool = False,
                         has_major_timetable: bool = True) -> AgentState:
    """创建一个新的初始 AgentState。"""
    return AgentState(
        user_id=user_id,
        session_id=session_id,
        intent="",
        conversation_phase=ConversationPhase.IDLE.value,
        has_minor_course=has_minor,
        has_major_timetable=has_major_timetable,
        user_input=user_input,
        _raw_input=user_input,
        current_recommendations=[],
        current_conflicts=[],
        current_resources=[],
        messages=[],
        final_response="",
    )
