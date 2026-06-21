"""
LangGraph 状态图编排。

Phase 4：全部 10 个 Agent 均已实现真实逻辑，无 Echo Stub。
"""

import logging
from typing import Dict, Any

from langgraph.graph import StateGraph, END

from src.graph.state import AgentState, ConversationPhase

logger = logging.getLogger(__name__)

# Lazy import to break circular dependency:
#   router.py → src.graph.state (fine)
#   workflow.py → router.py would create cycle via src.agents.__init__
#   so we import inside the functions that need router.
_INTENT_NODE_MAP = None
_route_func = None
_course_planner = None
_progress_tracker = None
_conflict_checker = None
_resource_recommender = None
_report_generator = None
_data_manager = None
_course_adjuster = None
_chat_qa = None

def _get_intent_node_map() -> dict:
    global _INTENT_NODE_MAP
    if _INTENT_NODE_MAP is None:
        from src.agents.router import INTENT_NODE_MAP as m
        _INTENT_NODE_MAP = m
    return _INTENT_NODE_MAP

def _get_route_func():
    global _route_func
    if _route_func is None:
        from src.agents.router import route as r
        _route_func = r
    return _route_func

def _get_course_planner():
    global _course_planner
    if _course_planner is None:
        from src.agents.course_planner import CoursePlannerAgent
        _course_planner = CoursePlannerAgent()
    return _course_planner

def _get_progress_tracker():
    global _progress_tracker
    if _progress_tracker is None:
        from src.agents.progress_tracker import ProgressTrackerAgent
        _progress_tracker = ProgressTrackerAgent()
    return _progress_tracker

def _get_conflict_checker():
    global _conflict_checker
    if _conflict_checker is None:
        from src.agents.conflict_checker import ConflictCheckerAgent
        _conflict_checker = ConflictCheckerAgent()
    return _conflict_checker

def _get_resource_recommender():
    global _resource_recommender
    if _resource_recommender is None:
        from src.agents.resource_recommender import ResourceRecommenderAgent
        _resource_recommender = ResourceRecommenderAgent()
    return _resource_recommender

def _get_report_generator():
    global _report_generator
    if _report_generator is None:
        from src.agents.report_generator import ReportGeneratorAgent
        _report_generator = ReportGeneratorAgent()
    return _report_generator

def _get_data_manager():
    global _data_manager
    if _data_manager is None:
        from src.agents.data_manager import DataManagerAgent
        _data_manager = DataManagerAgent()
    return _data_manager

def _get_course_adjuster():
    global _course_adjuster
    if _course_adjuster is None:
        from src.agents.course_adjuster import CourseAdjusterAgent
        _course_adjuster = CourseAdjusterAgent()
    return _course_adjuster

def _get_chat_qa():
    global _chat_qa
    if _chat_qa is None:
        from src.agents.chat_qa import ChatQAHandler
        _chat_qa = ChatQAHandler()
    return _chat_qa


# ══════════════════════════════════════════════════════
#  路由节点
# ══════════════════════════════════════════════════════

def router_node(state: AgentState) -> AgentState:
    """
    路由节点：调用路由逻辑，将 intent 写入 state。
    实际的边条件由 conditional_edges 根据 state["intent"] 分发。
    """
    # route() 会设置 state["intent"]
    _get_route_func()(state)
    logger.info(f"Router: intent={state.get('intent')}, phase={state.get('conversation_phase')}")
    return state


# ══════════════════════════════════════════════════════
#  权限门：冲突检测准入检查
# ══════════════════════════════════════════════════════

def conflict_gate(state: AgentState) -> AgentState:
    """
    冲突检测的准入条件检查（纯规则 + SQLite 验证，不调用 LLM）。

    三层校验：
      1. 是否已录入主修课表
      2. 是否已选辅修课程
      3. SQLite 数据完整性验证（防止 Redis 状态滞后）
    """
    user_id = state.get("user_id", "")

    if not state.get("has_major_timetable"):
        state["final_response"] = (
            "⚠️ 您还未录入主修课表，无法进行冲突检测。\n\n"
            "请先录入主修课程信息（课程名 + 上课时间 + 考试时间），\n"
            "输入「录入课表」开始。"
        )
        state["intent"] = "blocked"
        return state

    if not state.get("has_minor_course"):
        state["final_response"] = (
            "⚠️ 您暂未选择辅修课程，无需进行冲突检测。\n\n"
            "输入「推荐辅修」让我帮您规划辅修方案，\n"
            "选定辅修后即可检测时间冲突。"
        )
        state["intent"] = "blocked"
        return state

    # ── 第三层：SQLite 数据完整性验证 ──
    try:
        from src.storage.sqlite_client import get_timetable

        major_entries = get_timetable(user_id, course_type="major")
        minor_entries = get_timetable(user_id, course_type="minor")

        if not major_entries:
            state["final_response"] = (
                "⚠️ 主修课表为空。\n\n"
                "系统中标记您已录入主修课表，但未找到具体课程数据。\n"
                "请重新「录入课表」添加主修课程信息。"
            )
            state["intent"] = "blocked"
            return state

        if not minor_entries:
            state["final_response"] = (
                "⚠️ 辅修课表为空。\n\n"
                "系统中标记您已选择辅修，但未找到辅修课程数据。\n"
                "请输入「推荐辅修」重新规划辅修方案。"
            )
            state["intent"] = "blocked"
            return state

        logger.info(
            f"Conflict gate passed: {len(major_entries)} major + "
            f"{len(minor_entries)} minor courses ready for detection"
        )
    except Exception as e:
        logger.warning(f"Conflict gate SQLite verification failed: {e}, allowing pass-through")

    # 通过准入检查
    return state


