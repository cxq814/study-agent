"""
HTTP 请求封装 — UA 伪装 + 重试。
"""

import logging
import time
import requests

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

REQUEST_TIMEOUT = 15  # 秒
MAX_RETRIES = 2


class Fetcher:
    """HTTP 请求器。"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENTS[0],
            "Accept": "text/html,application/json,application/xhtml+xml,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Referer": "https://www.bilibili.com/",
        })

    def fetch(self, url: str) -> str:
        """
        发起 GET 请求，返回响应文本。

        自动重试，失败抛异常。
        """
        last_error = None

        for attempt in range(1 + MAX_RETRIES):
            try:
                logger.info("Fetching %s (attempt %d)", url[:80], attempt + 1)
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                return resp.text
            except requests.RequestException as e:
                last_error = e
                logger.warning("Fetch failed (attempt %d): %s", attempt + 1, e)
                if attempt < MAX_RETRIES:
                    time.sleep(2 * (attempt + 1))

        raise RuntimeError(f"Failed to fetch {url[:80]} after {MAX_RETRIES + 1} attempts: {last_error}")
