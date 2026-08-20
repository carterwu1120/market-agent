"""Local SQLite persistence for daily stock snapshots (price/chip/fundamental)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

from loguru import logger

from src.memory.store import _connect


def _today_tw() -> date:
    return datetime.now(timezone(timedelta(hours=8))).date()


def _upsert_price_sync(rows: list[dict]) -> None:
    conn = _connect()
    try:
        for row in rows:
            conn.execute(
                """
                INSERT INTO stock_daily_price
                    (symbol, company_name, date, close, change_pct, volume,
                     sma_20, sma_60, rsi_14, macd, macd_signal, bb_upper, bb_lower,
                     bias_20, bias_60, fetched_at)
                VALUES
                    (:symbol, :company_name, :date, :close, :change_pct, :volume,
                     :sma_20, :sma_60, :rsi_14, :macd, :macd_signal, :bb_upper, :bb_lower,
                     :bias_20, :bias_60, :fetched_at)
                ON CONFLICT(symbol, date) DO UPDATE SET
                    company_name=excluded.company_name, close=excluded.close,
                    change_pct=excluded.change_pct, volume=excluded.volume,
                    sma_20=excluded.sma_20, sma_60=excluded.sma_60, rsi_14=excluded.rsi_14,
                    macd=excluded.macd, macd_signal=excluded.macd_signal,
                    bb_upper=excluded.bb_upper, bb_lower=excluded.bb_lower,
                    bias_20=excluded.bias_20, bias_60=excluded.bias_60,
                    fetched_at=excluded.fetched_at
                """,
                row,
            )
        conn.commit()
    finally:
        conn.close()


def _upsert_chip_sync(rows: list[dict]) -> None:
    conn = _connect()
    try:
        for row in rows:
            conn.execute(
                """
                INSERT INTO stock_daily_chip
                    (symbol, date, foreign_net, trust_net, dealer_net, total_3_institutions,
                     margin_buy_balance, short_sell_balance, fetched_at)
                VALUES
                    (:symbol, :date, :foreign_net, :trust_net, :dealer_net, :total_3_institutions,
                     :margin_buy_balance, :short_sell_balance, :fetched_at)
                ON CONFLICT(symbol, date) DO UPDATE SET
                    foreign_net=excluded.foreign_net, trust_net=excluded.trust_net,
                    dealer_net=excluded.dealer_net, total_3_institutions=excluded.total_3_institutions,
                    margin_buy_balance=excluded.margin_buy_balance,
                    short_sell_balance=excluded.short_sell_balance, fetched_at=excluded.fetched_at
                """,
                row,
            )
        conn.commit()
    finally:
        conn.close()


def _upsert_fundamental_sync(rows: list[dict]) -> None:
    conn = _connect()
    try:
        for row in rows:
            conn.execute(
                """
                INSERT INTO stock_daily_fundamental
                    (symbol, company_name, date, pe_ratio, pb_ratio, eps_ttm, roe,
                     gross_margin, revenue_growth, analyst_target, analyst_recommendation, fetched_at)
                VALUES
                    (:symbol, :company_name, :date, :pe_ratio, :pb_ratio, :eps_ttm, :roe,
                     :gross_margin, :revenue_growth, :analyst_target, :analyst_recommendation, :fetched_at)
                ON CONFLICT(symbol, date) DO UPDATE SET
                    company_name=excluded.company_name, pe_ratio=excluded.pe_ratio,
                    pb_ratio=excluded.pb_ratio, eps_ttm=excluded.eps_ttm, roe=excluded.roe,
                    gross_margin=excluded.gross_margin, revenue_growth=excluded.revenue_growth,
                    analyst_target=excluded.analyst_target,
                    analyst_recommendation=excluded.analyst_recommendation, fetched_at=excluded.fetched_at
                """,
                row,
            )
        conn.commit()
    finally:
        conn.close()


def _query_history_sync(symbol: str, cutoff: str) -> dict:
    conn = _connect()
    try:
        price_rows = conn.execute(
            """
            SELECT date, close, change_pct, sma_20, sma_60, rsi_14 FROM stock_daily_price
            WHERE symbol = ? AND date >= ? ORDER BY date DESC
            """,
            (symbol, cutoff),
        ).fetchall()
        chip_rows = conn.execute(
            """
            SELECT date, foreign_net, trust_net, dealer_net, total_3_institutions
            FROM stock_daily_chip WHERE symbol = ? AND date >= ? ORDER BY date DESC
            """,
            (symbol, cutoff),
        ).fetchall()
        fund_rows = conn.execute(
            """
            SELECT date, pe_ratio, pb_ratio, eps_ttm, roe, gross_margin
            FROM stock_daily_fundamental WHERE symbol = ? AND date >= ? ORDER BY date DESC
            """,
            (symbol, cutoff),
        ).fetchall()
    finally:
        conn.close()

    return {
        "symbol": symbol,
        "price_history": [
            {"date": r["date"], "close": r["close"], "change_pct": r["change_pct"],
             "sma_20": r["sma_20"], "sma_60": r["sma_60"], "rsi_14": r["rsi_14"]}
            for r in price_rows
        ],
        "chip_history": [
            {"date": r["date"], "foreign_net": r["foreign_net"], "trust_net": r["trust_net"],
             "dealer_net": r["dealer_net"], "total_3_institutions": r["total_3_institutions"]}
            for r in chip_rows
        ],
        "fundamental_history": [
            {"date": r["date"], "pe_ratio": r["pe_ratio"], "pb_ratio": r["pb_ratio"],
             "eps_ttm": r["eps_ttm"], "roe": r["roe"], "gross_margin": r["gross_margin"]}
            for r in fund_rows
        ],
    }


async def upsert_daily_price(technical_data: list[dict]) -> None:
    """Upsert technical indicator rows from technical_agent output."""
    if not technical_data:
        return
    today = _today_tw()
    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for item in technical_data:
        sym = item.get("symbol", "")
        price = item.get("price", {})
        ind = item.get("indicators", {})
        if not sym:
            continue

        data_date = today
        if price.get("fetched_at"):
            try:
                data_date = datetime.fromisoformat(price["fetched_at"]).date()
            except Exception:
                pass

        rows.append({
            "symbol": sym,
            "company_name": price.get("company_name") or "",
            "date": data_date.isoformat(),
            "close": price.get("last_price"),
            "change_pct": price.get("change_pct"),
            "volume": price.get("volume"),
            "sma_20": ind.get("sma_20"),
            "sma_60": ind.get("sma_60"),
            "rsi_14": ind.get("rsi_14"),
            "macd": ind.get("macd"),
            "macd_signal": ind.get("macd_signal"),
            "bb_upper": ind.get("bb_upper"),
            "bb_lower": ind.get("bb_lower"),
            "bias_20": ind.get("bias_20"),
            "bias_60": ind.get("bias_60"),
            "fetched_at": fetched_at,
        })

    if not rows:
        return
    try:
        await asyncio.to_thread(_upsert_price_sync, rows)
        logger.info(f"StockStore: upserted {len(rows)} price rows")
    except Exception as exc:
        logger.warning(f"StockStore: price upsert failed: {exc}")


async def upsert_daily_chip(chip_data: list[dict]) -> None:
    """Upsert chip rows from chip_agent output."""
    if not chip_data:
        return
    today = _today_tw()
    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for item in chip_data:
        sym = item.get("symbol", "")
        inst = item.get("institutional", {})
        margin = item.get("margin", {})
        if not sym or inst.get("error"):
            continue

        data_date = today
        if inst.get("date"):
            try:
                data_date = date.fromisoformat(inst["date"])
            except Exception:
                pass

        rows.append({
            "symbol": sym,
            "date": data_date.isoformat(),
            "foreign_net": inst.get("foreign_net"),
            "trust_net": inst.get("trust_net"),
            "dealer_net": inst.get("dealer_net"),
            "total_3_institutions": inst.get("total_3_institutions"),
            "margin_buy_balance": margin.get("margin_buy_balance") if not margin.get("error") else None,
            "short_sell_balance": margin.get("short_sell_balance") if not margin.get("error") else None,
            "fetched_at": fetched_at,
        })

    if not rows:
        return
    try:
        await asyncio.to_thread(_upsert_chip_sync, rows)
        logger.info(f"StockStore: upserted {len(rows)} chip rows")
    except Exception as exc:
        logger.warning(f"StockStore: chip upsert failed: {exc}")


async def upsert_daily_fundamental(fundamental_data: list[dict]) -> None:
    """Upsert fundamental rows from fundamental_agent output."""
    if not fundamental_data:
        return
    today = _today_tw()
    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for item in fundamental_data:
        sym = item.get("symbol", "")
        if not sym or item.get("error"):
            continue
        rows.append({
            "symbol": sym,
            "company_name": item.get("company_name") or "",
            "date": today.isoformat(),
            "pe_ratio": item.get("pe_ratio"),
            "pb_ratio": item.get("pb_ratio"),
            "eps_ttm": item.get("eps_ttm"),
            "roe": item.get("roe"),
            "gross_margin": item.get("gross_margin"),
            "revenue_growth": item.get("revenue_growth"),
            "analyst_target": item.get("analyst_target"),
            "analyst_recommendation": item.get("analyst_recommendation"),
            "fetched_at": fetched_at,
        })

    if not rows:
        return
    try:
        await asyncio.to_thread(_upsert_fundamental_sync, rows)
        logger.info(f"StockStore: upserted {len(rows)} fundamental rows")
    except Exception as exc:
        logger.warning(f"StockStore: fundamental upsert failed: {exc}")


async def query_stock_history(symbol: str, days: int = 7) -> dict:
    """Query historical data for a symbol across all three tables."""
    cutoff = (_today_tw() - timedelta(days=days)).isoformat()
    return await asyncio.to_thread(_query_history_sync, symbol, cutoff)
