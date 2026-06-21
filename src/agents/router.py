"""
路由 Agent。

职责：
    1. 检查 conversation_phase → 如在等待状态，跳过意图分类
    2. 用户输入预处理（去标点、表情、多余空格、统一小写）
    3. 空输入判断
    4. 关键词匹配 → 基于词权重评分，而非“先到先得”
    5. LLM 精判 → 关键词未命中时，对所有规则计分取最高
    6. 返回目标节点名称

意图枚举：
    plan_course      — 辅修课程规划与推荐
    track_progress   — 查询/录入学习进度
    check_conflict   — 检测时间冲突（需经过 conflict_gate）
    recommend_res    — 推荐学习资源
    gen_report       — 生成学业报告
    data_manage      — 数据管理（录入课表、修改信息）
    course_adjust    — 课程调整（退课、换课）
    chat_qa          — 闲聊/学科问答
"""

import logging
import re
from typing import Optional, List, Tuple

from src.graph.state import AgentState, ConversationPhase

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════
#  关键词 → 意图映射表（每个意图附权重，词越长权重越大）
# ══════════════════════════════════════════════════════

KEYWORD_INTENT_MAP: List[Tuple[List[str], str]] = [
    (["推荐辅修", "选辅修", "规划课程", "辅修推荐", "辅修方案",
      "辅修专业", "有什么辅修", "我能辅修", "辅修方向", "辅修计划",
      "想辅修", "想选辅修", "辅修什么", "选什么辅修"], "plan_course"),
    (["考勤", "成绩", "作业", "查看进度", "进度查询",
      "录入成绩", "录入考勤", "学习进度", "提交作业", "出勤",
      "进度", "查看", "成绩查询"], "track_progress"),
    (["调课", "调整课程", "换时间", "改时间"], "adjust_schedule"),
    (["时间冲突", "撞课", "课表冲突", "考试冲突", "检测冲突",
      "冲突检测", "课程冲突", "时间重叠", "时间撞了", "冲突"], "check_conflict"),
    (["资料", "习题", "课件", "学习资源", "网课", "推荐资源",
      "找资料", "复习资料", "学习笔记", "公开课", "讲义"], "recommend_res"),
    (["生成报告", "总结情况", "学业报告", "出报告", "分析报告",
      "综合报告", "学习报告"], "gen_report"),
    (["修改课表", "录入课表", "录入", "添加课程", "更新信息",
      "修改专业", "设置专业", "我的信息", "课表管理",
      "我的课表", "查看课表", "删除课程", "查看课程"], "data_manage"),
    (["退课", "换课", "退选", "调整课程", "取消辅修", "改选"], "course_adjust"),
]

# ══════════════════════════════════════════════════════
#  LLM stub 正则规则表（每条规则有 regex + 权重 + 意图）
# ══════════════════════════════════════════════════════

STUB_RULES: List[Tuple[str, int, str]] = [
    # (regex_pattern, weight, intent)
    # 权重：更高的词代表更明确的意图信号
    (r"辅修|选课|第二专业|辅修方向", 3, "plan_course"),
    (r"规划|推荐|方案|有什么.*辅修|建议", 1, "plan_course"),

    (r"调课|调整课程|换时间|改时间|课程调整", 5, "adjust_schedule"),

    (r"冲突|撞课|撞了|互斥", 4, "check_conflict"),
    (r"时间.*重叠|同时.*上课|排得开|排不开", 3, "check_conflict"),
    (r"课太多|太多了|忙不过来|来不及", 2, "check_conflict"),

    (r"成绩|分数|考勤|出勤|旷课|迟到", 3, "track_progress"),
    (r"作业|提交|进度|学到.*哪|打卡", 2, "track_progress"),

    (r"资料|资源|课件|讲义|习题", 3, "recommend_res"),
    (r"网课|公开课|视频|笔记|复习", 2, "recommend_res"),
    (r"推荐.*资料|找.*资料|推荐.*资源|搜.*资源", 2, "recommend_res"),

    (r"报告|总结|分析|综合.*报告", 3, "gen_report"),
    (r"整体情况|学业.*情况|帮我看看", 2, "gen_report"),

    (r"退课|退选|换课|改选|取消", 4, "course_adjust"),

    (r"录入.*课表|修改.*课表|添加.*课表|我的信息", 4, "data_manage"),
    (r"录入|修改|添加|更新|设置", 1, "data_manage"),
]


