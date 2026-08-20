"""公司獨家技術與法說會資訊：cnyes + 開放網頁搜尋雙源，讓 LLM 摘要技術亮點。"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
import yfinance as yf
from loguru import logger

from src.tools.web_search import search_web

CNYES_SEARCH_API = "https://api.cnyes.com/media/api/v1/search"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
}

_PRIORITY_KEYWORDS = ["法說", "技術", "產品", "專利", "研發", "新品", "訂單", "客戶", "獨家"]

# Ticker → Chinese company name for web search query building
_TICKER_NAMES: dict[str, str] = {
    "2330": "台積電", "2454": "聯發科", "2317": "鴻海", "2308": "台達電",
    "2382": "廣達", "3231": "緯創", "3711": "日月光", "2303": "聯電",
    "2412": "中華電", "2881": "富邦金", "2882": "國泰金", "2884": "玉山金",
    "2049": "上銀", "2376": "技嘉", "2353": "宏碁", "2357": "華碩",
    "2327": "國巨", "2002": "中鋼", "2409": "友達", "2344": "華邦電",
    "2603": "長榮", "2313": "華通", "2312": "金寶",
}


def _is_priority(title: str) -> bool:
    return any(kw in title for kw in _PRIORITY_KEYWORDS)


async def _fetch_cnyes(code: str, client: httpx.AsyncClient) -> list[dict]:
    try:
        resp = await client.get(CNYES_SEARCH_API, params={"q": code, "limit": 30})
        resp.raise_for_status()
        raw = resp.json().get("items", {}).get("data", [])
        articles = []
        for a in raw:
            title = re.sub(r"<[^>]+>", "", a.get("title", ""))
            content = re.sub(r"<[^>]+>", "", a.get("content", ""))[:400]
            articles.append({
                "title": title,
                "content": content,
                "url": f"https://news.cnyes.com/news/id/{a.get('newsId', '')}",
                "published_at": a.get("publishAt", ""),
                "source": "cnyes",
            })
        return articles
    except Exception as exc:
        logger.warning(f"CompanyInsight cnyes failed [{code}]: {exc}")
        return []


async def get_company_insights(symbol: str, max_articles: int = 8) -> dict[str, Any]:
    code = symbol.replace(".TW", "").replace(".tw", "")
    company_name = await _get_company_name_async(symbol)
    name = _TICKER_NAMES.get(code) or company_name or code
    query = f"{name} 法說會 技術 新產品"

    async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
        cnyes_articles, web_results = await asyncio.gather(
            _fetch_cnyes(code, client),
            search_web(query, max_results=8),
            return_exceptions=True,
        )
    cnyes_articles = cnyes_articles if not isinstance(cnyes_articles, Exception) else []
    web_results = web_results if not isinstance(web_results, Exception) else []
    web_articles = [
        {"title": r["title"], "content": r["snippet"], "url": r["url"], "published_at": "", "source": "web_search"}
        for r in web_results
    ]

    # Merge: cnyes first, then web search; deduplicate by title
    seen_titles: set[str] = set()
    merged = []
    for a in cnyes_articles + web_articles:
        t = a["title"][:40]
        if t and t not in seen_titles:
            seen_titles.add(t)
            merged.append(a)

    # Priority sort: titles with tech/IR keywords first
    priority = [a for a in merged if _is_priority(a["title"])]
    others = [a for a in merged if not _is_priority(a["title"])]
    selected = (priority + others)[:max_articles]

    logger.info(f"CompanyInsight [{code}]: cnyes={len(cnyes_articles)} web={len(web_results)} → {len(selected)} selected")
    return {
        "symbol": code,
        "company_name": company_name,
        "articles": selected,
        "source": "cnyes+web_search",
    }


async def _get_company_name_async(symbol: str) -> str | None:
    try:
        info = await asyncio.to_thread(lambda: yf.Ticker(symbol).info)
        return info.get("longName") or info.get("shortName")
    except Exception:
        return None
