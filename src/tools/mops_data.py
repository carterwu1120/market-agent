"""Taiwan's official corporate disclosure system (MOPS).

TWSE's OpenAPI exposes 重大訊息公告 and 財報 as whole-market JSON dumps with
no per-ticker query parameter and no history (confirmed live against
openapi.twse.com.tw's swagger spec) — so this fetches the full day's dump and
filters by 公司代號 locally. Material-information dumps are archived in SQLite
so later calls can answer recent-history questions from snapshots collected
while this application was running. Financial data remains latest-quarter only.

Single-symbol functions (get_material_info/get_financial_summary) are used by
the react MCP tools, which fetch on demand for one ticker at a time. The
_batch variants are used by daily_brief's fan-out, which needs several tickers
at once — they share one whole-market fetch across all requested symbols
instead of re-downloading the same dump once per ticker.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from loguru import logger

MATERIAL_INFO_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
INCOME_STATEMENT_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci"

# Whole-market dump is identical across calls within a short window (it's a
# daily snapshot, not real-time) -- cache briefly so comparing several
# tickers in one react conversation doesn't re-download the same multi-MB
# dump once per symbol.
_DUMP_CACHE_TTL = 300
_TZ = ZoneInfo("Asia/Taipei")


def _code(symbol: str) -> str:
    return symbol.upper().replace(".TWO", "").replace(".TW", "")


def _mops_datetime(raw_date: str, raw_time: str) -> str | None:
    """Convert MOPS ROC/Gregorian date and HHMMSS time to Taipei ISO-8601."""
    digits = "".join(ch for ch in str(raw_date) if ch.isdigit())
    time_digits = "".join(ch for ch in str(raw_time) if ch.isdigit()).zfill(6)
    try:
        if len(digits) == 7:
            year, month, day = int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:7])
        elif len(digits) == 8:
            year, month, day = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
        else:
            return None
        hour, minute, second = int(time_digits[:2]), int(time_digits[2:4]), int(time_digits[4:6])
        return datetime(year, month, day, hour, minute, second, tzinfo=_TZ).isoformat()
    except ValueError:
        return None


async def _archive_material_dump(data: list[dict]) -> None:
    from src.memory.announcement_store import upsert_announcements

    fetched_at = datetime.now(_TZ).isoformat()
    items = []
    for row in data:
        raw_date = str(row.get("發言日期", ""))
        raw_time = str(row.get("發言時間", ""))
        announced_at = _mops_datetime(raw_date, raw_time)
        symbol = str(row.get("公司代號", "")).strip()
        subject = str(row.get("主旨", "")).strip()
        if announced_at and symbol and subject:
            items.append(
                {
                    "symbol": symbol,
                    "announced_at": announced_at,
                    "date": raw_date,
                    "time": raw_time,
                    "subject": subject,
                    "source_url": MATERIAL_INFO_URL,
                    "fetched_at": fetched_at,
                }
            )
    try:
        await upsert_announcements(items)
    except Exception as exc:
        logger.warning(f"MOPS announcement archive failed: {exc}")


async def _fetch_market_dump(url: str) -> list[dict] | None:
    from src.memory.cache_store import get_cached, set_cached

    cache_key = f"mops:dump:{url}"
    cached = await get_cached(cache_key)
    if cached is not None:
        data = cached.get("items")
        if url == MATERIAL_INFO_URL and isinstance(data, list):
            await _archive_material_dump(data)
        return data

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"MOPS dump fetch failed [{url}]: {exc}")
        return None

    if url == MATERIAL_INFO_URL:
        await _archive_material_dump(data)
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
            {
                "date": d.get("發言日期", ""),
                "time": d.get("發言時間", ""),
                "subject": d.get("主旨", ""),
            }
            for d in items
        ],
        "source": MATERIAL_INFO_URL,
    }


async def get_recent_material_info(symbol: str, days: int = 30) -> dict:
    """Recent archived announcements, including a refresh of today's snapshot."""
    code = _code(symbol)
    refresh = await _fetch_market_dump(MATERIAL_INFO_URL)
    from src.memory.announcement_store import get_recent_announcements

    try:
        items = (await get_recent_announcements([code], days)).get(code, [])
    except Exception as exc:
        logger.warning(f"MOPS recent announcement query failed for {code}: {exc}")
        return {"symbol": code, "error": "history query failed"}
    return {
        "symbol": code,
        "days": max(1, min(days, 365)),
        "items": items,
        "source": MATERIAL_INFO_URL,
        "today_refresh": "ok" if refresh is not None else "failed",
    }


async def get_recent_material_info_batch(
    symbols: list[str], days: int = 30
) -> dict[str, list[dict]]:
    """Recent archived announcements for several symbols after one refresh."""
    await _fetch_market_dump(MATERIAL_INFO_URL)
    from src.memory.announcement_store import get_recent_announcements

    codes = [_code(symbol) for symbol in symbols]
    stored = await get_recent_announcements(codes, days)
    return {symbol: stored.get(_code(symbol), []) for symbol in symbols}


async def refresh_material_info_snapshot() -> bool:
    """Refresh and archive the current whole-market MOPS snapshot."""
    return await _fetch_market_dump(MATERIAL_INFO_URL) is not None


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
                {
                    "date": d.get("發言日期", ""),
                    "time": d.get("發言時間", ""),
                    "subject": d.get("主旨", ""),
                }
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
