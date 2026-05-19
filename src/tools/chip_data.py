"""台股籌碼面資料：TWSE 公開資訊（三大法人）+ goodinfo scraper.

資料來源明確標註 URL，不憑空生成數據。
"""

from __future__ import annotations
import asyncio
from datetime import date, datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup
from loguru import logger


TWSE_BASE = "https://www.twse.com.tw"
TPEX_BASE = "https://www.tpex.org.tw"


# ── TWSE 三大法人買賣超 ────────────────────────────────────────────────────────

async def get_institutional_trading(symbol: str, query_date: date | None = None) -> dict[str, Any]:
    """
    Fetch 三大法人 (foreign/trust/dealer) net buy/sell from TWSE open data API.
    Source: https://www.twse.com.tw/rwd/zh/fund/T86
    """
    query_date = query_date or date.today()
    date_str = query_date.strftime("%Y%m%d")
    url = f"{TWSE_BASE}/rwd/zh/fund/T86?date={date_str}&selectType=ALLBUT0999&response=json"

    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Mozilla/5.0"}) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning(f"TWSE institutional data failed: {exc}")
            return {"symbol": symbol, "error": str(exc), "source": url}

    rows = data.get("data", [])
    # Row format: [代號, 名稱, 外資買, 外資賣, 外資淨買, 投信買, 投信賣, 投信淨買, 自營買, 自營賣, 自營淨買, 三大合計]
    target_code = symbol.replace(".TW", "")
    for row in rows:
        if row[0] == target_code:
            def to_int(s: str) -> int:
                return int(s.replace(",", "").replace("+", "") or 0)
            return {
                "symbol": symbol,
                "date": query_date.isoformat(),
                "foreign_net": to_int(row[4]),    # 外資淨買超（張）
                "trust_net": to_int(row[7]),       # 投信淨買超
                "dealer_net": to_int(row[10]),     # 自營商淨買超
                "total_3_institutions": to_int(row[11]),
                "source": f"{TWSE_BASE}/rwd/zh/fund/T86",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }

    return {"symbol": symbol, "date": query_date.isoformat(), "error": "symbol not found", "source": url}


# ── Goodinfo 籌碼資料（補充）────────────────────────────────────────────────────

async def get_goodinfo_chip(symbol: str) -> dict[str, Any]:
    """
    Scrape goodinfo.tw for shareholding concentration data.
    NOTE: goodinfo blocks aggressive scraping — add polite delays between calls.
    """
    code = symbol.replace(".TW", "")
    url = f"https://goodinfo.tw/tw/StockBzSaleInfo.asp?STOCK_ID={code}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://goodinfo.tw/",
        "Accept-Language": "zh-TW,zh;q=0.9",
    }

    async with httpx.AsyncClient(timeout=20, headers=headers) as client:
        try:
            await asyncio.sleep(1.5)  # polite delay
            resp = await client.get(url)
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception as exc:
            logger.warning(f"Goodinfo scrape failed [{code}]: {exc}")
            return {"symbol": symbol, "error": str(exc), "source": url}

    result: dict[str, Any] = {"symbol": symbol, "source": url}

    # Parse 董監持股 + 外資持股比例 from summary table
    tables = soup.find_all("table")
    for table in tables:
        text = table.get_text()
        if "外資持股" in text or "董監持股" in text:
            for row in table.find_all("tr"):
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if len(cells) >= 2:
                    if "外資持股" in cells[0]:
                        result["foreign_holding_pct"] = cells[1]
                    elif "董監持股" in cells[0]:
                        result["insider_holding_pct"] = cells[1]
                    elif "投信持股" in cells[0]:
                        result["trust_holding_pct"] = cells[1]
            break

    return result


# ── TWSE 融資融券 ──────────────────────────────────────────────────────────────

async def get_margin_trading(symbol: str, query_date: date | None = None) -> dict[str, Any]:
    """Fetch margin trading (融資融券) from TWSE.

    Falls back up to 5 previous calendar days when today has no data
    (weekends, holidays, or pre-close requests).
    """
    from datetime import timedelta
    code = symbol.replace(".TW", "")
    base_date = query_date or date.today()

    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for days_back in range(5):
            target = base_date - timedelta(days=days_back)
            date_str = target.strftime("%Y%m%d")
            url = f"{TWSE_BASE}/rwd/zh/marginTrading/MI_MARGN?date={date_str}&selectType=ALL&response=json"
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning(f"TWSE margin data failed ({date_str}): {exc}")
                continue

            rows = data.get("data", [])
            if not rows:
                continue  # no trading data for this date, try previous day

            for row in rows:
                if row[0] == code:
                    return {
                        "symbol": symbol,
                        "date": target.isoformat(),
                        "margin_buy_balance": row[6],
                        "short_sell_balance": row[12],
                        "margin_utilization": row[8],
                        "source": f"{TWSE_BASE}/rwd/zh/marginTrading/MI_MARGN",
                    }
            break  # date had data but symbol not found — don't keep going back

    return {"symbol": symbol, "date": base_date.isoformat(), "error": "not found"}
