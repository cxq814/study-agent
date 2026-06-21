"""
Redis 连接封装与会话状态读写。

职责：
    1. 管理 Redis 连接（支持无 Redis 时的优雅降级）
    2. Session 状态的读 / 写 / 过期刷新
    3. 对话历史的追加与截断
    4. RAG 检索缓存

Key 设计：
    session:{user_id}:{session_id}  → Hash   会话状态
    chat:{user_id}                  → List   最近对话历史
    rag_cache:{query_hash}          → String 检索结果缓存
"""

import json
import hashlib
import logging
from typing import Optional, Dict, Any, List

import redis

from config.settings import REDIS_URL, SESSION_TTL, RAG_CACHE_TTL

logger = logging.getLogger(__name__)

# ── 连接管理（懒加载 + 容错）─────────────────────────

_redis_client: Optional[redis.Redis] = None
_redis_available: Optional[bool] = None

# 内存回退存储 —— 当 Redis 不可用时，用内存字典兜底，
# 保证同一个进程内的多轮对话状态不会丢失。
# key 格式同 Redis: "session:{user_id}:{session_id}" → 映射 dict
_memory_fallback: Dict[str, Dict[str, Any]] = {}


def get_redis() -> Optional[redis.Redis]:
    """
    获取 Redis 连接。
    如果 Redis 不可用，返回 None（调用方需做 None 检查）。
    """
    global _redis_client, _redis_available

    if _redis_available is False:
        return None

    if _redis_client is not None:
        try:
            _redis_client.ping()
            return _redis_client
        except Exception:
            _redis_available = False
            _redis_client = None
            return None

    try:
        _redis_client = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=2)
        _redis_client.ping()
        _redis_available = True
        logger.info(f"Redis connected: {REDIS_URL}")
        return _redis_client
    except Exception as e:
        _redis_available = False
        _redis_client = None
        logger.warning(f"Redis unavailable ({e}) — session will use SQLite fallback")
        return None


def is_redis_available() -> bool:
    """检查 Redis 是否可用。"""
    return get_redis() is not None


# ── 会话状态读写 ─────────────────────────────────────

def _session_key(user_id: str, session_id: str) -> str:
    return f"session:{user_id}:{session_id}"


def save_session_state(user_id: str, session_id: str, state: Dict[str, Any]):
    """
    将会话状态写入 Redis Hash（不可用时回退到内存字典）。
    自动序列化 list/dict 字段为 JSON 字符串。
    """
    r = get_redis()
    key = _session_key(user_id, session_id)

    # 先序列化
    serialized = {}
    for k, v in state.items():
        if v is None:
            continue
        if isinstance(v, (list, dict)):
            serialized[k] = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, bool):
            serialized[k] = "1" if v else "0"
        else:
            serialized[k] = str(v)

    if r is not None:
        r.hset(key, mapping=serialized)
        r.expire(key, SESSION_TTL)
    else:
        # Redis 不可用 → 内存回退（同类 key 命名，方便统一管理）
        _memory_fallback[key] = serialized
        logger.debug(f"Session saved to memory fallback: {key}")


def load_session_state(user_id: str, session_id: str) -> Dict[str, Any]:
    """
    从 Redis Hash 读取会话状态（不可用时从内存字典回退）。
    自动反序列化 JSON 字符串。
    """
    r = get_redis()
    key = _session_key(user_id, session_id)

    if r is not None:
        # ── 优先 Redis ──
        raw = r.hgetall(key)
        if raw:
            # 刷新 TTL
            r.expire(key, SESSION_TTL)
        else:
            return {}
    else:
        # ── Redis 不可用 → 内存回退 ──
        raw = _memory_fallback.get(key, {})
        if not raw:
            return {}

    result = {}
    for k, v in raw.items():
        k_str = k.decode("utf-8") if isinstance(k, bytes) else k
        v_str = v.decode("utf-8") if isinstance(v, bytes) else v

        # 尝试解析 JSON
        if v_str.startswith("[") or v_str.startswith("{"):
            try:
                result[k_str] = json.loads(v_str)
                continue
            except json.JSONDecodeError:
                pass

        # 布尔值
        if v_str in ("1", "0") and k_str.startswith("has_"):
            result[k_str] = v_str == "1"
            continue

        result[k_str] = v_str

    return result


