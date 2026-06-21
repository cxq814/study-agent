"""
数据清洗与存储 — 去重、课程匹配、写入 JSON。
"""

import json
import os
import hashlib
import logging
from datetime import datetime
from typing import List, Dict

from config.settings import SCRAPED_DATA_DIR

logger = logging.getLogger(__name__)

# 课程代码关键词映射（用于自动匹配爬取内容到课程）
COURSE_KEYWORD_MAP = {
    "FIN101": ["金融学基础", "金融学", "货币银行", "金融市场"],
    "FIN201": ["公司金融", "企业财务", "资本结构", "NPV", "WACC"],
    "FIN301": ["投资学", "资产定价", "投资组合", "证券投资"],
    "DS101": ["数据科学导论", "数据科学", "Python 数据处理", "统计分析"],
    "DS201": ["机器学习", "监督学习", "无监督学习", "深度学习"],
    "LAW101": ["法理学", "法的概念", "法律基础", "法治"],
    "LAW201": ["民商法", "合同法", "侵权责任法", "民法"],
}


def _generate_id(item: dict) -> str:
    """根据标题 + 来源生成唯一 ID。"""
    raw = f"{item.get('title', '')}|{item.get('source', '')}|{item.get('url', '')}"
    return hashlib.md5(raw.encode()).hexdigest()


def _match_courses(item: dict) -> List[str]:
    """根据 item 内容和关键词匹配课程代码。"""
    text = (item.get("title", "") + " " + item.get("description", "") +
            " " + " ".join(item.get("keywords", [])))
    matched = []
    for code, kws in COURSE_KEYWORD_MAP.items():
        if any(kw in text for kw in kws):
            matched.append(code)
    return matched


def _deduplicate(items: List[dict]) -> List[dict]:
    """按 ID 去重，保留首次出现。"""
    seen = set()
    result = []
    for item in items:
        item_id = _generate_id(item)
        if item_id not in seen:
            seen.add(item_id)
            item["id"] = item_id
            result.append(item)
    return result


def save_items(items: List[dict]) -> int:
    """
    保存爬取结果到 data/scraped/。

    每批爬取存一个带时间戳的 JSON 文件。
    返回保存的 item 数量。
    """
    if not items:
        return 0

    os.makedirs(SCRAPED_DATA_DIR, exist_ok=True)

    # 去重 + 课程匹配
    items = _deduplicate(items)
    for item in items:
        item["matched_courses"] = _match_courses(item)
        item["scraped_at"] = datetime.now().isoformat()

    # 写入文件
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(SCRAPED_DATA_DIR, f"scraped_{timestamp}.json")

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    logger.info("Saved %d items to %s", len(items), filepath)
    return len(items)


def load_all_scraped() -> List[dict]:
    """加载所有已爬取的数据。"""
    if not os.path.exists(SCRAPED_DATA_DIR):
        return []

    all_items = []
    for filename in sorted(os.listdir(SCRAPED_DATA_DIR)):
        if filename.endswith(".json"):
            filepath = os.path.join(SCRAPED_DATA_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    items = json.load(f)
                    all_items.extend(items)
            except Exception as e:
                logger.warning("Failed to load %s: %s", filepath, e)

    return _deduplicate(all_items)


def search_scraped(keywords: List[str], top_k: int = 5) -> List[dict]:
    """按关键词搜索已爬取数据（简单关键词匹配，不需要 ChromaDB）。"""
    all_items = load_all_scraped()
    if not keywords:
        return all_items[:top_k]

    scored = []
    for item in all_items:
        text = (item.get("title", "") + " " + item.get("description", "") +
                " " + " ".join(item.get("keywords", [])) +
                " " + " ".join(item.get("matched_courses", [])))
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]
