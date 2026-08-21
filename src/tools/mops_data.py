"""Taiwan's official corporate disclosure system (MOPS) — today-only snapshots.

TWSE's OpenAPI exposes 重大訊息公告 and 財報 as whole-market JSON dumps with
no per-ticker query parameter and no history (confirmed live against
openapi.twse.com.tw's swagger spec) — so this fetches the full day's dump and
filters by 公司代號 locally. This only ever answers "today" / "latest
quarter"; it cannot answer historical questions. Real history would need a
daily ingestion job snapshotting these into SQLite over time (not built here).

Single-symbol functions (get_material_info/get_financial_summary) are used by
the react MCP tools, which fetch on demand for one ticker at a time. The
_batch variants are used by daily_brief's fan-out, which needs several tickers
at once — they share one whole-market fetch across all requested symbols
instead of re-downloading the same dump once per ticker.
"""

from __future__ import annotations

import httpx
from loguru import logger

MATERIAL_INFO_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
INCOME_STATEMENT_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci"

# Whole-market dump is identical across calls within a short window (it's a
# daily snapshot, not real-time) -- cache briefly so comparing several
# tickers in one react conversation doesn't re-download the same multi-MB
# dump once per symbol.
_DUMP_CACHE_TTL = 300


def _code(symbol: str) -> str:
    return symbol.upper().replace(".TWO", "").replace(".TW", "")


async def _fetch_market_dump(url: str) -> list[dict] | None:
    from src.memory.cache_store import get_cached, set_cached

    cache_key = f"mops:dump:{url}"
    cached = await get_cached(cache_key)
    if cached is not None:
        return cached.get("items")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"MOPS dump fetch failed [{url}]: {exc}")
        return None

    await set_cached(cache_key, {"items": data}, _DUMP_CACHE_TTL)
    return data


async def get_material_info(symbol: str) -> dict:
    """Today's 重大訊息公告 for this ticker (empty items list if none today)."""
    code = _code(symbol)
    data = await _fetch_market_dump(MATERIAL_INFO_URL)
    if data is None:
        return {"symbol": code, "error": "fetch failed"}

    items = [d for d in data if d.get("公司代號") == code]
    return {
        "symbol": code,
        "items": [
            {"date": d.get("發言日期", ""), "time": d.get("發言時間", ""), "subject": d.get("主旨", "")}
            for d in items
        ],
        "source": MATERIAL_INFO_URL,
    }


async def get_financial_summary(symbol: str) -> dict:
    """Latest-quarter 綜合損益表 for this ticker.

    Returns the raw matched record (minus symbol/name/report-date bookkeeping
    fields) rather than cherry-picking named columns — TWSE's schema may
    expose more fields than we've confirmed live, and dumping the raw dict
    avoids silently mis-mapping one that doesn't exist.
    """
    code = _code(symbol)
    data = await _fetch_market_dump(INCOME_STATEMENT_URL)
    if data is None:
        return {"symbol": code, "error": "fetch failed"}

    match = next((d for d in data if d.get("公司代號") == code), None)
    if not match:
        return {"symbol": code, "error": "本季查無此公司財報資料"}
    return {"symbol": code, "fields": match, "source": INCOME_STATEMENT_URL}


async def get_material_info_batch(symbols: list[str]) -> dict[str, list[dict]]:
    """Today's 重大訊息公告 for multiple tickers, sharing one whole-market fetch.

    Keys match the input `symbols` list exactly (e.g. "2330.TW").
    """
    code_to_symbol = {_code(s): s for s in symbols}
    data = await _fetch_market_dump(MATERIAL_INFO_URL)
    if data is None:
        return {}
    result: dict[str, list[dict]] = {s: [] for s in symbols}
    for d in data:
        code = d.get("公司代號")
        if code in code_to_symbol:
            result[code_to_symbol[code]].append(
                {"date": d.get("發言日期", ""), "time": d.get("發言時間", ""), "subject": d.get("主旨", "")}
            )
    return result


async def get_financial_summary_batch(symbols: list[str]) -> dict[str, dict]:
    """Latest-quarter 綜合損益表 for multiple tickers, sharing one whole-market fetch.

    Keys match the input `symbols` list (only symbols with a match present).
    """
    code_to_symbol = {_code(s): s for s in symbols}
    data = await _fetch_market_dump(INCOME_STATEMENT_URL)
    if data is None:
        return {}
    return {
        code_to_symbol[d["公司代號"]]: d
        for d in data
        if d.get("公司代號") in code_to_symbol
    }
