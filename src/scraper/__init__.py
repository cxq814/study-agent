"""
爬虫模块 — 爬取公开课程资源，补充本地知识库。

用法:
    from src.scraper import run_scraper
    run_scraper()  # 执行一次全量爬取

注意：fetcher/parser 依赖 bs4 + requests，采用懒加载避免导入时因缺少依赖
导致整个包不可用（search_scraped 不需要这些依赖）。
"""


def run_scraper() -> dict:
    """
    执行一次全量爬取。

    返回: {"total": N, "saved": N, "errors": [...]}
    """
    # 懒加载：仅爬虫运行时才需要 bs4/requests
    from src.scraper.fetcher import Fetcher
    from src.scraper.parser import parse_response
    from src.scraper.pipelines import save_items
    from src.scraper.targets import SCRAPE_TARGETS

    fetcher = Fetcher()
    result = {"total": 0, "saved": 0, "errors": []}

    for target in SCRAPE_TARGETS:
        try:
            html = fetcher.fetch(target["url"])
            items = parse_response(html, target)
            result["total"] += len(items)
            saved = save_items(items)
            result["saved"] += saved
        except Exception as e:
            result["errors"].append(f"{target['name']}: {e}")

    return result
