"""
RAG 检索工具集 — 封装 ChromaDB 检索器，提供统一查询接口。

调用方：课程规划 Agent、资源推荐 Agent、报告生成 Agent、兜底问答 Handler。
"""

import logging
from typing import List, Dict, Optional

from src.rag.retriever import Retriever

logger = logging.getLogger(__name__)

# 单例
_rag_toolset: Optional["RAGToolset"] = None


class RAGToolset:
    """
    RAG 检索工具集（单例）。

    用法:
        rag = get_rag_toolset()
        results = rag.search_courses("金融学基础", program="金融辅修")
    """

    def __init__(self):
        self._retriever = Retriever()

    # ── 通用检索 ──────────────────────────────────────

    def search_knowledge(self, query: str, top_k: int = 5,
                         source_filter: str = None) -> List[dict]:
        """
        通用知识库检索，可选按 source 路径前缀过滤。

        source_filter: "courses/" / "programs/" / "resources/" / "rules/"
        """
        if not query or not isinstance(query, str):
            return []
        try:
            results = self._retriever.search(query, top_k=top_k * 2)
            if source_filter:
                results = [
                    r for r in results
                    if r.get("metadata", {}).get("source", "").startswith(source_filter)
                ]
            return results[:top_k]
        except Exception as e:
            logger.error("search_knowledge failed: %s", e)
            return []

    # ── 定向检索 ──────────────────────────────────────

    def search_courses(self, query: str, program: str = None,
                       top_k: int = 5) -> List[dict]:
        """
        仅检索课程内容（courses/ 目录），可选按专业过滤。
        """
        if not query:
            return []
        try:
            return self._retriever.search_courses(query, program=program, top_k=top_k)
        except Exception as e:
            logger.error("search_courses failed: %s", e)
            return []

    def search_resources(self, query: str, resource_type: str = None,
                         course_code: str = None, top_k: int = 5) -> List[dict]:
        """
        仅检索学习资源（resources/ 目录）。

        resource_type: "exercise" / "note" / "video"（可选）
        course_code: 精确课程代码过滤（可选）
        """
        if not query:
            return []
        try:
            return self._retriever.search_resources(
                query, resource_type=resource_type,
                course_code=course_code, top_k=top_k,
            )
        except Exception as e:
            logger.error("search_resources failed: %s", e)
            return []

    def search_rules(self, query: str, top_k: int = 3) -> List[dict]:
        """仅检索规则文件（rules/ 目录）。"""
        if not query:
            return []
        try:
            return self._retriever.search_rules(query, top_k=top_k)
        except Exception as e:
            logger.error("search_rules failed: %s", e)
            return []

    def search_by_course(self, course_code: str) -> List[dict]:
        """
        精确检索某门课程的全部相关内容（跨所有目录）。
        """
        if not course_code or not isinstance(course_code, str):
            return []
        try:
            return self._retriever.search_by_course_code(course_code)
        except Exception as e:
            logger.error("search_by_course(%s) failed: %s", course_code, e)
            return []

    def search_programs(self, query: str, top_k: int = 3) -> List[dict]:
        """检索辅修专业简介。"""
        if not query:
            return []
        try:
            return self._retriever.search_programs(query, top_k=top_k)
        except Exception as e:
            logger.error("search_programs failed: %s", e)
            return []


def get_rag_toolset() -> RAGToolset:
    """获取 RAGToolset 单例。"""
    global _rag_toolset
    if _rag_toolset is None:
        _rag_toolset = RAGToolset()
    return _rag_toolset
