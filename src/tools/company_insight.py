"""公司獨家技術與法說會資訊：cnyes + DuckDuckGo 雙源搜尋，讓 LLM 摘要技術亮點。"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote_plus

import httpx
import yfinance as yf
from loguru import logger

CNYES_SEARCH_API = "https://api.cnyes.com/media/api/v1/search"
DDG_URL = "https://html.duckduckgo.com/html/"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
}

_PRIORITY_KEYWORDS = ["法說", "技術", "產品", "專利", "研發", "新品", "訂單", "客戶", "獨家"]

# Ticker → Chinese company name for DDG query building
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


async def _fetch_ddg(query: str, client: httpx.AsyncClient) -> list[dict]:
    """DuckDuckGo HTML search — returns title + snippet + url."""
    try:
        resp = await client.post(
            DDG_URL,
            data={"q": query, "kl": "tw-tzh"},
            headers={**_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=True,
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning(f"DDG search failed: {exc}")
        return []

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "lxml")
    articles = []
    for result in soup.select(".result")[:8]:
        title_el = result.select_one(".result__title")
        snippet_el = result.select_one(".result__snippet")
        url_el = result.select_one(".result__url")
        title = title_el.get_text(strip=True) if title_el else ""
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        url = url_el.get_text(strip=True) if url_el else ""
        if title:
            articles.append({
                "title": title,
                "content": snippet,
                "url": url,
                "published_at": "",
                "source": "duckduckgo",
            })
    logger.info(f"DDG search '{query}': {len(articles)} results")
    return articles


async def get_company_insights(symbol: str, max_articles: int = 8) -> dict[str, Any]:
    code = symbol.replace(".TW", "").replace(".tw", "")
    company_name = await _get_company_name_async(symbol)
    name = _TICKER_NAMES.get(code) or company_name or code
    ddg_query = f"{name} 法說會 OR 技術 OR 新產品 2026"

    async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
        cnyes_articles, ddg_articles = await asyncio.gather(
            _fetch_cnyes(code, client),
            _fetch_ddg(ddg_query, client),
        )

    # Merge: cnyes first, then DDG; deduplicate by title
    seen_titles: set[str] = set()
    merged = []
    for a in cnyes_articles + ddg_articles:
        t = a["title"][:40]
        if t and t not in seen_titles:
            seen_titles.add(t)
            merged.append(a)

    # Priority sort: titles with tech/IR keywords first
    priority = [a for a in merged if _is_priority(a["title"])]
    others = [a for a in merged if not _is_priority(a["title"])]
    selected = (priority + others)[:max_articles]

    logger.info(f"CompanyInsight [{code}]: cnyes={len(cnyes_articles)} ddg={len(ddg_articles)} → {len(selected)} selected")
    return {
        "symbol": code,
        "company_name": company_name,
        "articles": selected,
        "source": "cnyes+duckduckgo",
    }


async def _get_company_name_async(symbol: str) -> str | None:
    try:
        info = await asyncio.to_thread(lambda: yf.Ticker(symbol).info)
        return info.get("longName") or info.get("shortName")
    except Exception:
        return None
