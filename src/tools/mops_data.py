"""Taiwan's official corporate disclosure system (MOPS) — today-only snapshots.

TWSE's OpenAPI exposes 重大訊息公告 and 財報 as whole-market JSON dumps with
no per-ticker query parameter and no history (confirmed live against
openapi.twse.com.tw's swagger spec) — so this fetches the full day's dump and
filters by 公司代號 locally. This only ever answers "today" / "latest
quarter"; it cannot answer historical questions. Real history would need a
daily ingestion job snapshotting these into SQLite over time (not built here).
"""

from __future__ import annotations

import httpx
from loguru import logger

_MATERIAL_INFO_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
_INCOME_STATEMENT_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci"


def _code(symbol: str) -> str:
    return symbol.replace(".TW", "").replace(".tw", "")


async def get_material_info(symbol: str) -> dict:
    """Today's 重大訊息公告 for this ticker (empty items list if none today)."""
    code = _code(symbol)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(_MATERIAL_INFO_URL)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"MOPS material info fetch failed [{code}]: {exc}")
        return {"symbol": code, "error": str(exc)}

    items = [d for d in data if d.get("公司代號") == code]
    return {
        "symbol": code,
        "items": [
            {"date": d.get("發言日期", ""), "time": d.get("發言時間", ""), "subject": d.get("主旨", "")}
            for d in items
        ],
        "source": _MATERIAL_INFO_URL,
    }


async def get_financial_summary(symbol: str) -> dict:
    """Latest-quarter 綜合損益表 for this ticker.

    Returns the raw matched record (minus symbol/name/report-date bookkeeping
    fields) rather than cherry-picking named columns — TWSE's schema may
    expose more fields than we've confirmed live, and dumping the raw dict
    avoids silently mis-mapping one that doesn't exist.
    """
    code = _code(symbol)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(_INCOME_STATEMENT_URL)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"MOPS financial summary fetch failed [{code}]: {exc}")
        return {"symbol": code, "error": str(exc)}

    match = next((d for d in data if d.get("公司代號") == code), None)
    if not match:
        return {"symbol": code, "error": "本季查無此公司財報資料"}
    return {"symbol": code, "fields": match, "source": _INCOME_STATEMENT_URL}
