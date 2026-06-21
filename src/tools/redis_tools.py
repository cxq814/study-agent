"""
Redis 会话 & 缓存工具 — 封装 redis_client.py，加异常保护。

调用方：路由 Agent、所有多轮对话 Agent。
"""

import logging
from typing import Optional, List, Dict

from src.storage.redis_client import (
    save_session_state, load_session_state, delete_session,
    get_rag_cache, set_rag_cache,
    append_chat_message, get_chat_history,
    is_redis_available,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
#  会话状态
# ══════════════════════════════════════════════════════

def tool_save_session(user_id: str, session_id: str,
                       state_dict: dict) -> bool:
    """
    持久化会话状态到 Redis/内存。

    返回 True 表示写入成功（降级到内存也视为成功）。
    """
    if not user_id or not session_id:
        return False
    if not isinstance(state_dict, dict):
        logger.warning("state_dict must be dict")
        return False
    try:
        save_session_state(user_id, session_id, state_dict)
        return True
    except Exception as e:
        logger.error("tool_save_session failed: %s", e)
        return False


def tool_load_session(user_id: str, session_id: str) -> dict:
    """
    从 Redis/内存读取会话状态。

    无数据时返回空 dict。
    """
    if not user_id or not session_id:
        return {}
    try:
        return load_session_state(user_id, session_id)
    except Exception as e:
        logger.warning("tool_load_session failed: %s", e)
        return {}


def tool_clear_session(user_id: str, session_id: str) -> bool:
    """清除会话状态。"""
    if not user_id or not session_id:
        return False
    try:
        delete_session(user_id, session_id)
        return True
    except Exception as e:
        logger.error("tool_clear_session failed: %s", e)
        return False


# ══════════════════════════════════════════════════════
#  RAG 缓存
# ══════════════════════════════════════════════════════

def tool_get_rag_cache(query: str) -> Optional[str]:
    """读取 RAG 检索缓存。未命中返回 None。"""
    if not query or not isinstance(query, str):
        return None
    try:
        return get_rag_cache(query)
    except Exception as e:
        logger.warning("tool_get_rag_cache failed: %s", e)
        return None


def tool_set_rag_cache(query: str, result: str) -> bool:
    """写入 RAG 检索缓存。"""
    if not query or not result:
        return False
    try:
        set_rag_cache(query, result)
        return True
    except Exception as e:
        logger.warning("tool_set_rag_cache failed: %s", e)
        return False


# ══════════════════════════════════════════════════════
#  对话历史
# ══════════════════════════════════════════════════════

def tool_append_chat(user_id: str, role: str, content: str) -> bool:
    """追加一条对话消息。"""
    if not user_id or not role or not content:
        return False
    try:
        append_chat_message(user_id, role, content)
        return True
    except Exception as e:
        logger.warning("tool_append_chat failed: %s", e)
        return False


def tool_get_chat_history(user_id: str, limit: int = 10) -> list:
    """获取最近 N 条对话历史。"""
    if not user_id:
        return []
    try:
        return get_chat_history(user_id, limit=min(limit, 50))
    except Exception as e:
        logger.warning("tool_get_chat_history failed: %s", e)
        return []


# ══════════════════════════════════════════════════════
#  连接状态
# ══════════════════════════════════════════════════════

def tool_redis_status() -> bool:
    """检查 Redis 是否可用。"""
    try:
        return is_redis_available()
    except Exception:
        return False
