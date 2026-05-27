"""Market Agent — runs after news_agent for daily_brief.

Responsibilities:
1. Fetch major market indices (台股、S&P500、NASDAQ、DJI)
2. Extract 5-8 hot Taiwan stock symbols from today's news via LLM
"""

import asyncio
import json
import re
from dataclasses import asdict
from loguru import logger

from src.agents.state import AgentState
from src.llm import llm_chat
from src.tools.stock_data import get_market_indices


_EXTRACT_SYSTEM = """你是台股選股助理。從候選個股清單中，挑出今日新聞最熱門的台灣上市/上櫃個股。

規則：
- 只能從「候選清單」中的代號挑選，嚴禁自行新增清單外的代號
- 只回傳台灣股票代號（4位數字），格式為 JSON array
- 取 5-8 檔，優先選有具體事件（法說會、訂單、業績、產品發布）的個股
- 若候選清單不足 5 檔，全部回傳
- 只回傳 JSON，例如：["2330", "2454", "3034"]
"""

# Fallback ticker→name mapping used when TWSE fetch fails
_FALLBACK_TICKER_NAMES: dict[str, str] = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電",
    "2303": "聯電", "2412": "中華電", "2882": "國泰金", "2881": "富邦金",
    "2886": "兆豐金", "2891": "中信金", "2884": "玉山金", "3711": "日月光投控",
    "2379": "瑞昱", "3034": "聯詠", "2382": "廣達", "2357": "華碩",
    "2395": "研華", "4938": "和碩", "2474": "可成", "3008": "大立光",
    "2615": "萬海", "2603": "長榮", "2609": "陽明", "2327": "國巨",
    "6505": "台塑化", "1301": "台塑", "1303": "南亞", "1326": "台化",
    "2002": "中鋼", "2207": "和泰車", "2408": "南亞科", "2344": "華邦電",
    "3231": "緯創", "2367": "燿華", "6669": "緯穎", "3017": "奇鋐",
    "2376": "技嘉", "2377": "微星", "4904": "遠傳", "3045": "台灣大",
}

# In-memory cache of the authoritative TWSE ticker set (populated once at startup)
# Only TWSE (.TW) — TPEX (.TWO) excluded until the rest of the pipeline supports OTC symbols
_twse_ticker_set: set[str] | None = None
_twse_ticker_names: dict[str, str] = {}
_twse_fetch_failed: bool = False  # True = last attempt failed; retry on next call


async def _load_twse_tickers() -> tuple[set[str], dict[str, str]]:
    """Fetch the full TWSE listed stock universe as {code: name}.

    Only TWSE (.TW) stocks are included; TPEX (.TWO) is excluded until the rest
    of the pipeline supports OTC symbol suffixes.

    Caches a successful result in module globals for the process lifetime.
    If the fetch fails, returns the fallback list WITHOUT caching so the next
    call retries the upstream API.
    """
    global _twse_ticker_set, _twse_ticker_names, _twse_fetch_failed

    # Return cached result only if the last fetch succeeded
    if _twse_ticker_set is not None and not _twse_fetch_failed:
        return _twse_ticker_set, _twse_ticker_names

    import httpx

    names: dict[str, str] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                headers={"Accept": "application/json"},
            )
            if r.status_code == 200:
                for item in r.json():
                    code = item.get("Code", "").strip()
                    name = item.get("Name", "").strip()
                    if re.match(r"^\d{4}$", code):
                        names[code] = name
    except Exception as exc:
        logger.warning(f"MarketAgent: TWSE ticker fetch failed: {exc}")

    if not names:
        # Do NOT cache failure — retry on next call
        _twse_fetch_failed = True
        logger.warning("MarketAgent: TWSE fetch returned no data, using fallback list (will retry next call)")
        fallback = _FALLBACK_TICKER_NAMES.copy()
        return set(fallback.keys()), fallback

    # Successful fetch — seed with fallback names then cache
    for code, name in _FALLBACK_TICKER_NAMES.items():
        names.setdefault(code, name)

    _twse_ticker_names = names
    _twse_ticker_set = set(names.keys())
    _twse_fetch_failed = False
    logger.info(f"MarketAgent: loaded {len(_twse_ticker_set)} TWSE tickers")
    return _twse_ticker_set, _twse_ticker_names