# ══════════════════════════════════════════════════════
#  阶段 → 目标节点映射表
# ══════════════════════════════════════════════════════

PHASE_NODE_MAP = {
    ConversationPhase.AWAIT_COURSE_SELECTION.value:     "course_planner_agent",
    ConversationPhase.AWAIT_NEW_INTERESTS.value:         "course_planner_agent",
    ConversationPhase.AWAIT_PROGRESS_INPUT.value:        "progress_tracker_agent",
    ConversationPhase.AWAIT_DATA_INPUT.value:             "data_manager_agent",
    ConversationPhase.AWAIT_CONFLICT_DECISION.value:     "conflict_checker_agent",
    ConversationPhase.AWAIT_RESOURCE_FEEDBACK.value:     "resource_recommender_agent",
    ConversationPhase.AWAIT_COURSE_ADJUSTMENT.value:     "course_adjuster_agent",
}

# ══════════════════════════════════════════════════════
#  意图 → 目标节点映射表
# ══════════════════════════════════════════════════════

INTENT_NODE_MAP = {
    "plan_course":     "course_planner_agent",
    "track_progress":  "progress_tracker_agent",
    "adjust_schedule": "conflict_checker_agent",  # 调课直通（跳过 gate）
    "check_conflict":  "conflict_gate",           # 需要先经过权限门
    "recommend_res":   "resource_recommender_agent",
    "gen_report":      "report_generator_agent",
    "data_manage":     "data_manager_agent",
    "course_adjust":   "course_adjuster_agent",
    "chat_qa":         "chat_qa_handler",
}

# 提前计算反向映射
_INTENT_NODE_REVERSE = {v: k for k, v in INTENT_NODE_MAP.items()}

# Phase 路由使用的目标节点在 INTENT_NODE_MAP 中可能对应不同的 key
# （如 conflict_checker_agent 的上游是 conflict_gate），需补充映射：
_INTENT_NODE_REVERSE.update({
    "conflict_checker_agent": "check_conflict",
})

# ══════════════════════════════════════════════════════
#  文本预处理
# ══════════════════════════════════════════════════════

# 保留：中文字符 + 英文字母 + 数字 + 空白符
# 去除：标点、符号、emoji 等一切非核心文本字符
_KEEP_PATTERN = re.compile(
    r'[^一-鿿㐀-䶿'   # 中日韩统一表意文字 + 扩展A
    r'a-zA-Z0-9'                        # ASCII 字母 + 数字
    r'\s'                               # 空白
    r']+'
)

# 多余空白
_MULTISPACE_PATTERN = re.compile(r'\s{2,}')


def _preprocess_input(text: str) -> str:
    """
    文本预处理：

    1. strip 首尾空白
    2. 转小写（英文部分）
    3. 去除所有标点、emoji、符号（只保留中文+英文+数字+空白）
    4. 合并多余空格
    5. 再次 strip
    """
    if not text:
        return ""

    text = text.strip()
    if not text:
        return ""

    text = text.lower()
    text = _KEEP_PATTERN.sub(' ', text)
    text = _MULTISPACE_PATTERN.sub(' ', text)
    return text.strip()


def _is_empty_input(text: str) -> bool:
    """判断经预处理后的输入是否为纯空。"""
    return not text or not text.strip()


# ══════════════════════════════════════════════════════
#  路由主函数
# ══════════════════════════════════════════════════════

