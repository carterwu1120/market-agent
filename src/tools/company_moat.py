"""Evidence collection for company technology and supply-chain advantages.

The output is deliberately evidence-first.  It gives the research agent leads
to compare, but never labels a technology as unique or a supplier as
irreplaceable without corroborating evidence.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from src.config import settings
from src.memory.cache_store import get_cached, set_cached
from src.tools.company_insight import _get_company_name_async
from src.tools.web_search import search_web

_CACHE_VERSION = "v1"
_GROUP_KEYWORDS = {
    "technology": ("技術", "專利", "研發", "製程", "產品", "晶片", "平台"),
    "commercialization": ("量產", "營收", "訂單", "客戶", "出貨", "認證", "法說"),
    "supply_chain": ("供應鏈", "供應商", "合作", "策略夥伴", "獨家", "唯一", "採用"),
    "competition_risk": ("競爭", "對手", "替代", "風險", "市占", "降價", "庫存"),
}


def _normalize_symbol(symbol: str) -> tuple[str, str]:
    normalized = symbol.strip().upper()
    code = normalized.split(".", 1)[0]
    return code, normalized if "." in normalized else f"{normalized}.TW"


def _cache_key(symbol: str) -> str:
    code, _ = _normalize_symbol(symbol)
    return f"company_moat:{_CACHE_VERSION}:{code}"


def _categorize(title: str, snippet: str) -> list[str]:
    text = f"{title} {snippet}"
    groups = [name for name, words in _GROUP_KEYWORDS.items() if any(w in text for w in words)]
    return groups or ["technology"]


async def get_cached_company_moat(symbol: str) -> dict[str, Any] | None:
    """Read cached moat evidence without causing any network traffic."""
    result = await get_cached(_cache_key(symbol))
    if result is not None:
        result = dict(result)
        result["cached"] = True
    return result


async def get_company_moat_evidence(
    symbol: str,
    *,
    refresh: bool = False,
    max_per_group: int = 4,
) -> dict[str, Any]:
    """Collect focused evidence about technology, commercialization and risk."""
    if not refresh:
        cached = await get_cached_company_moat(symbol)
        if cached is not None:
            return cached

    code, normalized = _normalize_symbol(symbol)
    company_name = await _get_company_name_async(normalized)
    name = company_name or code
    queries = (
        f'"{name}" 技術 新產品 專利 研發 法說',
        f'"{name}" 量產 客戶 訂單 認證 供應鏈',
        f'"{name}" 競爭對手 替代技術 市占 風險',
    )
    raw_results = await asyncio.gather(
        *(search_web(query, max_results=6) for query in queries),
        return_exceptions=True,
    )

    evidence: dict[str, list[dict[str, str]]] = {name: [] for name in _GROUP_KEYWORDS}
    seen_urls: set[str] = set()
    for query, results in zip(queries, raw_results):
        if isinstance(results, Exception):
            logger.warning(f"CompanyMoat [{code}] search failed: {results}")
            continue
        for item in results:
            url = str(item.get("url", "")).strip()
            title = str(item.get("title", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            entry = {"title": title, "snippet": snippet, "url": url, "query": query}
            for group in _categorize(title, snippet):
                if len(evidence[group]) < max_per_group:
                    evidence[group].append(entry)

    result: dict[str, Any] = {
        "symbol": normalized,
        "company_name": company_name,
        "collected_at": datetime.now(UTC).isoformat(),
        "cached": False,
        "evidence": evidence,
        "limitations": [
            "搜尋結果是待查證線索，不等於公司擁有獨家技術或不可替代地位。",
            "應交叉核對公司公告、法說、客戶或監管資料，並確認量產與財務貢獻。",
            "公司品質與目前股價是否值得買進是兩個不同問題。",
        ],
    }
    await set_cached(_cache_key(symbol), result, settings.company_moat_cache_ttl_seconds)
    logger.info(
        f"CompanyMoat [{code}]: collected {sum(map(len, evidence.values()))} grouped leads"
    )
    return result
