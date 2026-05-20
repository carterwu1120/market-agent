"""CMoney 股票討論區爬蟲：取得個股最新社群討論，讓 LLM 摘要出獨家技術優勢與投資人關注點。

來源：cmoney.tw/forum/stock/{code}（公開，Nuxt SSR，無需登入）
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from bs4 import BeautifulSoup
from loguru import logger

CMONEY_BASE = "https://www.cmoney.tw"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
}


async def get_forum_posts(symbol: str, max_posts: int = 10) -> dict[str, Any]:
    """
    爬取 CMoney 討論區最新貼文。

    Args:
        symbol: 股票代碼，如 "2049.TW" 或 "2049"
    Returns:
        {
            "symbol": "2049",
            "posts": [{"title": ..., "content": ..., "url": ...}, ...],
            "source": "cmoney_forum",
        }
    """
    code = symbol.replace(".TW", "").replace(".tw", "")
    url = f"{CMONEY_BASE}/forum/stock/{code}"

    try:
        async with httpx.AsyncClient(timeout=15, headers=_HEADERS, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
    except Exception as exc:
        logger.warning(f"CMoney forum fetch failed [{symbol}]: {exc}")
        return {"symbol": code, "posts": [], "source": "cmoney_forum", "error": str(exc)}

    soup = BeautifulSoup(r.text, "lxml")
    posts = []

    # 每篇貼文：標題在 <a href="/forum/post/...">，內文摘要在相鄰 <p> 或 <div>
    for a in soup.find_all("a", href=re.compile(r"^/forum/post/\d+")):
        title = a.get_text(strip=True)
        if not title or len(title) < 5:
            continue

        post_url = CMONEY_BASE + a["href"]

        # 嘗試取相鄰的內文摘要
        content = ""
        parent = a.find_parent()
        if parent:
            p = parent.find("p")
            if p:
                content = p.get_text(strip=True)[:300]

        posts.append({"title": title, "content": content, "url": post_url})
        if len(posts) >= max_posts:
            break

    logger.info(f"CMoney forum [{code}]: {len(posts)} posts fetched")
    return {
        "symbol": code,
        "posts": posts,
        "source": "cmoney_forum",
        "forum_url": url,
    }
