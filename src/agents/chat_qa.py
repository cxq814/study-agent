"""
兜底问答 Handler。

职责（系统的"最后一道防线"）：
    1. 学科问题 → RAG 检索知识库 + LLM 生成回答（模板拼接回退）
    2. 闲聊 → 友好预设回复 + 功能引导
    3. 完全无匹配 → 提示可用功能

无多轮状态 — 每次调用独立完成，phase 回到 IDLE。
"""

import logging
import re
import random

from src.graph.state import AgentState, ConversationPhase
from src.tools.llm_tools import llm_call, llm_chat, llm_is_available
from src.tools.rag_tools import RAGToolset

logger = logging.getLogger(__name__)

# 学科关键词（触发 RAG 检索）
ACADEMIC_KEYWORDS_RE = re.compile(
    r'什么是|解释|为什么|怎么做|如何|定义|概念|原理|公式|定理|'
    r'算法|模型|金融|会计|经济|法律|管理|统计|编程|数据|'
    r'CAPM|NPV|IRR|ROI|GDP|CPI|WACC|DCF|'
    r'公司法|税法|劳动法|合同法|刑法|宪法|'
    r'资产负债|现金流|利润|成本|收益|风险|投资|'
    r'机器学习|深度学习|神经网络|数据库|操作系统',
    re.IGNORECASE,
)

# 闲聊回退模板
CASUAL_RESPONSES = [
    "你好！我是学业规划助手，可以帮你规划辅修、检测冲突、推荐学习资源。\n\n试试输入「**推荐辅修**」开始吧～",
    "欢迎使用学业规划系统！\n\n我可以帮你:\n- 📋 规划辅修方案\n- 📊 跟踪学习进度\n- 🔍 检测时间冲突\n- 📚 推荐学习资源\n- 📝 生成学业报告",
    "很高兴为您服务！请问需要什么帮助？\n\n你可以直接告诉我你的需求，比如「我想选一个辅修」。",
]


