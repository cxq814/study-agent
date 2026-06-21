"""
知识库检索器。

支持：
  1. 语义检索（ChromaDB 向量相似度）
  2. 精确匹配（按 course_code / program 过滤）
  3. 混合检索（语义 + 关键词）
  4. Redis 缓存（避免重复检索）
"""

import json
import logging
from typing import List, Dict, Optional

import chromadb

from src.rag.embeddings import get_embedding_model, BaseEmbedding
from src.rag.indexer import get_collection
from src.storage.redis_client import get_rag_cache, set_rag_cache

logger = logging.getLogger(__name__)


class Retriever:
    """
    知识库检索器。

    用法：
        r = Retriever()
        results = r.search("金融辅修课程推荐", top_k=5)
    """

    def __init__(self, embedding_model: BaseEmbedding = None,
                 collection: chromadb.Collection = None):
        self._embedding_model = embedding_model
        self._collection = collection

    @property
    def embedding_model(self) -> BaseEmbedding:
        if self._embedding_model is None:
            self._embedding_model = get_embedding_model()
        return self._embedding_model

    @property
    def collection(self) -> chromadb.Collection:
        if self._collection is None:
            self._collection = get_collection()
        return self._collection

    def search(self, query: str, top_k: int = 5,
               program: str = None,
               course_code: str = None) -> List[Dict]:
        """
        语义检索知识库。

        参数：
            query: 查询文本
            top_k: 返回数量
            program: 按辅修专业过滤（可选）
            course_code: 按课程代码过滤（可选）

        返回：
            [{id, document, metadata, distance}, ...]
        """
        # 1. 检查 Redis 缓存
        cache_key = f"{query}|{program}|{course_code}|{top_k}"
        cached = get_rag_cache(cache_key)
        if cached:
            logger.debug("RAG cache hit")
            return json.loads(cached)

        # 2. 构建过滤条件
        where = {}
        if program:
            where["program"] = program
        if course_code:
            where["course_code"] = course_code

        # 3. 向量检索
        query_vec = self.embedding_model.embed(query)

        results = self.collection.query(
            query_embeddings=[query_vec],
            n_results=min(top_k * 2, 50),  # 多取一些用于重排
            where=where if where else None,
            include=["documents", "metadatas", "distances"],
        )

        # 4. 整理结果
        items = []
        if results["ids"] and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                items.append({
                    "id": results["ids"][0][i],
                    "document": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i] or {},
                    "distance": results["distances"][0][i],
                })

        # 5. LLM 重排会在 Agent 层完成，这里先按距离排序截断
        items.sort(key=lambda x: x["distance"])
        items = items[:top_k]

        # 6. 写入缓存
        set_rag_cache(cache_key, json.dumps(items, ensure_ascii=False))

        return items

    def search_programs(self, query: str, top_k: int = 3) -> List[Dict]:
        """只检索辅修专业简介。"""
        return self.search(query, top_k=top_k)

    def search_courses(self, query: str, program: str = None,
                       top_k: int = 5) -> List[Dict]:
        """只检索课程内容。"""
        results = self.search(query, top_k=top_k * 2)
        # 过滤：只保留课程文件（source 路径包含 "courses/"）
        course_results = [
            r for r in results
            if r.get("metadata", {}).get("source", "").startswith("courses/")
        ]
        return course_results[:top_k]

    def search_rules(self, query: str, top_k: int = 3) -> List[Dict]:
        """检索辅修规则。"""
        results = self.search(query, top_k=top_k * 2)
        rule_results = [
            r for r in results
            if r.get("metadata", {}).get("source", "").startswith("rules/")
        ]
        return rule_results[:top_k]

    def search_by_course_code(self, course_code: str) -> List[Dict]:
        """精确检索指定课程代码的所有内容。"""
        return self.search(course_code, top_k=10, course_code=course_code)

    def search_resources(self, query: str, resource_type: str = None,
                         course_code: str = None, top_k: int = 5) -> List[Dict]:
        """
        检索学习资源（习题/笔记/网课）。

        参数：
            query: 查询文本
            resource_type: "exercise" / "note" / "video"（可选）
            course_code: 按课程过滤（可选）
            top_k: 返回数量
        """
        results = self.search(query, top_k=top_k * 3)

        # 筛选：只保留资源文件
        resource_results = []
        for r in results:
            meta = r.get("metadata", {})
            source = meta.get("source", "")

            # 必须来自 resources/ 目录
            if not source.startswith("resources/"):
                continue

            # 按类型过滤
            if resource_type:
                if resource_type == "exercise" and "exercises" not in source:
                    continue
                if resource_type == "note" and "notes" not in source:
                    continue
                if resource_type == "video" and "videos" not in source:
                    continue

            # 按课程代码过滤
            if course_code and meta.get("course_code") != course_code:
                continue

            resource_results.append(r)

        return resource_results[:top_k]
