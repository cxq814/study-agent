"""
LangGraph 状态管理工具 — AgentState 读写快捷方法。

调用方：全流程节点。
所有函数直接操作 state dict，返回修改后的 state（原地修改 + 返回）。
"""

import logging
from typing import List

from src.graph.state import AgentState, ConversationPhase

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
#  Intent / Phase
# ══════════════════════════════════════════════════════

def set_intent(state: AgentState, intent: str) -> AgentState:
    """设置当前意图并返回 state。"""
    if not intent:
        return state
    state["intent"] = intent
    return state


def set_phase(state: AgentState, phase: ConversationPhase) -> AgentState:
    """设置对话阶段并返回 state。"""
    if not phase:
        return state
    state["conversation_phase"] = phase.value if hasattr(phase, "value") else str(phase)
    return state


def get_phase(state: AgentState) -> str:
    """读取当前对话阶段字符串。"""
    return state.get("conversation_phase", ConversationPhase.IDLE.value)


def is_idle(state: AgentState) -> bool:
    """判断当前是否为空闲态。"""
    return get_phase(state) == ConversationPhase.IDLE.value


# ══════════════════════════════════════════════════════
#  Response
# ══════════════════════════════════════════════════════

def set_final_response(state: AgentState, response: str) -> AgentState:
    """设置最终回复文本并返回 state。"""
    if response is None:
        return state
    state["final_response"] = str(response)
    return state


def append_to_response(state: AgentState, text: str) -> AgentState:
    """在已有回复末尾追加文本。"""
    if not text:
        return state
    current = state.get("final_response", "")
    state["final_response"] = current + str(text)
    return state


# ══════════════════════════════════════════════════════
#  Messages
# ══════════════════════════════════════════════════════

def append_message(state: AgentState, role: str, content: str) -> AgentState:
    """追加一条对话消息到 state.messages。"""
    if not role or not content:
        return state
    state.setdefault("messages", []).append({"role": role, "content": content})
    return state


# ══════════════════════════════════════════════════════
#  Intermediate Results
# ══════════════════════════════════════════════════════

def set_recommendations(state: AgentState, recs: List[dict]) -> AgentState:
    """暂存推荐结果。"""
    if not isinstance(recs, list):
        return state
    state["current_recommendations"] = recs
    return state


def set_conflicts(state: AgentState, conflicts: List[dict]) -> AgentState:
    """暂存冲突结果。"""
    if not isinstance(conflicts, list):
        return state
    state["current_conflicts"] = conflicts
    return state


def set_resources(state: AgentState, resources: List[dict]) -> AgentState:
    """暂存资源推荐结果。"""
    if not isinstance(resources, list):
        return state
    state["current_resources"] = resources
    return state


def clear_intermediate_results(state: AgentState) -> AgentState:
    """清空所有中间结果（推荐/冲突/资源），不改变 phase 和 intent。"""
    state["current_recommendations"] = []
    state["current_conflicts"] = []
    state["current_resources"] = []
    return state


# ══════════════════════════════════════════════════════
#  Flags
# ══════════════════════════════════════════════════════

def set_has_minor(state: AgentState, value: bool) -> AgentState:
    """设置辅修状态标记。"""
    state["has_minor_course"] = bool(value)
    return state


def set_has_major_timetable(state: AgentState, value: bool) -> AgentState:
    """设置主修课表标记。"""
    state["has_major_timetable"] = bool(value)
    return state