# ══════════════════════════════════════════════════════
#  业务 Agent 节点
# ══════════════════════════════════════════════════════

# ── Phase 2 真实 Agent ──

def course_planner_node(state: AgentState) -> AgentState:
    """课程规划 Agent（真实实现）。"""
    return _get_course_planner().run(state)


def progress_tracker_node(state: AgentState) -> AgentState:
    """进度跟踪 Agent（真实实现）。"""
    return _get_progress_tracker().run(state)


# ── Phase 3 真实 Agent ──

def conflict_checker_node(state: AgentState) -> AgentState:
    """冲突检测 Agent（真实实现）。"""
    return _get_conflict_checker().run(state)


def resource_recommender_node(state: AgentState) -> AgentState:
    """资源推荐 Agent（真实实现）。"""
    return _get_resource_recommender().run(state)


# ── Phase 4 真实 Agent ──

def report_generator_node(state: AgentState) -> AgentState:
    """报告生成 Agent（真实实现）。"""
    return _get_report_generator().run(state)


def data_manager_node(state: AgentState) -> AgentState:
    """数据管理 Agent（真实实现）。"""
    return _get_data_manager().run(state)


def course_adjuster_node(state: AgentState) -> AgentState:
    """课程调整 Agent（真实实现）。"""
    return _get_course_adjuster().run(state)


def chat_qa_node(state: AgentState) -> AgentState:
    """兜底问答 Handler（真实实现）。"""
    return _get_chat_qa().run(state)


# ══════════════════════════════════════════════════════
#  构建图
# ══════════════════════════════════════════════════════

def _build_node_map() -> Dict[str, Any]:
    """构建所有节点的名称 → 函数映射。"""
    nodes = {}

    # 路由
    nodes["router"] = router_node

    # 权限门
    nodes["conflict_gate"] = conflict_gate

    # Phase 2：真实业务 Agent
    nodes["course_planner_agent"] = course_planner_node
    nodes["progress_tracker_agent"] = progress_tracker_node

    # Phase 3：冲突检测 + 资源推荐
    nodes["conflict_checker_agent"] = conflict_checker_node
    nodes["resource_recommender_agent"] = resource_recommender_node

    # Phase 4：报告生成 + 数据管理 + 课程调整 + 兜底问答
    nodes["report_generator_agent"] = report_generator_node
    nodes["data_manager_agent"] = data_manager_node
    nodes["course_adjuster_agent"] = course_adjuster_node
    nodes["chat_qa_handler"] = chat_qa_node

    return nodes


def build_workflow() -> StateGraph:
    """构建并编译 LangGraph 状态图。"""

    wf = StateGraph(AgentState)
    node_map = _build_node_map()

    # ── 注册所有节点 ──
    for name, func in node_map.items():
        wf.add_node(name, func)

    # ── 入口 ──
    wf.set_entry_point("router")

    # ── 路由分发 ──
    # 从 router 出发，根据 state["intent"] 分发；若被 conflict_gate 拦截则直接 END
    def router_to_node(state: AgentState) -> str:
        intent = state.get("intent", "chat_qa")
        if intent == "blocked":
            return END
        return _get_intent_node_map().get(intent, "chat_qa_handler")

    node_map_values = _get_intent_node_map()
    wf.add_conditional_edges("router", router_to_node, {
        k: k for k in node_map_values.values()
    } | {END: END})

    # ── 权限门分发 ──
    def gate_to_node(state: AgentState) -> str:
        if state.get("intent") == "blocked":
            return END
        return "conflict_checker_agent"

    wf.add_conditional_edges("conflict_gate", gate_to_node, {
        "conflict_checker_agent": "conflict_checker_agent",
        END: END,
    })

    # ── 所有终端节点 → END ──
    for name in node_map:
        if name not in ("router", "conflict_gate"):
            wf.add_edge(name, END)

    # ── 编译 ──
    compiled = wf.compile()
    logger.info(f"Workflow compiled with {len(node_map)} nodes")
    return compiled


# ── 单例 ──

_app = None


def get_app() -> StateGraph:
    """获取编译后的 LangGraph app（懒加载单例）。"""
    global _app
    if _app is None:
        _app = build_workflow()
    return _app


def reset_app():
    """重置所有懒加载单例 + 清除模块缓存（Streamlit 热重载时调用）。"""
    global _app, _INTENT_NODE_MAP, _route_func
    global _course_planner, _progress_tracker, _conflict_checker, _resource_recommender
    global _report_generator, _data_manager, _course_adjuster, _chat_qa
    _app = None
    _INTENT_NODE_MAP = None
    _route_func = None
    _course_planner = None
    _progress_tracker = None
    _conflict_checker = None
    _resource_recommender = None
    _report_generator = None
    _data_manager = None
    _course_adjuster = None
    _chat_qa = None
    # 清除 sys.modules 中的项目模块缓存
    import sys
    for mod in list(sys.modules.keys()):
        if mod.startswith("src."):
            del sys.modules[mod]
