"""
全局基础工具 — 所有 Agent 共用。

提供用户信息读取、会话阶段管理、Markdown 表格渲染等通用能力。
"""

import logging
from typing import Optional

from src.storage.sqlite_client import get_user as _sqlite_get_user
from src.storage.redis_client import load_session_state, save_session_state

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
#  用户信息
# ══════════════════════════════════════════════════════

def get_current_user(user_id: str) -> Optional[dict]:
    """
    从 SQLite 获取用户档案。

    返回 dict 含 has_minor、minor_program 等字段；
    用户不存在时返回 None。
    """
    if not user_id or not isinstance(user_id, str):
        logger.warning("get_current_user: invalid user_id=%s", user_id)
        return None
    try:
        return _sqlite_get_user(user_id)
    except Exception as e:
        logger.error("get_current_user failed: %s", e)
        return None


# ══════════════════════════════════════════════════════
#  会话阶段
# ══════════════════════════════════════════════════════

def get_session_phase(user_id: str, session_id: str) -> str:
    """
    从 Redis/内存读取当前对话阶段。

    返回 ConversationPhase.value 字符串，默认 "idle"。
    """
    if not user_id or not session_id:
        return "idle"
    try:
        state = load_session_state(user_id, session_id)
        return state.get("conversation_phase", "idle")
    except Exception as e:
        logger.warning("get_session_phase failed: %s", e)
        return "idle"


def set_session_phase(user_id: str, session_id: str, phase: str) -> bool:
    """
    写入对话阶段到 Redis/内存。

    返回 True 表示写入成功，False 表示失败（降级场景仍视为成功）。
    """
    if not user_id or not session_id or not phase:
        return False
    try:
        save_session_state(user_id, session_id, {"conversation_phase": phase})
        return True
    except Exception as e:
        logger.warning("set_session_phase failed: %s", e)
        return False


# ══════════════════════════════════════════════════════
#  Markdown 渲染
# ══════════════════════════════════════════════════════

def format_markdown_table(headers: list, rows: list) -> str:
    """
    将表头 + 数据行渲染为 Markdown 表格字符串。

    例:
        headers = ["课程", "学分", "成绩"]
        rows = [["FIN101", "3", "88"], ["DS101", "3", "92"]]
        → "| 课程 | 学分 | 成绩 |\n|------|------|------|\n| FIN101 | 3 | 88 |\n| DS101 | 3 | 92 |"
    """
    if not headers:
        return ""
    try:
        header_line = "| " + " | ".join(str(h) for h in headers) + " |"
        sep_line = "|" + "|".join("------" for _ in headers) + "|"
        data_lines = []
        for row in (rows or []):
            data_lines.append("| " + " | ".join(str(c) for c in row) + " |")
        return "\n".join([header_line, sep_line] + data_lines)
    except Exception as e:
        logger.error("format_markdown_table failed: %s", e)
        return ""
