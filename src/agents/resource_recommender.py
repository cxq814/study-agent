"""
资源推荐 Agent。

职责：
  1. 学习薄弱点识别 — 分析 study_progress 找出低分/缺勤课程
  2. 多级检索 — 按薄弱课程检索知识库（习题 + 笔记 + 网课）
  3. LLM/模板整理 — 生成个性化推荐清单
  4. 多轮交互 — 推荐后等待用户反馈

依赖：
  - SQLite: study_progress, courses, user_timetable
  - ChromaDB: RAG 检索资源文件
  - Redis: 会话状态（推荐暂存、phase 控制）
"""

import json
import logging
from typing import List, Dict, Optional

from src.graph.state import AgentState, ConversationPhase
from src.storage.sqlite_client import (
    get_study_progress, get_course, get_timetable,
)
from src.storage.redis_client import save_session_state
from src.rag.retriever import Retriever
from src.tools.llm_tools import llm_call, llm_is_available
try:
    from src.scraper.pipelines import search_scraped
except ImportError:
    search_scraped = None

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
#  资源推荐 Agent
# ══════════════════════════════════════════════════════

class ResourceRecommenderAgent:
    """资源推荐 Agent —— 薄弱点识别 + 多级检索 + 智能推荐。"""

    # 课程代码正则：2-4个大写字母 + 3-4个数字（如 FIN101, CS201, DS101）
    _COURSE_CODE_RE = __import__("re").compile(r'\b([A-Z]{2,4}\d{3,4})\b')

    def __init__(self):
        self.retriever = Retriever()

    def run(self, state: AgentState) -> AgentState:
        """
        主入口：根据 conversation_phase 分发。
        """
        phase = state.get("conversation_phase", "")

        if phase == ConversationPhase.AWAIT_RESOURCE_FEEDBACK.value:
            return self._handle_feedback(state)
        else:
            return self._recommend_resources(state)

    # ── 指令解析 ──────────────────────────────────────

    @classmethod
    def _parse_course_code(cls, user_input: str) -> Optional[str]:
        """
        从用户输入中提取课程代码。

        例：
          「推荐资源 FIN101」→ "FIN101"
          「推荐资源」→ None
          「FIN101 有什么资料」→ "FIN101"
        """
        m = cls._COURSE_CODE_RE.search(user_input.upper())
        return m.group(1) if m else None

    # ── 推荐流程 ──────────────────────────────────────

    def _recommend_resources(self, state: AgentState) -> AgentState:
        """
        资源推荐主流程。

        场景 A：「推荐资源」→ 全局薄弱点推荐
        场景 B：「推荐资源 FIN101」→ 仅查询该门课程资源

        规则：
          - 成功展示资源 → phase = AWAIT_RESOURCE_FEEDBACK，追加反馈提问
          - 未找到资源 → 提示无数据，phase = IDLE
        """
        user_id = state["user_id"]
        user_input = state.get("user_input", "")
        print(f"[DIAG] _recommend_resources START: user={user_id} input='{user_input[:50]}'", flush=True)
        logger.info(f"ResourceRecommender: 推荐请求 user={user_id} input='{user_input[:50]}'")

        # ── 场景 B：指定课程代码 → 定向检索 ──
        course_code = self._parse_course_code(user_input)
        if course_code:
            return self._recommend_for_course(course_code, state)

        # ── 场景 A：全局推荐（薄弱点驱动 / 进阶拓展）──

        # 1. 读取学习进度
        all_progress = get_study_progress(user_id)
        if not all_progress:
            state["final_response"] = (
                "⚠️ 暂未找到学习进度记录。\n\n"
                "请先录入学习数据，输入「录入成绩 [课程代码] 第N周 作业XX 考勤XX 小测XX」开始。\n"
                "例如：「录入成绩 FIN101 第5周 作业88 考勤正常 小测85」"
            )
            state["conversation_phase"] = ConversationPhase.IDLE.value
            return state

        # 2. 获取辅修课程清单 + 构建课程摘要（含成绩，供 LLM 使用）
        minor_courses = get_timetable(user_id, course_type="minor")
        minor_codes = set(c.get("course_code", "") for c in minor_courses)
        course_summary = self._build_course_summary(all_progress, minor_courses, minor_codes)

        # 3. 分析薄弱点
        weak_points = self._identify_weak_points(all_progress, minor_codes)
        print(f"[DIAG] weak_points identified: {len(weak_points)} courses", flush=True)

        # 4. 多级检索 — 有薄弱点搜薄弱点，无薄弱点搜全部课程
        all_resources = []
        search_targets = weak_points if weak_points else [
            {"course_code": cs["course_code"], "course_name": cs["course_name"], "issues": []}
            for cs in (course_summary or [])[:6]
        ]
        for target in search_targets:
            resources = self._retrieve_for_weak_point(target)
            all_resources.append({
                "weak_point": target,
                "resources": resources,
            })

        # 5. 构建推荐（LLM 或模板）— 传入 course_summary 供进阶模式使用
        recommendations = self._build_recommendations(
            weak_points, all_resources, course_summary
        )
        print(f"[DIAG] recommendations result: {len(recommendations)} items", flush=True)

        # 6. 暂存 + 设置等待阶段
        state["current_resources"] = all_resources
        state["conversation_phase"] = ConversationPhase.AWAIT_RESOURCE_FEEDBACK.value

        save_session_state(user_id, state["session_id"], {
            "conversation_phase": state["conversation_phase"],
            "has_minor_course": state["has_minor_course"],
            "current_resources": all_resources,
        })

        # 7. 构建每门课的真实链接映射（用于强制注入）
        # 注意：_collect_scraped_context 内部有懒加载回退，即使模块级 search_scraped 为 None 也会尝试导入
        print(f"[DIAG] module-level search_scraped is None: {search_scraped is None}", flush=True)
        print(f"[DIAG] weak_points={len(weak_points)}, course_summary={len(course_summary) if course_summary else 0}", flush=True)
        scraped = self._collect_scraped_context(weak_points, course_summary)
        print(f"[DIAG] _collect_scraped_context returned: {len(scraped or [])} items", flush=True)
        scraped_links = {}
        for s in (scraped or []):
            for mc in s.get("matched_courses", []):
                url = s.get("url", "")
                if url and url not in scraped_links.get(mc, []):
                    scraped_links.setdefault(mc, []).append(url)
        print(f"[DIAG] scraped_links built: {len(scraped_links)} courses, {sum(len(v) for v in scraped_links.values())} total URLs", flush=True)

        # 8. 统计 RAG 和爬虫使用情况
        rag_chunks = sum(
            len(entry.get("resources", {}).get(k, [])) if isinstance(entry.get("resources", {}), dict) else 0
            for entry in all_resources for k in ("exercises", "notes", "videos")
        )
        scraped_count = sum(len(v) for v in scraped_links.values())
        data_stats = {
            "rag_chunks": rag_chunks,
            "scraped_courses": len(scraped_links),
            "scraped_urls": scraped_count,
        }
        print(f"[DIAG] data_stats: RAG={rag_chunks} chunks, scraper={scraped_count} URLs across {len(scraped_links)} courses", flush=True)

        # 9. 格式化输出 + 追加反馈提问
        mode = "advanced" if not weak_points else "remedial"
        output = self._format_recommendation_output(weak_points, all_resources, recommendations, mode, scraped_links, data_stats)
        output += "\n\n💬 请问这些推荐资源对您有帮助吗？"
        state["final_response"] = output
        state["intent"] = "recommend_res"
        return state

    # ── 场景 B：按课程代码定向推荐 ───────────────────

    def _recommend_for_course(self, course_code: str,
                              state: AgentState) -> AgentState:
        """
        仅检索指定课程的资源并展示。

        成功 → phase = AWAIT_RESOURCE_FEEDBACK
        失败（无数据）→ phase = IDLE，提示未找到
        """
        user_id = state["user_id"]
        logger.info(f"ResourceRecommender: 定向推荐 course={course_code}")

        # 1. 获取课程信息
        course_info = get_course(course_code)
        course_name = course_info.get("course_name", course_code) if course_info else course_code

        # 2. 检索知识库中该课程的所有资源类型
        exercises = self.retriever.search_resources(
            f"{course_code} {course_name} 习题", resource_type="exercise",
            course_code=course_code, top_k=2,
        )
        notes = self.retriever.search_resources(
            f"{course_code} {course_name} 笔记 知识点", resource_type="note",
            course_code=course_code, top_k=2,
        )
        videos = self.retriever.search_resources(
            f"{course_code} {course_name} 网课 视频", resource_type="video",
            top_k=2,
        )

        resources = {
            "exercises": exercises,
            "notes": notes,
            "videos": videos,
            "total_found": len(exercises) + len(notes) + len(videos),
        }

        # 3. 无数据 → 保持 idle
        if resources["total_found"] == 0:
            state["final_response"] = (
                f"⚠️ 暂未找到 {course_code} {course_name} 的相关资料。\n\n"
                f"💡 建议：\n"
                f"  • 尝试输入「推荐资源」查看全局学习建议\n"
                f"  • 联系任课教师获取课程配套材料"
            )
            state["conversation_phase"] = ConversationPhase.IDLE.value
            state["intent"] = "recommend_res"
            return state

        # 4. 有数据 → 格式化输出
        lines = [
            f"📋 **{course_code} {course_name} — 学习资源**",
            "",
        ]

        if exercises:
            lines.append("📝 **推荐习题：**")
            for e in exercises:
                title = e.get("metadata", {}).get("title", "课后习题")
                lines.append(f"  • {title}")
            lines.append("")

        if notes:
            lines.append("📒 **推荐笔记：**")
            for n in notes:
                title = n.get("metadata", {}).get("title", "课堂笔记")
                lines.append(f"  • {title}")
            lines.append("")

        if videos:
            lines.append("🎬 **推荐网课：**")
            for v in videos:
                title = v.get("metadata", {}).get("title", "网课")
                lines.append(f"  • {title}")
            lines.append("")

        lines.append("━" * 40)
        lines.append("")

        output = "\n".join(lines)
        output += "💬 请问这些推荐资源对您有帮助吗？"

        # 5. 暂存 + 进入反馈等待
        course_resources = [{
            "weak_point": {
                "course_code": course_code,
                "course_name": course_name,
                "weakness_level": "targeted",
            },
            "resources": resources,
        }]

        state["current_resources"] = course_resources
        state["conversation_phase"] = ConversationPhase.AWAIT_RESOURCE_FEEDBACK.value

        save_session_state(user_id, state["session_id"], {
            "conversation_phase": state["conversation_phase"],
            "has_minor_course": state["has_minor_course"],
            "current_resources": course_resources,
        })

        state["final_response"] = output
        state["intent"] = "recommend_res"
        return state

    # ── 课程摘要构建 ──────────────────────────────────

    def _build_course_summary(self, progress: List[Dict],
                               minor_courses_list: List[Dict],
                               minor_codes: set) -> List[Dict]:
        """
        构建所有在读课程的成绩摘要（含无进度数据的课程）。

        优先使用有成绩的课程；同时纳入课表中的辅修课程（即使暂无成绩）。
        """
        by_course: Dict[str, List[Dict]] = {}
        for p in progress:
            code = p.get("course_code", "")
            if minor_codes and code not in minor_codes:
                continue
            by_course.setdefault(code, []).append(p)

        # 确保所有辅修课程都在摘要中（即使没有进度数据）
        covered = set(by_course.keys())
        for mc in minor_courses_list:
            code = mc.get("course_code", "")
            if code and code not in covered:
                by_course[code] = []  # 空记录 = 该课程无成绩
                covered.add(code)

        summary = []
        for code, records in by_course.items():
            hw_scores = [r.get("homework_score") for r in records
                        if r.get("homework_score") is not None]
            quiz_scores = [r.get("quiz_score") for r in records
                          if r.get("quiz_score") is not None]
            avg_hw = round(sum(hw_scores) / len(hw_scores), 1) if hw_scores else None
            avg_quiz = round(sum(quiz_scores) / len(quiz_scores), 1) if quiz_scores else None

            course_info = get_course(code)
            course_name = course_info.get("course_name", code) if course_info else code
            ctype = "minor" if code in minor_codes else "major"

            summary.append({
                "course_code": code,
                "course_name": course_name,
                "avg_homework": avg_hw,
                "avg_quiz": avg_quiz,
                "total_records": len(records),
                "type": ctype,
            })

        return summary

    # ── 薄弱点识别 ────────────────────────────────────

    def _identify_weak_points(self, progress: List[Dict],
                              minor_codes: set) -> List[Dict]:
        """
        从学习进度中识别薄弱点。

        判定标准：
          - 作业平均分 < 70 → 弱项
          - 小测平均分 < 60 → 弱项
          - 缺勤 ≥ 2 次 → 弱项
          - 只分析辅修课程（如无辅修则分析所有课程）
        """
        # 按课程分组
        by_course: Dict[str, List[Dict]] = {}
        for p in progress:
            code = p.get("course_code", "")
            # 如果用户有辅修课程，只分析辅修
            if minor_codes and code not in minor_codes:
                continue
            by_course.setdefault(code, []).append(p)

        weak_points = []
        for code, records in by_course.items():
            if not records:
                continue

            # 计算统计
            hw_scores = [r.get("homework_score") for r in records
                        if r.get("homework_score") is not None]
            quiz_scores = [r.get("quiz_score") for r in records
                          if r.get("quiz_score") is not None]
            absences = sum(1 for r in records
                          if r.get("attendance") == "absent")
            late = sum(1 for r in records
                      if r.get("attendance") == "late")

            avg_hw = sum(hw_scores) / len(hw_scores) if hw_scores else 100
            avg_quiz = sum(quiz_scores) / len(quiz_scores) if quiz_scores else 100

            # 获取课程名
            course_info = get_course(code)
            course_name = course_info.get("course_name", code) if course_info else code

            issues = []
            if avg_hw < 70:
                issues.append({
                    "type": "homework",
                    "value": round(avg_hw, 1),
                    "detail": f"作业平均分 {avg_hw:.0f}（偏低，建议加强练习）",
                })
            if avg_quiz < 60:
                issues.append({
                    "type": "quiz",
                    "value": round(avg_quiz, 1),
                    "detail": f"小测平均分 {avg_quiz:.0f}（需重点提升）",
                })
            if absences >= 2:
                issues.append({
                    "type": "attendance",
                    "value": absences,
                    "detail": f"缺勤 {absences} 次（影响学习连续性）",
                })
            elif late >= 3:
                issues.append({
                    "type": "attendance",
                    "value": late,
                    "detail": f"迟到 {late} 次（建议关注出勤）",
                })

            if issues:
                weak_points.append({
                    "course_code": code,
                    "course_name": course_name,
                    "avg_homework": round(avg_hw, 1),
                    "avg_quiz": round(avg_quiz, 1),
                    "absence_count": absences,
                    "late_count": late,
                    "total_records": len(records),
                    "issues": issues,
                    "weakness_level": self._calc_weakness_level(avg_hw, avg_quiz, absences),
                })

        # 按薄弱程度排序（最弱的排前面）
        level_order = {"high": 0, "medium": 1, "low": 2}
        weak_points.sort(key=lambda w: level_order.get(w["weakness_level"], 9))

        return weak_points

    @staticmethod
    def _calc_weakness_level(avg_hw: float, avg_quiz: float,
                            absences: int) -> str:
        """计算薄弱等级。"""
        score = 0
        if avg_hw < 60:
            score += 3
        elif avg_hw < 75:
            score += 1
        if avg_quiz < 50:
            score += 3
        elif avg_quiz < 65:
            score += 1
        if absences >= 3:
            score += 3
        elif absences >= 2:
            score += 1

        if score >= 5:
            return "high"
        elif score >= 2:
            return "medium"
        return "low"

    # ── 多级检索 ──────────────────────────────────────

    def _retrieve_for_weak_point(self, weak_point: Dict) -> List[Dict]:
        """
        多级检索：习题 → 笔记 → 网课。

        每级检索知识库中对应 resource_type 的内容。
        """
        code = weak_point["course_code"]
        course_name = weak_point["course_name"]
        issues = weak_point.get("issues", [])

        # 构建检索查询
        issue_types = [i["type"] for i in issues]
        query_parts = [code, course_name]

        if "homework" in issue_types:
            query_parts.append("习题 练习 作业")
        if "quiz" in issue_types:
            query_parts.append("知识点 考点 重点")
        if "attendance" in issue_types:
            query_parts.append("笔记 总结 课堂")

        query = " ".join(query_parts)

        # 搜索知识库（不限 resource_type，让语义匹配发挥作用）
        results = self.retriever.search(query, top_k=8)

        # 分类整理
        exercises = []
        notes = []
        videos = []

        for r in results:
            meta = r.get("metadata", {})
            rtype = meta.get("resource_type", "")
            source = meta.get("source", "")

            if rtype == "exercise" or "exercises" in source:
                exercises.append(r)
            elif rtype == "note" or "notes" in source:
                notes.append(r)
            elif rtype == "video" or "videos" in source:
                videos.append(r)

        # 每个类别最多取 2 条
        return {
            "exercises": exercises[:2],
            "notes": notes[:2],
            "videos": videos[:2],
            "total_found": len(results),
        }

    # ── 推荐构建 ──────────────────────────────────────

    def _build_recommendations(self, weak_points: List[Dict],
                               all_resources: List[Dict],
                               course_summary: List[Dict] = None) -> List[Dict]:
        """使用 LLM 或模板构建推荐内容。"""
        # 尝试 LLM（始终尝试，不依赖薄弱点）
        llm_resp = self._try_llm_recommendation(weak_points, all_resources, course_summary)
        if llm_resp:
            print(f"[DIAG] _build_recommendations: using LLM recommendations ({len(llm_resp)} items)", flush=True)
            return llm_resp

        # 模板回退
        print(f"[DIAG] _build_recommendations: LLM failed, using template (weak_points={len(weak_points)})", flush=True)
        return self._template_recommendations(weak_points, all_resources, course_summary)

    def _collect_scraped_context(self, weak_points: List[Dict],
                                  course_summary: List[Dict] = None) -> List[Dict]:
        """从爬虫数据中搜索相关资源。"""
        _search = search_scraped
        # 懒加载回退：如果模块级导入失败，尝试在方法内重新导入
        if not _search:
            try:
                from src.scraper.pipelines import search_scraped as _lazy_search
                _search = _lazy_search
                print("[DIAG] _collect_scraped_context: lazy import succeeded", flush=True)
            except ImportError as e:
                print(f"[DIAG] _collect_scraped_context: lazy import FAILED: {e}", flush=True)
                return []
        if not _search:
            print("[DIAG] _collect_scraped_context: _search is still None, returning []", flush=True)
            return []
        try:
            keywords = []
            for wp in (weak_points or [])[:3]:
                keywords.append(wp.get("course_code", ""))
                keywords.append(wp.get("course_name", ""))
            # 进阶模式：使用课程摘要中的课程信息
            if not keywords and course_summary:
                for cs in course_summary[:5]:
                    keywords.append(cs.get("course_code", ""))
                    keywords.append(cs.get("course_name", ""))
            if not keywords:
                keywords = ["金融学", "数据科学", "法学", "机器学习"]
            print(f"[DIAG] _collect_scraped_context: searching with keywords={keywords[:6]}...", flush=True)
            result = _search(keywords, top_k=15)
            print(f"[DIAG] _collect_scraped_context: got {len(result)} results", flush=True)
            return result
        except Exception as e:
            logger.warning("Failed to search scraped: %s", e)
            print(f"[DIAG] _collect_scraped_context: exception: {e}", flush=True)
            return []

    def _try_llm_recommendation(self, weak_points: List[Dict],
                                all_resources: List[Dict],
                                course_summary: List[Dict] = None) -> Optional[List[Dict]]:
        """尝试用 LLM 生成推荐。薄弱点模式 → 补救推荐；无薄弱点 → 进阶拓展推荐。"""
        is_advanced = not weak_points

        # 收集 RAG 上下文
        rag_parts = []
        for entry in all_resources[:5]:
            wp = entry.get("weak_point", {})
            resources = entry.get("resources", {})
            # resources 是 dict: {"exercises": [...], "notes": [...], "videos": [...]}
            all_rag_items = []
            for key in ("exercises", "notes", "videos"):
                all_rag_items.extend(resources.get(key, []) if isinstance(resources, dict) else [])
            for r in all_rag_items[:3]:
                c = r.get("content", r.get("document", r.get("text", "")))[:300]
                if c:
                    rag_parts.append(f"[{wp.get('course_code', '')}] {c}")
        rag_ctx = "\n---\n".join(rag_parts) if rag_parts else ""
        print(f"[DIAG] _try_llm_recommendation: rag_ctx={len(rag_ctx)} chars from {len(rag_parts)} chunks", flush=True)

        # 收集爬虫数据 — 按课程预匹配（内部有懒加载回退）
        scraped = self._collect_scraped_context(weak_points, course_summary)
        # 为每门课程预先匹配真实链接
        scraped_links = {}
        for s in (scraped or []):
            for mc in s.get("matched_courses", []):
                url = s.get("url", "")
                if url and url not in scraped_links.get(mc, []):
                    scraped_links.setdefault(mc, []).append(url)
        # 构建 scrape 上下文（精简，只传有真实链接的）
        scraped_items = []
        for s in (scraped or [])[:8]:
            if s.get("url", ""):
                scraped_items.append({
                    "title": s.get("title", "")[:60],
                    "platform": s.get("platform", ""),
                    "url": s.get("url", ""),
                    "courses": s.get("matched_courses", []),
                })
        scraped_s = json.dumps(scraped_items, ensure_ascii=False, indent=2) if scraped_items else ""

        # ── 构建 LLM 提示词 ──
        if is_advanced:
            # 进阶拓展模式：学生成绩好，推荐高阶资源
            # 为每门课程匹配真实链接
            cs_with_links = []
            for cs in (course_summary or [])[:8]:
                code = cs["course_code"]
                links = scraped_links.get(code, [])
                entry = {
                    "course_code": code,
                    "course_name": cs.get("course_name", ""),
                    "avg_homework": cs.get("avg_homework"),
                    "avg_quiz": cs.get("avg_quiz"),
                    "available_links": links[:2],  # 每门课最多 2 个真实链接
                }
                cs_with_links.append(entry)

            cs_json = json.dumps(cs_with_links, ensure_ascii=False, indent=2)

            prompt = f"""你是一位大学学业辅导专家。这位学生学习状况良好，没有明显薄弱课程。
请为TA的**每门在读课程**各推荐 1 个**进阶拓展**学习方向。

## 学生课程与真实可用资源（请务必使用 available_links 中的链接！）
{cs_json}

## 所有爬取资源（可交叉参考）
{scraped_s}

## 推荐方向（每门课选一个）
- 学术前沿 / 行业应用 / 竞赛备战 / 项目实战 / 交叉学科

## external_link 规则（非常重要！）
- **必须**使用上面 available_links 中该课程对应的真实链接
- 如果该课程没有 available_links，external_link 留空 ""
- **严禁编造链接**

请输出 JSON（务必为每门课生成一条）：
```json
{{
  "recommendations": [
    {{
      "course_code": "FIN101",
      "course_name": "金融学基础",
      "focus": "进阶方向",
      "plan": "具体学习路径",
      "tip": "学习技巧",
      "external_link": "从 available_links 中选择一个链接，或留空"
    }}
  ]
}}
```"""
        else:
            # 薄弱点补救模式
            wp_summary = []
            for wp in weak_points[:3]:
                wp_summary.append({
                    "course": f"{wp['course_code']} {wp['course_name']}",
                    "level": wp["weakness_level"],
                    "issues": [i["detail"] for i in wp.get("issues", [])],
                })

            prompt = f"""你是一位大学学习辅导专家。根据学生的薄弱点分析，为其推荐具体的学习资源。

## 学生薄弱点
{json.dumps(wp_summary, ensure_ascii=False, indent=2)}

## 知识库资料摘要
{rag_ctx[:2000] if rag_ctx else "（暂无本地资料）"}

## 网络最新资源
{scraped_s}

## 可推荐资源类型
- 课后习题 — 针对性练习巩固
- 课堂笔记 — 知识点梳理复习
- 网课视频 — 系统性学习补充

请输出 JSON 格式推荐：
```json
{{
  "recommendations": [
    {{
      "course_code": "FIN101",
      "focus": "针对xxx薄弱点",
      "plan": "建议先xxx再xxx",
      "tip": "具体学习技巧"
    }}
  ]
}}
```"""

        print(f"[DIAG] _try_llm_recommendation: calling LLM (weak_points={len(weak_points)}, rag_ctx={len(rag_ctx)} chars)", flush=True)
        try:
            resp = llm_call(prompt, system="你是一位有15年经验的大学学业辅导专家。", role="task")
        except Exception as e:
            print(f"[DIAG] _try_llm_recommendation: LLM call CRASHED: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return None

        if not resp:
            print(f"[DIAG] _try_llm_recommendation: LLM returned EMPTY response", flush=True)
            return None
        if "LLM unavailable" in resp:
            print(f"[DIAG] _try_llm_recommendation: LLM UNAVAILABLE — {resp[:120]}", flush=True)
            return None

        print(f"[DIAG] _try_llm_recommendation: LLM response received ({len(resp)} chars)", flush=True)
        try:
            import re
            json_match = re.search(r'```json\s*(.*?)\s*```', resp, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(1))
                recs = data.get("recommendations", [])
            else:
                data = json.loads(resp)
                recs = data.get("recommendations", [])

            # ── 后处理：过滤虚构链接，只保留真实爬取的 URL ──
            all_real_urls = set()
            for links in scraped_links.values():
                all_real_urls.update(links)
            cleaned = 0
            for rec in recs:
                link = rec.get("external_link", "")
                if link and link not in all_real_urls:
                    rec["external_link"] = ""
                    cleaned += 1
            if cleaned:
                print(f"[DIAG] _try_llm_recommendation: cleaned {cleaned} hallucinated links", flush=True)

            print(f"[DIAG] _try_llm_recommendation: parsed JSON → {len(recs)} recommendations", flush=True)
            return recs
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[DIAG] _try_llm_recommendation: JSON parse FAILED: {e} | raw={resp[:300]}", flush=True)
        return None

    def _template_recommendations(self, weak_points: List[Dict],
                                  all_resources: List[Dict],
                                  course_summary: List[Dict] = None) -> List[Dict]:
        """模板回退方案 — 结合爬虫数据 + 课程信息生成推荐。"""
        recs = []
        level_labels = {"high": "🔴 急需提升", "medium": "🟡 需要关注", "low": "🟢 轻度薄弱"}

        # 有薄弱点 → 标准模板
        for i, (wp, ar) in enumerate(zip(weak_points, all_resources)):
            resources = ar.get("resources", {})
            issues_text = [iss["detail"] for iss in wp.get("issues", [])]

            plan_steps = []
            if resources.get("notes"):
                plan_steps.append("先复习课堂笔记，理清知识框架")
            if resources.get("exercises"):
                plan_steps.append("完成课后习题，检验掌握程度")
            if resources.get("videos"):
                plan_steps.append("观看网课视频，补充理解盲区")
            if not plan_steps:
                plan_steps.append("建议增加该课程的练习时间投入")

            recs.append({
                "course_code": wp["course_code"],
                "course_name": wp["course_name"],
                "level": level_labels.get(wp["weakness_level"], ""),
                "focus": "；".join(issues_text),
                "plan": " → ".join(plan_steps),
                "tip": self._study_tip(wp.get("issues", [])),
                "resources": resources,
            })

        # 无薄弱点 → 进阶模板（结合爬虫数据）
        if not recs and course_summary:
            # 尝试匹配爬虫资源（内部有懒加载回退）
            scraped = self._collect_scraped_context([], course_summary)
            scraped_by_course = {}
            for s in scraped:
                for mc in s.get("matched_courses", []):
                    scraped_by_course.setdefault(mc, []).append(s)

            for cs in course_summary[:6]:
                code = cs["course_code"]
                cname = cs["course_name"]
                hw = cs.get("avg_homework")
                qz = cs.get("avg_quiz")
                grade_str = f"作业均分{hw}" if hw else ""
                grade_str += f" | 小测均分{qz}" if qz else ""

                # 匹配该课程的爬虫资源
                matched = scraped_by_course.get(code, [])
                ext_links = [m.get("url", "") for m in matched[:2] if m.get("url")]

                plan_parts = ["结合课程知识点进行系统性复习"]
                if matched:
                    plan_parts.append(f"参考 {matched[0].get('platform', '网络')} 资源「{matched[0].get('title', '相关课程')}」深入学习")
                plan_parts.append("尝试完成一项综合项目或参加学科竞赛以巩固所学")

                recs.append({
                    "course_code": code,
                    "course_name": cname,
                    "focus": f"当前成绩优秀（{grade_str}），推荐进阶拓展",
                    "plan": " → ".join(plan_parts),
                    "tip": "在扎实掌握基础知识后，可以关注学科前沿动态和行业应用案例",
                    "external_link": ext_links[0] if ext_links else "",
                })

        return recs

    @staticmethod
    def _study_tip(issues: List[Dict]) -> str:
        """根据问题类型给出学习技巧。"""
        for iss in issues:
            t = iss.get("type", "")
            if t == "homework":
                return "建议先独立完成习题再对照答案，标记错题建立错题本"
            if t == "quiz":
                return "小测前回顾前几周笔记+错题，用思维导图串联知识点"
            if t == "attendance":
                return "尽量保证出勤，课堂听讲效率远高于课后自学弥补"
        return "保持当前学习节奏，定期回顾总结"

    # ── 输出格式化 ────────────────────────────────────

    @staticmethod
    def _format_recommendation_output(weak_points: List[Dict],
                                      all_resources: List[Dict],
                                      recommendations: List[Dict] = None,
                                      mode: str = "remedial",
                                      scraped_links: Dict[str, List[str]] = None,
                                      data_stats: Dict = None) -> str:
        """格式化推荐输出。mode: 'remedial' (补救) | 'advanced' (进阶拓展)。"""
        scraped_links = scraped_links or {}
        data_stats = data_stats or {}
        _VERSION = "v3.2-rag-scraper"  # 版本标记

        # LLM 生成的推荐内容优先使用
        if recommendations:
            print(f"[DIAG] _format_recommendation_output: AI path ({len(recommendations)} recs, mode={mode})", flush=True)
            header = "📋 **AI 进阶学习推荐**" if mode == "advanced" else "📋 **AI 个性化学习推荐**"
            subtitle = "你的课程成绩优秀！以下是为你定制的进阶拓展方案：" if mode == "advanced" else ""

            # 数据来源标识
            rag_n = data_stats.get("rag_chunks", 0)
            scr_n = data_stats.get("scraped_urls", 0)
            scr_c = data_stats.get("scraped_courses", 0)
            rag_icon = "✅" if rag_n > 0 else "❌"
            scr_icon = "✅" if scr_n > 0 else "❌"
            data_line = f"🟢 `{_VERSION}` | 📚 RAG知识库: {rag_icon} {rag_n}条 | 🕸️ 爬虫资源: {scr_icon} {scr_n}个链接({scr_c}门课)"

            lines = [header, data_line, ""]
            if subtitle:
                lines.append(subtitle)
                lines.append("")
            for i, rec in enumerate(recommendations[:5], 1):
                code = rec.get("course_code", "")
                cname = rec.get("course_name", "")
                label = f"{code} {cname}".strip() if cname else code
                focus = rec.get("focus", "")
                plan = rec.get("plan", "")
                tip = rec.get("tip", "")
                link = rec.get("external_link", "")
                lines.append(f"**{i}. {label}** — {focus}")
                if plan:
                    lines.append(f"   📝 学习计划：{plan}")
                if tip:
                    lines.append(f"   💡 技巧：{tip}")
                if link:
                    lines.append(f"   🔗 外部资源：{link}")
                # ── 强制注入：LLM 没填链接时，从爬虫数据中补 ──
                if not link and code in scraped_links:
                    for real_url in scraped_links[code][:1]:  # 每门课最多补1个
                        lines.append(f"   🔗 推荐资源：{real_url}")
                lines.append("")
            return "\n".join(lines)

        if not weak_points:
            print(f"[DIAG] _format_recommendation_output: template advanced — no weak points", flush=True)
            # 模板进阶推荐已由 _template_recommendations 生成（含爬虫数据），走通用格式
            lines = [
                "📋 **学习资源推荐**",
                "",
                "✅ **当前学习状况良好！**",
                "",
                "暂未发现明显的薄弱课程。为你推荐以下进阶拓展方向：",
                "",
            ]
            # 如果有从模板生成的推荐（含爬虫数据），直接展示
            if recommendations is not None and len(recommendations) == 0:
                lines.append("💡 建议：")
                lines.append("  • 输入「推荐资源 [课程代码]」获取特定课程资料")
                lines.append("  • 继续保持当前学习节奏")
            return "\n".join(lines)

        lines = [
            "📋 **学习资源推荐报告**",
            "",
            f"  分析课程：{len(weak_points)} 门",
            f"  薄弱课程：{len(weak_points)} 门",
            "",
            "检测到部分课程存在薄弱点，为你推送基础巩固资料：",
            "▶ 课后基础习题、核心知识点笔记、入门讲解网课",
            "",
        ]

        for i, (wp, ar) in enumerate(zip(weak_points, all_resources)):
            resources = ar.get("resources", {})
            level_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}

            lines.append("━" * 50)
            lines.append(
                f"**{i+1}. {wp['course_code']} {wp['course_name']}** "
                f"{level_emoji.get(wp['weakness_level'], '')} "
                f"{wp['weakness_level']}"
            )
            lines.append("")
            lines.append(f"  学习记录：{wp['total_records']} 周 | "
                        f"作业均分 {wp['avg_homework']} | "
                        f"小测均分 {wp['avg_quiz']} | "
                        f"缺勤 {wp['absence_count']} 次")
            lines.append("")

            for iss in wp.get("issues", []):
                lines.append(f"  ⚡ {iss['detail']}")

            lines.append("")

            # 习题
            exs = resources.get("exercises", [])
            if exs:
                lines.append("  📝 **推荐习题：**")
                for e in exs:
                    title = e.get("metadata", {}).get("title", "课后习题")
                    src = e.get("metadata", {}).get("source", "")
                    lines.append(f"    • {title} ({src})")

            # 笔记
            nts = resources.get("notes", [])
            if nts:
                lines.append("  📒 **推荐笔记：**")
                for n in nts:
                    title = n.get("metadata", {}).get("title", "课堂笔记")
                    src = n.get("metadata", {}).get("source", "")
                    lines.append(f"    • {title} ({src})")

            # 网课
            vids = resources.get("videos", [])
            if vids:
                lines.append("  🎬 **推荐网课：**")
                for v in vids:
                    title = v.get("metadata", {}).get("title", "网课")
                    src = v.get("metadata", {}).get("source", "")
                    lines.append(f"    • {title} ({src})")

            lines.append("")

        lines.append("━" * 50)
        lines.append("")
        lines.append("💡 **如何反馈？**")
        lines.append("  输入「有帮助」/「换一批」/「不够详细」告诉我这些资源是否有用。")
        lines.append("  输入「推荐资源 [课程代码]」获取特定课程更多资料。")
        lines.append("")
        lines.append("💬 请问这些推荐资源对您有帮助吗？")

        return "\n".join(lines)

    # ── 反馈处理 ──────────────────────────────────────

    def _handle_feedback(self, state: AgentState) -> AgentState:
        """处理用户对资源推荐的反馈。"""
        user_input = state.get("user_input", "")
        user_id = state["user_id"]

        logger.info(f"ResourceRecommender: 反馈处理 input='{user_input[:50]}'")

        # ── 全局指令拦截：命中则回到空闲，交给路由分发 ──
        _GLOBAL_COMMANDS = [
            "推荐辅修", "检测冲突", "录入成绩", "查看进度",
            "生成报告", "录入课表", "退课", "调课",
        ]
        if any(cmd in user_input for cmd in _GLOBAL_COMMANDS):
            state["conversation_phase"] = ConversationPhase.IDLE.value
            state["current_resources"] = []
            state["final_response"] = "✅ 已退出资源推荐，请输入您的新指令。"
            state["intent"] = "recommend_res"
            return state

        if any(kw in user_input for kw in ["有帮助", "不错", "谢谢", "好的", "OK"]):
            state["final_response"] = (
                "😊 很高兴这些资源对您有帮助！\n\n"
                "需要时随时输入「推荐资源」获取更多学习资料。\n"
                "祝学习顺利！📚"
            )
        elif any(kw in user_input for kw in ["换一批", "更多", "再来", "更新"]):
            # 重新检索
            state["conversation_phase"] = ConversationPhase.IDLE.value
            state["current_resources"] = []
            return self._recommend_resources(state)
        elif any(kw in user_input for kw in ["不够详细", "太少", "不足"]):
            state["final_response"] = (
                "📝 需要更详细的资料？\n\n"
                "您可以：\n"
                "  • 输入「推荐资源 [课程代码]」指定课程\n"
                "  • 输入「搜索资料 [关键词]」精确查找\n"
                "  • 联系任课教师获取更专业的指导"
            )
        else:
            # 无法识别 → 展示选项，保持反馈等待状态
            state["final_response"] = (
                "🤔 请问这些推荐资源对您有帮助吗？\n\n"
                "  输入「有帮助」— 问题已解决\n"
                "  输入「换一批」— 查看更多资源\n"
                "  输入「不够详细」— 需要更具体的内容"
            )
            state["intent"] = "recommend_res"
            return state

        # 退出反馈等待，清理状态
        state["conversation_phase"] = ConversationPhase.IDLE.value
        state["current_resources"] = []

        save_session_state(user_id, state["session_id"], {
            "conversation_phase": state["conversation_phase"],
            "has_minor_course": state["has_minor_course"],
            "current_resources": [],
        })

        state["intent"] = "recommend_res"
        return state
