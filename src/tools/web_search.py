"""General-purpose web search via DuckDuckGo's HTML endpoint.

Free, no API key required — but same fragility class as this project's other
scrapers (見 README「已知的資料品質注意事項」): DuckDuckGo may rate-limit or
serve empty result pages to automated traffic, and its HTML markup can change
without notice. Results are supplementary background/event context, never a
source for prices or financial figures — those must come from the structured
tools in stock_data.py/chip_data.py/mops_data.py.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup
from loguru import logger

_DDG_URL = "https://html.duckduckgo.com/html/"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
}


def _resolve_url(title_el) -> str:
    """DDG wraps result links behind a redirect URL (//duckduckgo.com/l/?uddg=<encoded>);
    unwrap it to the real target. Falls back to "" if not found."""
    a = title_el.find("a") if title_el else None
    href = a.get("href", "") if a else ""
    if "uddg=" in href:
        try:
            return parse_qs(urlparse(href).query)["uddg"][0]
        except Exception:
            pass
    return href


async def search_web(query: str, max_results: int = 5) -> list[dict]:
    """Search the open web. Returns [{"title","snippet","url"}], [] on any failure."""
    try:
        async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
            resp = await client.post(
                _DDG_URL,
                data={"q": query, "kl": "tw-tzh"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                follow_redirects=True,
            )
            resp.raise_for_status()
    except Exception as exc:
        logger.warning(f"web_search: request failed for '{query}': {exc}")
        return []

    try:
        soup = BeautifulSoup(resp.text, "lxml")
        results = []
        for result in soup.select(".result")[:max_results]:
            title_el = result.select_one(".result__title")
            snippet_el = result.select_one(".result__snippet")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                continue
            url_el = result.select_one(".result__url")
            url = _resolve_url(title_el) or (url_el.get_text(strip=True) if url_el else "")
            results.append({
                "title": title,
                "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                "url": url,
            })
        logger.info(f"web_search: '{query}' -> {len(results)} results")
        return results
    except Exception as exc:
        logger.warning(f"web_search: parsing failed for '{query}': {exc}")
        return []
