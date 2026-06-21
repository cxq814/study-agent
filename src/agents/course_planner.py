"""
课程规划 Agent。

职责：
  1. 接收用户输入 → 检索知识库 → 生成个性化辅修推荐清单
  2. 处理用户选择 → 写入课表 → 更新辅修状态
  3. 多轮对话：推荐后等待用户确认选择

依赖：
  - SQLite: 用户档案、课程数据、课表
  - ChromaDB: 知识库 RAG 检索
  - Redis: 会话状态（推荐暂存、phase 控制）
"""

import json
import logging
from typing import List, Dict, Optional

from src.graph.state import AgentState, ConversationPhase
from src.storage.sqlite_client import (
    get_user, upsert_user, list_courses, list_all_courses,
    add_timetable_entry, get_timetable,
)
from src.storage.redis_client import save_session_state
from src.rag.retriever import Retriever

logger = logging.getLogger(__name__)


from src.tools.llm_tools import llm_call as _llm

# ══════════════════════════════════════════════════════
#  课程规划 Agent
# ══════════════════════════════════════════════════════

class CoursePlannerAgent:
    """课程规划 Agent。"""

    def __init__(self):
        self.retriever = Retriever()

    def run(self, state: AgentState) -> AgentState:
        """
        主入口：根据 conversation_phase 分发到推荐或确认流程。
        """
        phase = state.get("conversation_phase", "")

        if phase == ConversationPhase.AWAIT_NEW_INTERESTS.value:
            return self._handle_new_interests(state)
        elif phase == ConversationPhase.AWAIT_COURSE_SELECTION.value:
            return self._handle_selection(state)
        else:
            return self._handle_recommendation(state)

    # ── 推荐流程 ──────────────────────────────────────

    def _handle_recommendation(self, state: AgentState) -> AgentState:
        """
        处理辅修推荐请求。

        流程：
          1. 读取用户档案（主修、兴趣、空闲时间）
          2. 从 SQLite 获取已有主修课表（用于过滤冲突时段）
          3. 检索知识库：
             a. 辅修专业简介（programs/）
             b. 候选课程详情（courses/）
             c. 辅修规则（rules/）
          4. LLM 整合生成推荐清单
          5. 结果暂存 Redis，phase → AWAIT_COURSE_SELECTION
        """
        user_id = state["user_id"]
        user_input = state.get("user_input", "")
        logger.info(f"CoursePlanner: 推荐请求 user={user_id} input='{user_input[:50]}'")

        # 1. 读取用户档案
        user = get_user(user_id)
        if not user:
            state["final_response"] = "⚠️ 未找到您的用户信息，请先录入个人资料。"
            return state

        # ── 已选辅修：直接展示现有数据，不再生成推荐 ──
        if user.get("has_minor") == 1 and user.get("minor_program"):
            return self._show_existing_minor(state, user)

        interests = user.get("interests", "[]")
        try:
            interests_list = json.loads(interests) if isinstance(interests, str) else interests
        except json.JSONDecodeError:
            interests_list = []

        # 2. 获取主修课表（用于后续冲突预检测——Phase 3 完善）
        major_timetable = get_timetable(user_id, course_type="major")

        # 3. RAG 检索
        # 3a. 检索匹配的辅修专业
        interest_query = " ".join(interests_list) if interests_list else user_input
        program_results = self.retriever.search_programs(interest_query, top_k=3)

        # 3b. 检索相关课程
        course_results = self.retriever.search_courses(interest_query, top_k=12)

        # 3c. 检索辅修规则
        rule_results = self.retriever.search_rules("辅修报名条件 选课限制", top_k=3)

        # 4. 构建推荐（LLM 或模板）
        recommendations = self._build_recommendations(
            user=user,
            interests=interests_list,
            user_input=user_input,
            program_results=program_results,
            course_results=course_results,
            rule_results=rule_results,
            major_timetable=major_timetable,
        )

        # 5. 暂存结果，设置等待阶段
        state["current_recommendations"] = recommendations
        state["conversation_phase"] = ConversationPhase.AWAIT_COURSE_SELECTION.value

        # 持久化到 Redis
        save_session_state(user_id, state["session_id"], {
            "conversation_phase": state["conversation_phase"],
            "has_minor_course": state["has_minor_course"],
            "current_recommendations": recommendations,
        })

        state["final_response"] = self._format_recommendation_output(
            recommendations, user
        )
        state["intent"] = "plan_course"
        return state

    def _build_recommendations(self, user: dict, interests: list,
                                user_input: str, program_results: list,
                                course_results: list, rule_results: list,
                                major_timetable: list) -> List[Dict]:
        """
        构建辅修推荐清单。

        优先使用 LLM，失败时回退到规则模板。
        """
        # 提取程序信息
        programs_info = []
        seen_programs = set()
        for r in program_results:
            source = r.get("metadata", {}).get("source", "")
            prog_name = r.get("metadata", {}).get("program", "")
            if prog_name and prog_name not in seen_programs:
                seen_programs.add(prog_name)
                programs_info.append({
                    "name": prog_name,
                    "credits": r.get("metadata", {}).get("total_credits", "?"),
                    "difficulty": r.get("metadata", {}).get("difficulty", "?"),
                    "summary": r.get("document", "")[:200],
                })

        # 提取课程信息
        courses_info = []
        for r in course_results[:12]:
            meta = r.get("metadata", {})
            courses_info.append({
                "code": meta.get("course_code", ""),
                "name": meta.get("course_name", ""),
                "credits": meta.get("credits", ""),
                "difficulty": meta.get("difficulty", ""),
                "program": meta.get("program", ""),
                "prerequisites": meta.get("prerequisites", ""),
            })

        # LLM Prompt
        prompt = f"""你是一位大学辅修规划顾问。根据以下信息，为学生推荐最适合的辅修方案。

## 学生信息
- 主修专业：{user.get('major', '')}
- 年级：{user.get('grade', '')}
- 兴趣方向：{', '.join(interests) if interests else '未指定'}
- 可用时段：{user.get('available_slots', '未指定')}

## 可选辅修专业
{json.dumps(programs_info, ensure_ascii=False, indent=2)}

## 可选课程
{json.dumps(courses_info, ensure_ascii=False, indent=2)}

## 辅修规则
至少选 12 学分，每学期不超过 6 学分。主修优先，需注意时间冲突。

请输出 JSON 格式的推荐方案（最多 3 个方案）：
```json
{{
  "recommendations": [
    {{
      "rank": 1,
      "program_name": "金融辅修",
      "reason": "与计算机专业互补...",
      "courses": [
        {{"code": "FIN101", "name": "金融学基础", "credits": 3, "reason": "入门课程，无先修要求"}}
      ],
      "total_credits": 12,
      "risk_note": "FIN201 可能与主修 CS301 时间冲突，建议确认"
    }}
  ]
}}
```"""

        llm_response = _llm(prompt, system="你是一位经验丰富的大学学业规划顾问。输出简洁、专业、可操作的辅修建言。", role="task")

        # 尝试解析 LLM JSON
        if llm_response and llm_response != "<<FALLBACK>>":
            try:
                # 提取 JSON 块
                import re
                json_match = re.search(r'```json\s*(.*?)\s*```', llm_response, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group(1))
                    return data.get("recommendations", [])
                # 尝试直接解析
                data = json.loads(llm_response)
                return data.get("recommendations", [])
            except (json.JSONDecodeError, KeyError):
                pass

        # 回退：基于规则生成推荐
        return self._fallback_recommendations(
            user, interests, programs_info, courses_info
        )

    def _fallback_recommendations(self, user: dict, interests: list,
                                   programs_info: list,
                                   courses_info: list) -> List[Dict]:
        """
        规则模板回退方案 —— 无需 LLM 也能输出合理的推荐。
        """
        recs = []
        major = user.get("major", "")

        # 方案 1：匹配兴趣的第一个辅修专业
        if programs_info:
            prog = programs_info[0]
            prog_courses = [
                c for c in courses_info
                if c.get("program") == prog["name"]
            ][:4]
            recs.append({
                "rank": 1,
                "program_name": prog["name"],
                "reason": f"与您的兴趣方向「{interests[0] if interests else '综合发展'}」匹配度高，"
                          f"且与主修「{major}」有良好的学科交叉空间。",
                "courses": [
                    {
                        "code": c["code"],
                        "name": c["name"],
                        "credits": int(c.get("credits", 3)),
                        "reason": self._course_reason(c, "intro"),
                    }
                    for c in prog_courses
                ],
                "total_credits": sum(int(c.get("credits", 3)) for c in prog_courses),
                "risk_note": "建议在选课前确认上课时间是否与主修冲突。",
            })

        # 方案 2：第二个辅修专业
        if len(programs_info) > 1:
            prog2 = programs_info[1]
            prog2_courses = [
                c for c in courses_info
                if c.get("program") == prog2["name"]
            ][:4]
            recs.append({
                "rank": 2,
                "program_name": prog2["name"],
                "reason": f"备选方案，{prog2['name']}与主修的交叉领域就业前景广阔。",
                "courses": [
                    {
                        "code": c["code"],
                        "name": c["name"],
                        "credits": int(c.get("credits", 3)),
                        "reason": self._course_reason(c, "intro"),
                    }
                    for c in prog2_courses
                ],
                "total_credits": sum(int(c.get("credits", 3)) for c in prog2_courses),
                "risk_note": "建议与方案一对比后选择。",
            })

        # 方案 3：如果有第三个
        if len(programs_info) > 2:
            prog3 = programs_info[2]
            prog3_courses = [
                c for c in courses_info
                if c.get("program") == prog3["name"]
            ][:3]
            recs.append({
                "rank": 3,
                "program_name": prog3["name"],
                "reason": f"拓宽知识面的选择，{prog3['name']}知识在各行各业都有应用。",
                "courses": [
                    {
                        "code": c["code"],
                        "name": c["name"],
                        "credits": int(c.get("credits", 3)),
                        "reason": self._course_reason(c, "intro"),
                    }
                    for c in prog3_courses
                ],
                "total_credits": sum(int(c.get("credits", 3)) for c in prog3_courses),
                "risk_note": "课程较少，建议搭配其他课程满足 12 学分要求。",
            })

        return recs

    @staticmethod
    def _course_reason(course: dict, level: str) -> str:
        """生成课程推荐理由。"""
        name = course.get("name", "")
        code = course.get("code", "")
        diff = course.get("difficulty", "")
        pre = course.get("prerequisites", "")
        parts = []
        if diff == "入门":
            parts.append("入门课程，适合零基础")
        elif diff == "中级":
            parts.append("中级课程，需要一定基础")
        if pre and pre != "[]" and pre != '[""]':
            parts.append(f"先修要求：{pre}")
        return "；".join(parts) if parts else "核心课程"

    def _format_recommendation_output(self, recommendations: List[Dict],
                                       user: dict) -> str:
        """格式化推荐输出为美观文本。"""
        if not recommendations:
            return "⚠️ 暂未找到合适的辅修方案，请尝试调整兴趣方向或联系教务。\n\n💡 提示：输入「推荐辅修 + 你的兴趣方向」获取更精准推荐。"

        lines = [
            "📋 **辅修课程推荐方案**",
            f"",
            f"根据您的主修「{user.get('major', '')}」和兴趣偏好，为您推荐以下辅修方案：",
            f"",
        ]

        for rec in recommendations:
            rank = rec.get("rank", "?")
            prog = rec.get("program_name", "")
            reason = rec.get("reason", "")
            courses = rec.get("courses", [])
            total = rec.get("total_credits", 0)
            risk = rec.get("risk_note", "")

            stars = "⭐" * min(rank, 5) + ("☆" * max(5 - rank, 0))
            lines.append("─" * 50)
            lines.append(f"**方案 {rank}：{prog}** {stars}")
            lines.append(f"")
            lines.append(f"  推荐理由：{reason}")
            lines.append(f"  总学分：{total} 学分")
            lines.append(f"")

            if courses:
                lines.append(f"  📚 课程组合：")
                for c in courses:
                    code = c.get("code", "")
                    name = c.get("name", "")
                    credits = c.get("credits", "")
                    reason_c = c.get("reason", "")
                    lines.append(f"    • {code} {name}（{credits}学分）— {reason_c}")

            if risk:
                lines.append(f"")
                lines.append(f"  ⚠️ 注意事项：{risk}")

            lines.append(f"")

        lines.append("─" * 50)
        lines.append("")
        lines.append("💡 **如何选择？**")
        lines.append("  输入「对比方案」查看更详细的方案对比。")
        lines.append("  输入「重新推荐 + 其他兴趣」调整推荐方向。")

        return "\n".join(lines)

    # ── 选择确认流程 ──────────────────────────────────

    def _handle_selection(self, state: AgentState) -> AgentState:
        """
        处理用户对推荐方案的选择。

        流程：
          1. 从 State/Redis 读取上轮推荐结果
          2. 解析用户选择（"选方案一" / "选金融辅修"）
          3. 将课程写入 user_timetable
          4. 更新 users.has_minor
          5. phase → IDLE
        """
        user_id = state["user_id"]
        user_input = state.get("user_input", "")
        recommendations = state.get("current_recommendations", [])
        logger.info(f"CoursePlanner: 选择确认 user={user_id} input='{user_input[:50]}'")

        # ── 【优先】重新推荐：清空已选，按新兴趣重新生成方案 ──
        if "重新推荐" in user_input:
            return self._handle_restart(user_input, state)

        # 如果没有暂存的推荐结果，回退到推荐流程
        if not recommendations:
            logger.warning("No cached recommendations, falling back to recommendation")
            return self._handle_recommendation(state)

        # 解析用户选择
        selected = self._parse_selection(user_input, recommendations)

        if not selected:
            state["final_response"] = (
                "🤔 抱歉，我没能识别您的选择。\n\n"
                "请明确输入：\n"
                "  •「选方案一」或「选方案二」\n"
                "  •「选金融辅修」或「选数据科学辅修」\n"
                "  •「对比方案」查看详细信息"
            )
            return state

        # 写入课表 —— 从 SQLite 按辅修专业读取预设课程列表
        user = get_user(user_id)
        program_name = selected.get("program_name", "")

        # 从 SQLite 查出该辅修专业下的全部课程
        program_courses = list_courses(program=program_name)
        if not program_courses:
            # 兜底：SQLite 里没有则用推荐结果里的课程列表
            program_courses = [
                {
                    "course_code": c.get("code", ""),
                    "course_name": c.get("name", ""),
                    "credits": c.get("credits", 3),
                }
                for c in selected.get("courses", [])
            ]

        added_courses = []
        total_credits = 0.0
        for pc in program_courses:
            code = pc.get("course_code", "")
            name = pc.get("course_name", code)
            credits = float(pc.get("credits", 0) or 0)

            # 写入课表
            entry = {
                "user_id": user_id,
                "course_code": code,
                "course_type": "minor",
                "week_start": 1,
                "week_end": 16,
                "day_of_week": self._default_day(code),
                "period_start": self._default_period_start(code),
                "period_end": self._default_period_end(code),
                "location": self._default_location(code),
                "exam_time": pc.get("semester", "") + "期末" if isinstance(pc, dict) and pc.get("semester") else "",
            }
            add_timetable_entry(entry)
            added_courses.append(f"{code} {name}（{credits:.0f}学分）")
            total_credits += credits

        # 更新用户辅修状态
        upsert_user(
            user_id=user_id,
            student_name=user.get("student_name", ""),
            major=user.get("major", ""),
            grade=user.get("grade"),
            available_slots=user.get("available_slots"),
            interests=user.get("interests"),
            has_minor=1,
            minor_program=program_name,
        )

        # 重置状态
        state["has_minor_course"] = True
        state["conversation_phase"] = ConversationPhase.IDLE.value
        state["current_recommendations"] = []

        # 持久化
        save_session_state(user_id, state["session_id"], {
            "conversation_phase": state["conversation_phase"],
            "has_minor_course": True,
            "current_recommendations": [],
        })

        # 格式化输出 —— 调取已同步的 added_courses 和 total_credits
        lines = [
            "✅ **辅修选择确认成功！**",
            "",
            f"📌 已选辅修专业：**{program_name}**",
            f"📌 已添加课程：",
        ]
        for c in added_courses:
            lines.append(f"    • {c}")
        lines.append("")
        lines.append(f"📌 总学分：{total_credits:.0f} 学分")
        lines.append("")
        lines.append("💡 **下一步建议：**")
        lines.append("  • 输入「检测冲突」检查上课/考试时间是否冲突")
        lines.append("  • 输入「推荐资源」获取配套学习资料")
        lines.append("  • 输入「录入成绩」记录学习进度")

        state["final_response"] = "\n".join(lines)
        state["intent"] = "plan_course"
        return state

    # ── 已选辅修展示 ──────────────────────────────────

    def _show_existing_minor(self, state: AgentState, user: dict) -> AgentState:
        """
        用户已有辅修数据时，直接读取 SQLite 展示现状，
        不再走 RAG 推荐流程。
        """
        user_id = state["user_id"]
        program_name = user.get("minor_program", "")

        # 从 SQLite 读课程列表
        program_courses = list_courses(program=program_name)
        timetable = get_timetable(user_id, course_type="minor")

        added_courses = []
        total_credits = 0.0
        seen_codes = set()
        for pc in program_courses:
            code = pc.get("course_code", "")
            if code in seen_codes:
                continue
            seen_codes.add(code)
            name = pc.get("course_name", code)
            credits = float(pc.get("credits", 0) or 0)
            added_courses.append(f"{code} {name}（{credits:.0f}学分）")
            total_credits += credits

        # 也把 timetable 里有但 courses 表没有的列出来
        for t in timetable:
            code = t.get("course_code", "")
            if code not in seen_codes:
                seen_codes.add(code)
                added_courses.append(f"{code}（{t.get('location', '')}）")

        lines = [
            "📋 **当前辅修方案**",
            "",
            f"📌 辅修专业：**{program_name}**",
            f"📌 已选课程（{len(added_courses)} 门）：",
        ]
        for c in added_courses:
            lines.append(f"    • {c}")
        lines.append("")
        lines.append(f"📌 总学分：{total_credits:.0f} 学分")
        lines.append("")
        lines.append("━" * 40)
        lines.append("")
        lines.append("💡 **后续操作：**")
        lines.append("  • 输入「重新推荐 + 新兴趣」更换辅修方向")
        lines.append("  • 输入「检测冲突」检查上课/考试时间冲突")
        lines.append("  • 输入「推荐资源」获取配套学习资料")
        lines.append("  • 输入「录入成绩」记录学习进度")

        state["final_response"] = "\n".join(lines)
        state["conversation_phase"] = ConversationPhase.IDLE.value
        state["intent"] = "plan_course"
        return state

    # ── 重新推荐流程 ──────────────────────────────────

    def _handle_restart(self, user_input: str, state: AgentState) -> AgentState:
        """
        处理「重新推荐」指令。

        1. 清空已选辅修数据（SQLite + state）
        2. 若输入含「+」，截取加号后文字作为新兴趣 → 直接生成新方案
        3. 若仅「重新推荐」，提示用户输入新兴趣 → 切换 AWAIT_NEW_INTERESTS
        """
        user_id = state["user_id"]
        logger.info(f"CoursePlanner: 重新推荐 user={user_id}")

        # 清空辅修数据
        self._clear_minor_courses(user_id, state)

        # 检查是否带加号（新兴趣）—— 用原始输入，因为清洗会去掉 +
        raw = state.get("_raw_input", user_input)
        if "+" in raw:
            # 截取加号后面的文字作为新兴趣（用原始输入 split）
            parts = raw.split("+", 1)
            new_interest = parts[1].strip() if len(parts) > 1 else ""

            if new_interest:
                # 更新用户兴趣
                user = get_user(user_id)
                if user:
                    upsert_user(
                        user_id=user_id,
                        student_name=user.get("student_name", ""),
                        major=user.get("major", ""),
                        grade=user.get("grade"),
                        available_slots=user.get("available_slots"),
                        interests=json.dumps([new_interest], ensure_ascii=False),
                        has_minor=0,
                        minor_program=None,
                    )

                # 构建新输入文本，直接生成推荐
                state["user_input"] = f"推荐{new_interest}辅修"
                state["conversation_phase"] = ConversationPhase.IDLE.value
                state["current_recommendations"] = []
                state["has_minor_course"] = False

                save_session_state(user_id, state["session_id"], {
                    "conversation_phase": ConversationPhase.AWAIT_COURSE_SELECTION.value,
                    "has_minor_course": False,
                    "current_recommendations": [],
                })

                # 生成新方案
                result = self._handle_recommendation(state)
                # 确保停留在等待选课状态
                result["conversation_phase"] = ConversationPhase.AWAIT_COURSE_SELECTION.value
                return result

        # 仅「重新推荐」，无加号 → 提示输入新兴趣
        state["final_response"] = (
            "🔄 **重新推荐辅修方案**\n\n"
            "已清空原有辅修选择和相关课程数据。\n\n"
            "请输入您的新兴趣方向，例如：\n"
            "  •「我对金融感兴趣」\n"
            "  •「想学数据科学」\n"
            "  •「法学」\n\n"
            "💡 提示：也可以直接输入「重新推荐 + 新兴趣」一步到位，\n"
            "    如「重新推荐 + 法学」"
        )
        state["conversation_phase"] = ConversationPhase.AWAIT_NEW_INTERESTS.value
        state["current_recommendations"] = []
        state["has_minor_course"] = False
        state["intent"] = "plan_course"

        save_session_state(user_id, state["session_id"], {
            "conversation_phase": state["conversation_phase"],
            "has_minor_course": False,
            "current_recommendations": [],
        })

        return state

    def _handle_new_interests(self, state: AgentState) -> AgentState:
        """
        处理用户输入的新兴趣方向（AWAIT_NEW_INTERESTS 状态下）。

        读取用户输入作为新兴趣 → 调用推荐流程生成方案 → 切回等待选课。
        """
        user_id = state["user_id"]
        user_input = state.get("user_input", "").strip()
        logger.info(f"CoursePlanner: 新兴趣输入 user={user_id} interest='{user_input[:50]}'")

        # 如果用户又输入了「重新推荐 + xxx」，交给 _handle_restart
        if "重新推荐" in user_input:
            return self._handle_restart(user_input, state)

        # 更新用户兴趣
        new_interest = user_input
        user = get_user(user_id)
        if user:
            upsert_user(
                user_id=user_id,
                student_name=user.get("student_name", ""),
                major=user.get("major", ""),
                grade=user.get("grade"),
                available_slots=user.get("available_slots"),
                interests=json.dumps([new_interest], ensure_ascii=False),
                has_minor=0,
                minor_program=None,
            )

        # 构建推荐查询
        state["user_input"] = f"推荐{new_interest}辅修"
        state["conversation_phase"] = ConversationPhase.IDLE.value
        state["current_recommendations"] = []
        state["has_minor_course"] = False

        # 生成推荐
        result = self._handle_recommendation(state)

        # 切回等待选课状态
        result["conversation_phase"] = ConversationPhase.AWAIT_COURSE_SELECTION.value

        save_session_state(user_id, state["session_id"], {
            "conversation_phase": result["conversation_phase"],
            "has_minor_course": False,
            "current_recommendations": result.get("current_recommendations", []),
        })

        return result

    @staticmethod
    def _clear_minor_courses(user_id: str, state: AgentState):
        """清空用户辅修课表 + 重置辅修状态。"""
        from src.storage.sqlite_client import clear_user_minor_data
        clear_user_minor_data(user_id)
        state["has_minor_course"] = False
        state["current_recommendations"] = []
        logger.info(f"CoursePlanner: 已清空 user={user_id} 的辅修数据")

    def _parse_selection(self, user_input: str,
                          recommendations: List[Dict]) -> Optional[Dict]:
        """
        解析用户的方案选择。

        支持：
          - "选方案一" / "选第一个" / "方案1" / "1"
          - "选金融辅修" / "金融" / "数据科学"
        """
        import re

        text = user_input.strip()

        # 数字匹配
        num_match = re.search(r'([一二三四五1-5])', text)
        if num_match:
            num_str = num_match.group(1)
            num_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
            idx = num_map.get(num_str, int(num_str) if num_str.isdigit() else None)
            if idx and 1 <= idx <= len(recommendations):
                return recommendations[idx - 1]

        # 名称匹配
        for rec in recommendations:
            prog_name = rec.get("program_name", "")
            if prog_name and prog_name in text:
                return rec

        return None

    # ── 默认时间映射（知识库中提取的常规上课时间）─────────

    @staticmethod
    def _default_day(code: str) -> int:
        mapping = {
            "FIN101": 2, "FIN201": 3, "FIN301": 5,
            "DS101": 2, "DS201": 4,
            "LAW101": 1, "LAW201": 3,
        }
        return mapping.get(code, 4)

    @staticmethod
    def _default_period_start(code: str) -> int:
        mapping = {
            "FIN101": 3, "FIN201": 3, "FIN301": 1,
            "DS101": 6, "DS201": 1,
            "LAW101": 6, "LAW201": 6,
        }
        return mapping.get(code, 3)

    @staticmethod
    def _default_period_end(code: str) -> int:
        mapping = {
            "FIN101": 5, "FIN201": 5, "FIN301": 3,
            "DS101": 8, "DS201": 3,
            "LAW101": 8, "LAW201": 8,
        }
        return mapping.get(code, 5)

    @staticmethod
    def _default_location(code: str) -> str:
        mapping = {
            "FIN101": "经管楼A201", "FIN201": "经管楼B102", "FIN301": "经管楼C303",
            "DS101": "信息楼D201", "DS201": "信息楼E102",
            "LAW101": "法学院F101", "LAW201": "法学院F201",
        }
        return mapping.get(code, "待定")
