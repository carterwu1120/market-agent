"""社群訊號工具：PTT Stock 板、鉅亨論壇關鍵字搜尋.

用於抓取「公司搶到大訂單」、「法說會利多」等先行指標。
"""

from __future__ import annotations
import asyncio
import re
from datetime import datetime, timezone
from dataclasses import dataclass, field

import httpx
from bs4 import BeautifulSoup
from loguru import logger


@dataclass
class SocialPost:
    title: str
    content: str
    author: str
    url: str
    source: str
    published_at: datetime
    tickers: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


PTT_BASE = "https://www.ptt.cc"
PTT_STOCK_URL = f"{PTT_BASE}/bbs/Stock/index.html"

# Keywords that often precede significant price moves
SIGNAL_KEYWORDS = [
    "大單", "訂單", "法說", "轉單", "爆量", "外資大買", "投信連買",
    "獲利創高", "營收創高", "EPS", "漲停", "突破", "季線", "年線",
    "供應鏈", "蘋果概念", "AI概念", "半導體", "缺貨", "漲價",
]


def _extract_tickers(text: str) -> list[str]:
    """Extract Taiwan stock codes from text. e.g. '台積電(2330)' → ['2330.TW']"""
    codes = re.findall(r"[\(（](\d{4,6})[\)）]", text)
    return [f"{c}.TW" for c in codes if len(c) == 4]


async def fetch_ptt_stock(max_pages: int = 3) -> list[SocialPost]:
    """Fetch recent posts from PTT Stock board."""
    posts = []
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Cookie": "over18=1",
    }

    async with httpx.AsyncClient(timeout=20, headers=headers, base_url=PTT_BASE) as client:
        url = "/bbs/Stock/index.html"
        for _ in range(max_pages):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")
            except Exception as exc:
                logger.warning(f"PTT fetch failed: {exc}")
                break

            for div in soup.select("div.r-ent"):
                title_tag = div.select_one("div.title a")
                if not title_tag:
                    continue
                title = title_tag.get_text(strip=True)
                post_url = PTT_BASE + title_tag["href"]
                tickers = _extract_tickers(title)
                found_keywords = [kw for kw in SIGNAL_KEYWORDS if kw in title]

                posts.append(SocialPost(
                    title=title,
                    content="",  # fetch on demand to avoid rate-limiting
                    author=div.select_one("div.author").get_text(strip=True) if div.select_one("div.author") else "",
                    url=post_url,
                    source="PTT Stock",
                    published_at=datetime.now(timezone.utc),
                    tickers=tickers,
                    keywords=found_keywords,
                ))

            # Navigate to previous page
            prev_link = soup.select_one("a.btn.wide:-soup-contains('上頁')")
            if not prev_link:
                break
            url = prev_link["href"]
            await asyncio.sleep(0.5)

    logger.info(f"PTT: fetched {len(posts)} posts")
    return posts


async def fetch_post_content(post: SocialPost) -> SocialPost:
    """Fetch full content for a single PTT post."""
    headers = {"User-Agent": "Mozilla/5.0", "Cookie": "over18=1"}
    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        try:
            resp = await client.get(post.url)
            soup = BeautifulSoup(resp.text, "lxml")
            main_content = soup.select_one("div#main-content")
            if main_content:
                # Remove metadata tags
                for tag in main_content.select("div.article-metaline, div.article-metaline-right"):
                    tag.decompose()
                post.content = main_content.get_text("\n", strip=True)[:3000]
        except Exception as exc:
            logger.warning(f"PTT post content fetch failed: {exc}")
    return post


def filter_signal_posts(posts: list[SocialPost], min_keywords: int = 1) -> list[SocialPost]:
    """Return only posts that contain investment-relevant keywords."""
    return [p for p in posts if len(p.keywords) >= min_keywords or p.tickers]
