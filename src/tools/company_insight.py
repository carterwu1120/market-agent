"""公司獨家技術與法說會資訊：從鉅亨網搜尋個股相關新聞，讓 LLM 摘要出技術亮點。

使用鉅亨網 search API，針對股票代碼 + 公司名稱搜尋法說會、技術突破、產品新聞。
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import yfinance as yf
from loguru import logger

CNYES_SEARCH_API = "https://api.cnyes.com/media/api/v1/search"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
}

# 優先抓法說會、技術、產品相關新聞的關鍵詞
_PRIORITY_KEYWORDS = ["法說", "技術", "產品", "專利", "研發", "新品", "訂單", "客戶", "獨家"]


def _get_company_name(symbol: str) -> str | None:
    """用 yfinance 取公司中文/英文名稱。"""
    try:
        info = yf.Ticker(symbol).fast_info
        # fast_info 沒有 longName，改用 info
        full_info = yf.Ticker(symbol).info
        return full_info.get("longName") or full_info.get("shortName")
    except Exception:
        return None


def _is_priority(title: str) -> bool:
    return any(kw in title for kw in _PRIORITY_KEYWORDS)


async def get_company_insights(symbol: str, max_articles: int = 8) -> dict[str, Any]:
    """
    搜尋個股相關新聞，優先取法說會與技術面報導。

    Returns:
        {
            "symbol": "2049",
            "company_name": "上銀科技",
            "articles": [{"title": ..., "content": ..., "url": ..., "published_at": ...}],
            "source": "cnyes_search",
        }
    """
    code = symbol.replace(".TW", "").replace(".tw", "")

    # 取公司名稱作為搜尋關鍵字（優先用代碼，避免英文名稱造成 API 500）
    company_name = await _get_company_name_async(symbol)
    query = code  # cnyes search 用代碼最穩定

    try:
        async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
            resp = await client.get(CNYES_SEARCH_API, params={"q": query, "limit": 30})
            resp.raise_for_status()
            articles_raw = resp.json().get("items", {}).get("data", [])
    except Exception as exc:
        logger.warning(f"CompanyInsight fetch failed [{symbol}]: {exc}")
        return {"symbol": code, "company_name": company_name, "articles": [], "source": "cnyes_search", "error": str(exc)}

    # 優先法說會/技術文章，其次取最新
    priority = [a for a in articles_raw if _is_priority(a.get("title", ""))]
    others = [a for a in articles_raw if not _is_priority(a.get("title", ""))]
    selected = (priority + others)[:max_articles]

    articles = []
    for a in selected:
        title = re.sub(r"<[^>]+>", "", a.get("title", ""))
        content = re.sub(r"<[^>]+>", "", a.get("content", ""))[:400]
        articles.append({
            "title": title,
            "content": content,
            "url": f"https://news.cnyes.com/news/id/{a.get('newsId', '')}",
            "published_at": a.get("publishAt", ""),
        })

    logger.info(f"CompanyInsight [{code}]: {len(priority)} priority + {len(others)} others → {len(articles)} returned")
    return {
        "symbol": code,
        "company_name": company_name,
        "articles": articles,
        "source": "cnyes_search",
    }


async def _get_company_name_async(symbol: str) -> str | None:
    import asyncio
    try:
        info = await asyncio.to_thread(lambda: yf.Ticker(symbol).info)
        return info.get("longName") or info.get("shortName")
    except Exception:
        return None