def _normalize_articles(articles: list) -> list[dict]:
    """Ensure articles are plain dicts (convert dataclasses if needed)."""
    return [asdict(a) if not isinstance(a, dict) else a for a in articles]


def _extract_candidate_codes(news_articles: list[dict], ticker_set: set[str], ticker_names: dict[str, str]) -> list[str]:
    """Extract Taiwan stock codes that actually appear in article text/titles.

    Only accepts codes present in the authoritative TWSE/TPEX ticker universe
    to avoid treating years, dates, or counts as tickers.
    """
    text = " ".join(
        f"{a.get('title', '')} {a.get('content', '')}"
        for a in news_articles
    )
    found: set[str] = set()
    for code in re.findall(r"\b(\d{4})\b", text):
        if code in ticker_set:
            found.add(code)
    for code, name in ticker_names.items():
        if name in text:
            found.add(code)
    unmapped = [c for c in re.findall(r"\b(\d{4})\b", text) if c not in ticker_set and 1000 <= int(c) <= 9999]
    if unmapped:
        logger.debug(f"MarketAgent: {len(set(unmapped))} unmapped 4-digit tokens skipped: {set(unmapped)}")
    return list(found)


async def _extract_hot_stocks(news_articles: list[dict]) -> list[str]:
    if not news_articles:
        return []

    ticker_set, ticker_names = await _load_twse_tickers()

    # Use the same article slice for both candidate extraction and LLM title list
    article_slice = news_articles[:30]
    candidates = _extract_candidate_codes(article_slice, ticker_set, ticker_names)
    if not candidates:
        logger.info("MarketAgent: no candidate tickers found in news, skipping hot stock extraction")
        return []

    titles = "\n".join(
        f"- {a.get('title', '')}"
        for a in article_slice[:20]
        if a.get("title")
    )
    candidate_str = ", ".join(candidates)

    try:
        raw = await llm_chat(
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": (
                    f"候選清單（只能從這裡選）：[{candidate_str}]\n\n"
                    f"今日新聞標題：\n{titles}"
                )},
            ],
            temperature=0.1,
        )
        raw = re.sub(r"```(?:json)?|```", "", raw).strip()
        codes = json.loads(raw)
        candidate_set = set(candidates)
        symbols = [
            f"{c}.TW" for c in codes
            if re.match(r"^\d{4}$", str(c)) and str(c) in candidate_set
        ]
        logger.info(f"MarketAgent: extracted {len(symbols)} hot symbols from {len(candidates)} candidates")
        return symbols[:8]
    except Exception as exc:
        logger.warning(f"MarketAgent: hot stock extraction failed: {exc}")
        return []


async def market_agent_node(state: AgentState) -> dict:
    logger.info("MarketAgent: fetching indices + extracting hot stocks")

    news_articles = _normalize_articles(state.news_articles)

    if not news_articles and state.news_cached:
        from src.memory.news_cache import load_news_cache
        cached = await load_news_cache()
        if cached:
            news_articles = _normalize_articles(cached)
            logger.info(f"MarketAgent: loaded {len(news_articles)} articles from cache")
        else:
            logger.warning("MarketAgent: cache load returned empty, falling back to fresh news fetch")
            from src.tools.news_fetcher import fetch_all_news
            from src.memory.news_cache import save_news_cache
            try:
                raw = await fetch_all_news()
                news_articles = _normalize_articles(raw)
                if news_articles:
                    await save_news_cache(news_articles)
                    logger.info(f"MarketAgent: fetched {len(news_articles)} articles as fallback")
            except Exception as exc:
                logger.error(f"MarketAgent: fallback news fetch failed: {exc}")

            if not news_articles:
                # Both cache and fresh fetch failed — surface this as an explicit error
                indices = await get_market_indices()
                return {
                    "market_indices": indices,
                    "target_symbols": [],
                    "news_articles": [],
                    "error": "每日摘要新聞來源暫時無法取得（Redis 快取與備援新聞擷取均失敗），股票分析略過。",
                }

    indices, hot_symbols = await asyncio.gather(
        get_market_indices(),
        _extract_hot_stocks(news_articles),
    )

    return {
        "market_indices": indices,
        "target_symbols": hot_symbols,
        "news_articles": news_articles if not state.news_articles else [],
    }