class ChatQAHandler:
    """兜底问答 Handler。"""

    def run(self, state: AgentState) -> AgentState:
        user_input = state.get("user_input", "")

        # 空输入
        if not user_input.strip():
            state["final_response"] = "🤔 你好像没输入内容？请再说一次吧～"
            state["conversation_phase"] = ConversationPhase.IDLE.value
            return state

        # 判断是否是学科问题
        if self._is_academic_question(user_input):
            response = self._answer_academic(user_input)
        else:
            response = self._answer_casual(user_input)

        state["final_response"] = response
        state["conversation_phase"] = ConversationPhase.IDLE.value
        return state

    def _is_academic_question(self, text: str) -> bool:
        """判断输入是否为学科相关问题。"""
        return bool(ACADEMIC_KEYWORDS_RE.search(text))

    def _answer_academic(self, question: str) -> str:
        """学科问题：RAG 检索 + LLM/模板 生成回答。"""
        try:
            # RAG 检索相关知识
            rag = RAGToolset()
            docs = rag.search_knowledge(question, top_k=3)

            if not docs:
                return (
                    f"关于「{question[:50]}」，知识库中暂时没有找到相关资料。\n\n"
                    "建议:\n"
                    "- 查阅相关教材或课件\n"
                    "- 向授课老师或助教请教\n"
                    "- 在课程群中与同学讨论"
                )

            # 拼接上下文
            context_parts = []
            refs = []
            for d in docs:
                content = d.get("content", d.get("text", ""))[:500]
                source = d.get("source", d.get("metadata", {}).get("source", ""))
                context_parts.append(content)
                if source:
                    filename = source.split("/")[-1].replace(".md", "")
                    refs.append(f"- 📄 {filename}")

            context = "\n---\n".join(context_parts)
            ref_text = "\n\n**参考来源:**\n" + "\n".join(refs[:3])

            # 尝试 LLM 回答
            if llm_is_available():
                prompt = (
                    f"请根据以下参考资料回答学生的问题。"
                    f"如果参考资料不足以回答，请诚实说明并给出建议。\n\n"
                    f"## 学生问题\n{question}\n\n"
                    f"## 参考资料\n{context}"
                )

                def fallback_template():
                    return (
                        f"📚 **关于「{question[:40]}」**\n\n"
                        f"根据学习资料库中的信息：\n\n"
                        f"{context[:1200]}\n\n"
                        f"---\n上述信息仅供参考。如需更详细的信息，"
                        f"建议查阅完整课件或教材。"
                        f"{ref_text}"
                    )

                answer = llm_call(
                    prompt=prompt,
                    system="你是一位耐心的大学学业辅导助手。请用简洁的中文回答，引用参考资料中的知识点。",
                    fallback=fallback_template,
                    max_tokens=1500,
                )
                return answer if answer else fallback_template()

            # 模板拼接（无 LLM）
            return (
                f"📚 **关于「{question[:40]}」**\n\n"
                f"根据学习资料库中的信息：\n\n"
                f"{context[:1200]}\n\n"
                f"---\n上述信息仅供参考。如需更详细的信息，"
                f"建议查阅完整课件或教材。"
                f"{ref_text}"
            )

        except Exception as e:
            logger.error("Chat QA academic answer failed: %s", e)
            return (
                f"抱歉，检索知识库时出现错误。\n\n"
                f"你询问的是「{question[:40]}」，建议查阅相关教材或咨询授课老师。"
            )

    def _answer_casual(self, text: str) -> str:
        """闲聊/问候：LLM 优先 → 模板回退。"""
        text_lower = text.lower().strip()

        # 意图标签
        intent_label = "general"
        if any(kw in text_lower for kw in ["你好", "hi", "hello", "嗨", "在吗", "在不在"]):
            intent_label = "greeting"
        elif any(kw in text_lower for kw in ["谢谢", "感谢", "多谢", "thanks", "thank"]):
            intent_label = "thanks"
        elif any(kw in text_lower for kw in ["再见", "拜拜", "bye", "88"]):
            intent_label = "bye"
        elif any(kw in text_lower for kw in ["帮助", "help", "功能", "能做什么", "怎么用"]):
            intent_label = "help"

        # LLM 可用时：调用闲聊模型
        if llm_is_available():
            context = {
                "greeting": "学生正在打招呼，请友好地介绍你能帮他做什么：辅修规划、进度跟踪、冲突检测、资源推荐、报告生成、退课换课。",
                "thanks": "学生表示感谢，请简短友好地回应。",
                "bye": "学生说再见，请送上简短祝福。",
                "help": "学生需要帮助，请列出你可以做的所有功能。",
                "general": "学生说了些日常闲聊，请友好回应并引导到学习相关功能。",
            }
            prompt = f"学生说：「{text}」\n\n{context.get(intent_label, context['general'])}"

            def fallback():
                return self._template_reply(intent_label)
            return llm_chat(prompt, fallback=fallback)

        # LLM 不可用：模板
        return self._template_reply(intent_label)

    def _template_reply(self, intent: str) -> str:
        """闲聊模板回退。"""
        if intent == "greeting":
            return (
                "你好！👋 我是学业规划助手。\n\n"
                "我可以帮你:\n"
                "- 📋 **推荐辅修** — 根据你的兴趣推荐辅修方案\n"
                "- 📊 **查看进度** — 跟踪学习进度和成绩趋势\n"
                "- 🔍 **检测冲突** — 检查主修/辅修课表时间冲突\n"
                "- 📚 **推荐资源** — 查找课程相关的学习资料\n"
                "- 📝 **生成报告** — 生成综合学业报告\n"
                "- 🗑️ **退课/换课** — 调整辅修课程\n\n"
                "直接说出你的需求，我会尽力帮你！"
            )
        if intent == "thanks":
            return "不客气！有问题随时找我～ 😊"
        if intent == "bye":
            return "再见！祝你学业顺利～ 🎓"
        if intent == "help":
            return (
                "🎓 **学业规划助手 — 功能列表**\n\n"
                "| 功能 | 示例指令 |\n"
                "|------|---------|\n"
                "| 推荐辅修 | `推荐辅修` |\n"
                "| 查看进度 | `查看进度` |\n"
                "| 检测冲突 | `检测冲突` |\n"
                "| 推荐资源 | `推荐资源` |\n"
                "| 生成报告 | `生成报告` |\n"
                "| 我的课表 | `我的课表` |\n"
                "| 退课/换课 | `退课` 或 `换课` |\n"
                "| 录入课表 | `录入课表` |\n\n"
                "直接输入指令即可使用对应功能。"
            )
        return random.choice(CASUAL_RESPONSES)
