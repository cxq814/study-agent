"""
文件导出工具 — 报告内容导出为 md / txt / json 文件。

仅实现三种文本格式生成，PDF 在后续 Streamlit 前端环节额外实现。
"""

import json
import logging
import os
from datetime import datetime

from config.settings import PROJECT_ROOT

logger = logging.getLogger(__name__)

# 导出目录
EXPORT_DIR = os.path.join(PROJECT_ROOT, "data", "exports")


def _ensure_export_dir() -> str:
    """确保导出目录存在，返回绝对路径。"""
    os.makedirs(EXPORT_DIR, exist_ok=True)
    return EXPORT_DIR


def _sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符。"""
    return "".join(c for c in name if c.isalnum() or c in "._- ()（）")


def _build_filename(prefix: str, ext: str) -> str:
    """构建带时间戳的文件名。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_prefix = _sanitize_filename(prefix)
    return f"{safe_prefix}_{ts}.{ext}"


# ══════════════════════════════════════════════════════
#  导出方法
# ══════════════════════════════════════════════════════

def export_markdown(report_content: str, filename: str = None) -> str:
    """
    导出 .md 文件。

    参数:
        report_content: Markdown 正文
        filename: 自定义文件名（不含扩展名），默认 "report"
    返回: 文件绝对路径，失败返回空字符串
    """
    if not report_content or not isinstance(report_content, str):
        logger.warning("export_markdown: content is empty")
        return ""

    try:
        _ensure_export_dir()
        name = filename or "report"
        filepath = os.path.join(EXPORT_DIR, _build_filename(name, "md"))
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(report_content)
        logger.info("Markdown exported: %s", filepath)
        return filepath
    except Exception as e:
        logger.error("export_markdown failed: %s", e)
        return ""


def export_text(report_content: str, filename: str = None) -> str:
    """
    导出纯文本 .txt 文件。

    参数:
        report_content: 文本正文
        filename: 自定义文件名（不含扩展名），默认 "report"
    返回: 文件绝对路径，失败返回空字符串
    """
    if not report_content or not isinstance(report_content, str):
        logger.warning("export_text: content is empty")
        return ""

    try:
        _ensure_export_dir()
        name = filename or "report"
        filepath = os.path.join(EXPORT_DIR, _build_filename(name, "txt"))
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(report_content)
        logger.info("Text exported: %s", filepath)
        return filepath
    except Exception as e:
        logger.error("export_text failed: %s", e)
        return ""


def export_json_summary(report_data: dict, filename: str = None) -> str:
    """
    导出结构化 JSON 摘要。

    参数:
        report_data: 可序列化的字典
        filename: 自定义文件名（不含扩展名），默认 "report_summary"
    返回: 文件绝对路径，失败返回空字符串
    """
    if not isinstance(report_data, dict):
        logger.warning("export_json_summary: data must be dict")
        return ""

    try:
        _ensure_export_dir()
        name = filename or "report_summary"
        filepath = os.path.join(EXPORT_DIR, _build_filename(name, "json"))
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2, default=str)
        logger.info("JSON summary exported: %s", filepath)
        return filepath
    except Exception as e:
        logger.error("export_json_summary failed: %s", e)
        return ""