def route(state: AgentState) -> str:
    """
    路由主入口。

    返回 LangGraph 图中的目标节点名称。
    """
    phase = state.get("conversation_phase", ConversationPhase.IDLE.value)
    raw_input = state.get("user_input", "")

    # 保存原始输入（清洗后符号/emoji 会丢失，下游节点可能需要）
    state["_raw_input"] = raw_input

    # ── 第 0 步：空输入判断 ──
    clean_input = _preprocess_input(raw_input)
    if _is_empty_input(clean_input):
        state["intent"] = "chat_qa"
        state["final_response"] = "🤔 你好像没输入内容？请再说一次吧～"
        logger.info("Empty input → chat_qa")
        return "chat_qa_handler"

    # 将清洗后的文本回存 state，后续节点可直接使用
    state["user_input"] = clean_input

    # ── 第 1 步：阶段检查（处于等待状态 → 跳过意图分类）──
    if phase != ConversationPhase.IDLE.value and phase in PHASE_NODE_MAP:
        node_name = PHASE_NODE_MAP[phase]
        state["intent"] = _INTENT_NODE_REVERSE.get(node_name, "chat_qa")
        logger.info(f"Phase routing: {phase} → {node_name}")
        return node_name

    # ── 第 2 步：关键词评分匹配 ──
    intent, score = keyword_match_scored(clean_input)
    if intent and score > 0:
        logger.info(f"Keyword match: '{clean_input[:40]}' → {intent} (score={score})")
        state["intent"] = intent
        # 调课指令：空闲态直达冲突决策分支，省去重复检测
        if intent == "adjust_schedule":
            state["conversation_phase"] = ConversationPhase.AWAIT_CONFLICT_DECISION.value
        return INTENT_NODE_MAP[intent]

    # ── 第 3 步：LLM stub 多规则计分 ──
    intent, score = llm_classify_scored(clean_input)
    logger.info(f"LLM classify: '{clean_input[:40]}' → {intent} (score={score})")
    state["intent"] = intent
    if intent == "adjust_schedule":
        state["conversation_phase"] = ConversationPhase.AWAIT_CONFLICT_DECISION.value
    return INTENT_NODE_MAP.get(intent, "chat_qa_handler")


# ══════════════════════════════════════════════════════
#  关键词评分匹配（替代旧 keyword_match）
# ══════════════════════════════════════════════════════

def keyword_match_scored(text: str) -> Tuple[Optional[str], int]:
    """
    对每个意图计算匹配得分。

    规则：
      - 每个匹配到的关键词贡献 「关键词长度」的分数
      - 最终选得分最高的意图
      - 得分相同时，选单次匹配权重更高的（即被更长的词命中）
      - 完全没命中 → 返回 (None, 0)
    """
    best_intent: Optional[str] = None
    best_score: float = 0
    best_max_len: int = 0  # 该意图中最长匹配词的长度

    for keywords, intent in KEYWORD_INTENT_MAP:
        intent_score = 0
        max_len = 0
        for kw in keywords:
            if kw in text:
                intent_score += len(kw)      # 关键词越长权重越大
                if len(kw) > max_len:
                    max_len = len(kw)

        if intent_score > best_score:
            best_score = intent_score
            best_intent = intent
            best_max_len = max_len
        elif intent_score == best_score and intent_score > 0:
            # 平局：比较最长匹配词长度（更具体的词优先）
            if max_len > best_max_len:
                best_intent = intent
                best_max_len = max_len

    return (best_intent, best_score) if best_intent else (None, 0)


# 保留旧接口兼容（不推荐使用）
def keyword_match(text: str) -> Optional[str]:
    """旧版兼容接口，内部使用评分版本。"""
    intent, _ = keyword_match_scored(text)
    return intent


# ══════════════════════════════════════════════════════
#  LLM stub 多规则计分（替代旧 llm_classify_stub）
# ══════════════════════════════════════════════════════

def llm_classify_scored(text: str) -> Tuple[str, int]:
    """
    对所有正则规则计分，返回最高分意图。

    每条规则：
      - 匹配 1 次 → 加 weight * 1
      - 多次匹配 → 累加（一次匹配多个词时更有信号）
    """
    scores: dict[str, float] = {}

    for pattern, weight, intent in STUB_RULES:
        matches = re.findall(pattern, text)
        if matches:
            # 每次命中贡献权重，多次命中可累加
            scores[intent] = scores.get(intent, 0) + weight * len(matches)

    if not scores:
        return ("chat_qa", 0)

    # 选最高分；同分时比较「最高单条权重」
    best_intent = max(scores, key=lambda k: (scores[k], max(
        (w for p, w, i in STUB_RULES if i == k and re.search(p, text)),
        default=0
    )))

    return (best_intent, scores[best_intent])


def llm_classify_stub(text: str, state: AgentState) -> str:
    """旧版兼容接口。"""
    intent, _ = llm_classify_scored(text)
    return intent