def delete_session(user_id: str, session_id: str):
    """清除会话状态（Redis + 内存回退）。"""
    r = get_redis()
    key = _session_key(user_id, session_id)
    if r is not None:
        r.delete(key)
    # 同时清理内存回退
    _memory_fallback.pop(key, None)


# ── 对话历史 ─────────────────────────────────────────

def _chat_key(user_id: str) -> str:
    return f"chat:{user_id}"

MAX_CHAT_HISTORY = 20  # 最多保留 20 条对话


def append_chat_message(user_id: str, role: str, content: str):
    """追加一条对话消息到 Redis List。"""
    r = get_redis()
    if r is None:
        return

    key = _chat_key(user_id)
    msg = json.dumps({"role": role, "content": content}, ensure_ascii=False)
    r.rpush(key, msg)
    # 超出上限时裁剪旧消息
    length = r.llen(key)
    if length > MAX_CHAT_HISTORY:
        r.ltrim(key, length - MAX_CHAT_HISTORY, -1)
    r.expire(key, 86400)  # 24h


def get_chat_history(user_id: str, limit: int = 10) -> List[Dict[str, str]]:
    """获取最近 N 条对话历史。"""
    r = get_redis()
    if r is None:
        return []

    key = _chat_key(user_id)
    raw = r.lrange(key, -limit, -1)
    messages = []
    for item in raw:
        try:
            item_str = item.decode("utf-8") if isinstance(item, bytes) else item
            messages.append(json.loads(item_str))
        except json.JSONDecodeError:
            continue
    return messages


# ── RAG 检索缓存 ─────────────────────────────────────

# RAG 缓存内存回退（Redis 不可用时启用）
_rag_cache_fallback: Dict[str, str] = {}

def _rag_cache_key(query: str) -> str:
    """为检索查询生成缓存键（基于内容哈希）。"""
    h = hashlib.md5(query.encode("utf-8")).hexdigest()
    return f"rag_cache:{h}"


def get_rag_cache(query: str) -> Optional[str]:
    """读取 RAG 检索缓存（Redis 优先，内存回退）。"""
    r = get_redis()
    key = _rag_cache_key(query)

    if r is not None:
        result = r.get(key)
        if result:
            return result.decode("utf-8") if isinstance(result, bytes) else result

    # Redis 不可用 → 内存回退
    return _rag_cache_fallback.get(key)


def set_rag_cache(query: str, result: str):
    """写入 RAG 检索缓存（Redis 优先 + TTL，内存回退）。"""
    r = get_redis()
    key = _rag_cache_key(query)

    if r is not None:
        r.setex(key, RAG_CACHE_TTL, result)
    else:
        # Redis 不可用 → 内存回退
        _rag_cache_fallback[key] = result
        # 限制内存回退大小（最多缓存 200 条）
        if len(_rag_cache_fallback) > 200:
            # 删除最早的 50 条
            oldest = list(_rag_cache_fallback.keys())[:50]
            for k in oldest:
                _rag_cache_fallback.pop(k, None)
        logger.debug(f"RAG cache saved to memory fallback: {key}")


# ── 便捷方法：从 Redis 冷启动 / 回填 SQLite ──────────

def restore_or_init_session(user_id: str, session_id: str,
                            sqlite_loader) -> Dict[str, Any]:
    """
    尝试从 Redis 恢复会话；若不命中，则用 sqlite_loader 从 SQLite 冷启动。
    sqlite_loader: callable(user_id) -> Dict[str, Any]
    """
    state = load_session_state(user_id, session_id)
    if state:
        return state

    # 冷启动：从 SQLite 加载用户信息
    user_data = sqlite_loader(user_id)
    state = {
        "user_id": user_id,
        "session_id": session_id,
        "conversation_phase": "idle",
        "has_minor_course": user_data.get("has_minor", 0) == 1 if user_data else False,
        "has_major_timetable": True,  # 简化：假设已录入
        "current_recommendations": [],
        "current_conflicts": [],
        "current_resources": [],
        "messages": [],
    }

    # 回填 Redis
    save_session_state(user_id, session_id, state)
    return state
