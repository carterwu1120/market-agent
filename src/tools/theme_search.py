"""主題/題材股票搜尋：透過新聞關鍵字動態找出相關個股。

流程：
1. 鉅亨網全文搜尋 API（精確）+ UDN 搜尋頁面（補充）
2. 從新聞內文抽取 XXXX-TW 格式的股票代碼
3. 按出現頻率排序，取前 N 檔
4. 同時回傳相關新聞供 synthesizer 參考
"""

from __future__ import annotations
import asyncio
import re
from collections import Counter
from typing import Any

import httpx
from bs4 import BeautifulSoup
from loguru import logger

CNYES_SEARCH_API = "https://api.cnyes.com/media/api/v1/search"
UDN_SEARCH_URL = "https://money.udn.com/search/result/1001/{keyword}"

_CODE_PATTERNS = [
    re.compile(r'(\d{4})-TW'),
    re.compile(r'（(\d{4})）'),
    re.compile(r'\((\d{4})\)'),
]

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
}


def _extract_codes(text: str) -> list[str]:
    codes = []
    for pattern in _CODE_PATTERNS:
        for m in pattern.finditer(text):
            code = m.group(1)
            if 1000 <= int(code) <= 9999:
                codes.append(code)
    return codes


async def _fetch_cnyes(keyword: str, client: httpx.AsyncClient) -> tuple[list[dict], Counter]:
    """鉅亨網全文搜尋，回傳新聞和代碼計數。"""
    try:
        resp = await client.get(CNYES_SEARCH_API, params={"q": keyword, "limit": 20})
        resp.raise_for_status()
        articles = resp.json().get("items", {}).get("data", [])
    except Exception as exc:
        logger.warning(f"ThemeSearch cnyes failed '{keyword}': {exc}")
        return [], Counter()

    counter: Counter = Counter()
    news_items = []
    for art in articles:
        title = art.get("title", "")
        content = art.get("content", "")
        counter.update(_extract_codes(title + content))
        news_items.append({
            "title": re.sub(r'<[^>]+>', '', title),
            "source_name": "鉅亨網",
            "source_url": f"https://news.cnyes.com/news/id/{art.get('newsId', '')}",
            "published_at": art.get("publishAt", ""),
            "content": re.sub(r'<[^>]+>', '', content)[:500],
        })

    return news_items, counter


async def _fetch_udn(keyword: str, client: httpx.AsyncClient) -> tuple[list[dict], Counter]:
    """UDN 經濟日報搜尋，補充更多新聞和代碼。"""
    try:
        resp = await client.get(UDN_SEARCH_URL.format(keyword=keyword))
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception as exc:
        logger.warning(f"ThemeSearch UDN failed '{keyword}': {exc}")
        return [], Counter()

    counter: Counter = Counter()
    news_items = []

    # UDN 搜尋結果的新聞標題
    for item in soup.find_all("h2"):
        title = item.get_text(strip=True)
        if title and len(title) > 5:
            counter.update(_extract_codes(title))
            news_items.append({
                "title": title,
                "source_name": "經濟日報",
                "source_url": "",
                "published_at": "",
                "content": "",
            })

    # 從整頁文字也補充抓代碼
    page_text = soup.get_text()
    counter.update(_extract_codes(page_text))

    return news_items, counter


async def search_theme_stocks(
    keyword: str,
    max_symbols: int = 10,
) -> dict[str, Any]:
    """
    給定主題關鍵字，從新聞中動態找出相關個股。

    Returns:
        {
            "keyword": "機器人概念股",
            "symbols": ["2049.TW", "2049.TW", ...],  # 按出現頻率排序
            "code_frequency": {"2049": 5, ...},
            "articles": [...],
            "total_articles": int,
            "source": "cnyes+udn",
        }
    """
    async with httpx.AsyncClient(timeout=20, headers=_HEADERS, follow_redirects=True) as client:
        cnyes_news, cnyes_counter = await _fetch_cnyes(keyword, client)
        await asyncio.sleep(0.5)  # polite delay
        udn_news, udn_counter = await _fetch_udn(keyword, client)

    # 合併計數
    total_counter = cnyes_counter + udn_counter
    top_codes = [code for code, _ in total_counter.most_common(max_symbols)]
    symbols = [f"{code}.TW" for code in top_codes]

    all_articles = cnyes_news + udn_news

    logger.info(
        f"ThemeSearch '{keyword}': {len(all_articles)} articles, "
        f"{len(total_counter)} unique codes → top {len(symbols)}: {symbols}"
    )

    return {
        "keyword": keyword,
        "symbols": symbols,
        "code_frequency": dict(total_counter.most_common(max_symbols)),
        "articles": all_articles,
        "total_articles": len(all_articles),
        "source": "cnyes_search+udn",
    }
