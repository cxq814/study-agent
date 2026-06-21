"""
HTML 解析器 — 从不同平台提取结构化数据。
"""

import logging
import re
import json
from typing import List, Dict
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def parse_response(html: str, target: dict) -> List[dict]:
    """
    根据 target 的 parser 类型分发解析器。

    返回: 结构化 item 列表
    """
    parser_type = target.get("parser", "generic")
    keywords = target.get("keywords", [])
    item_type = target.get("type", "article")

    if parser_type == "icourse163":
        items = _parse_icourse163(html, keywords)
    elif parser_type == "bilibili":
        items = _parse_bilibili(html, keywords)
    else:
        items = _parse_generic(html, target["url"], keywords)

    # 标注类型
    for item in items:
        item["type"] = item_type
        item["source"] = target["name"]
        item["url"] = item.get("url", target["url"])

    logger.info("Parsed %d items from %s", len(items), target["name"])
    return items


def _parse_icourse163(html: str, keywords: List[str]) -> List[dict]:
    """解析中国大学MOOC搜索结果页。"""
    items = []
    soup = BeautifulSoup(html, "html.parser")

    # 课程卡片
    for card in soup.select(".m-course-list .u-course-card, .course-card"):
        title_el = card.select_one(".course-name, .course-title, h4 a")
        desc_el = card.select_one(".course-desc, .description, .course-brief")
        link_el = card.select_one("a[href]") or (title_el if title_el and title_el.name == "a" else None)

        title = title_el.get_text(strip=True) if title_el else ""
        desc = desc_el.get_text(strip=True) if desc_el else ""
        link = link_el.get("href", "") if link_el else ""

        if link and not link.startswith("http"):
            link = "https://www.icourse163.org" + link

        if not title:
            continue

        # 关键词过滤
        if keywords and not any(kw in title + desc for kw in keywords):
            continue

        items.append({
            "title": title,
            "description": desc[:500] if desc else "",
            "url": link,
            "platform": "icourse163",
            "keywords": keywords,
        })

    # 如果 bs4 解析不到，用正则兜底
    if not items:
        items = _parse_generic(html, "", keywords)

    return items[:10]


def _parse_bilibili(html: str, keywords: List[str]) -> List[dict]:
    """解析 B站 API 返回的 JSON 结果。"""
    items = []

    try:
        data = json.loads(html)
        video_list = data.get("data", {}).get("result", [])
    except (json.JSONDecodeError, AttributeError):
        # 非 JSON，尝试从 HTML 提取
        return _parse_generic(html, "", keywords)

    for video in video_list:
        title = video.get("title", "").replace('<em class="keyword">', "").replace("</em>", "")
        desc = video.get("description", "")[:300]
        bvid = video.get("bvid", "")
        play = video.get("play", 0)
        url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""

        if not title:
            continue

        if keywords and not any(kw in title for kw in keywords):
            continue

        items.append({
            "title": title,
            "description": desc,
            "url": url,
            "platform": "bilibili",
            "extra": {"play_count": play},
            "keywords": keywords,
        })

    return items[:10]


def _parse_generic(html: str, url: str, keywords: List[str]) -> List[dict]:
    """通用解析器 — 提取页面中的标题和段落。"""
    items = []
    soup = BeautifulSoup(html, "html.parser")

    # 提取标题
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        title = tag.get_text(strip=True)
        if not title or len(title) < 4:
            continue
        if keywords and not any(kw in title for kw in keywords):
            continue

        # 获取后续段落作为描述
        desc_parts = []
        next_el = tag.find_next_sibling()
        for _ in range(3):
            if next_el and next_el.name in ("p", "div", "span"):
                text = next_el.get_text(strip=True)
                if text:
                    desc_parts.append(text[:300])
                next_el = next_el.find_next_sibling()
            else:
                break

        items.append({
            "title": title,
            "description": " ".join(desc_parts)[:500],
            "url": url,
            "platform": "generic",
            "keywords": keywords,
        })

    return items[:5] if items else [
        {
            "title": f"搜索结果: {', '.join(keywords[:3])}",
            "description": "（此页面未能提取结构化内容，建议人工访问查看）",
            "url": url,
            "platform": "generic",
            "keywords": keywords,
        }
    ]
