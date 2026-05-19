"""Stock data tools: price, technical indicators, fundamental data.

All returned dicts include a `source` field with the data origin URL
so the synthesizer agent can cite them in the final report.
"""

from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf
from loguru import logger


def _tw_ticker(symbol: str) -> str:
    """Normalize Taiwan stock symbol: '2330' → '2330.TW'"""
    if "." not in symbol and symbol.isdigit():
        return f"{symbol}.TW"
    return symbol


# ── Price & Basic Info ────────────────────────────────────────────────────────

async def get_stock_price(symbol: str) -> dict[str, Any]:
    """Fetch latest price and basic market data via yfinance."""
    ticker_sym = _tw_ticker(symbol)

    def _fetch():
        t = yf.Ticker(ticker_sym)
        info = t.fast_info
        hist = t.history(period="5d")
        return info, hist

    try:
        info, hist = await asyncio.to_thread(_fetch)
        last_price = float(hist["Close"].iloc[-1]) if not hist.empty else None
        prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else None
        change_pct = ((last_price - prev_close) / prev_close * 100) if (last_price and prev_close) else None

        return {
            "symbol": ticker_sym,
            "last_price": last_price,
            "prev_close": prev_close,
            "change_pct": round(change_pct, 2) if change_pct else None,
            "volume": int(info.three_month_average_volume or 0),
            "market_cap": info.market_cap,
            "currency": info.currency,
            "source": f"https://finance.yahoo.com/quote/{ticker_sym}",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.warning(f"Price fetch failed [{ticker_sym}]: {exc}")
        return {"symbol": ticker_sym, "error": str(exc)}


# ── Technical Analysis ────────────────────────────────────────────────────────

async def get_technical_indicators(symbol: str, period: str = "3mo") -> dict[str, Any]:
    """Compute MA, RSI, MACD, Bollinger Bands from yfinance OHLCV."""
    ticker_sym = _tw_ticker(symbol)

    def _compute():
        import pandas_ta as ta
        t = yf.Ticker(ticker_sym)
        df = t.history(period=period)
        if df.empty:
            return None

        df.ta.rsi(length=14, append=True)
        df.ta.macd(fast=12, slow=26, signal=9, append=True)
        df.ta.bbands(length=20, append=True)
        df.ta.sma(length=20, append=True)
        df.ta.sma(length=60, append=True)
        df.ta.ema(length=12, append=True)

        latest = df.iloc[-1]
        return {
            "symbol": ticker_sym,
            "period": period,
            "close": round(float(latest["Close"]), 2),
            "rsi_14": round(float(latest.get("RSI_14", float("nan"))), 2),
            "macd": round(float(latest.get("MACD_12_26_9", float("nan"))), 4),
            "macd_signal": round(float(latest.get("MACDs_12_26_9", float("nan"))), 4),
            "macd_hist": round(float(latest.get("MACDh_12_26_9", float("nan"))), 4),
            "bb_upper": round(float(latest.get("BBU_20_2.0", float("nan"))), 2),
            "bb_lower": round(float(latest.get("BBL_20_2.0", float("nan"))), 2),
            "sma_20": round(float(latest.get("SMA_20", float("nan"))), 2),
            "sma_60": round(float(latest.get("SMA_60", float("nan"))), 2),
            "ema_12": round(float(latest.get("EMA_12", float("nan"))), 2),
            "source": f"https://finance.yahoo.com/quote/{ticker_sym}/history/",
        }

    try:
        result = await asyncio.to_thread(_compute)
        return result or {"symbol": ticker_sym, "error": "empty data"}
    except Exception as exc:
        logger.warning(f"Technical indicators failed [{ticker_sym}]: {exc}")
        return {"symbol": ticker_sym, "error": str(exc)}


# ── Fundamental Data ──────────────────────────────────────────────────────────

async def get_fundamental_data(symbol: str) -> dict[str, Any]:
    """Fetch key fundamental ratios from yfinance (covers global + TW ADRs)."""
    ticker_sym = _tw_ticker(symbol)

    def _fetch():
        t = yf.Ticker(ticker_sym)
        info = t.info
        return {
            "symbol": ticker_sym,
            "company_name": info.get("longName") or info.get("shortName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "pb_ratio": info.get("priceToBook"),
            "eps_ttm": info.get("trailingEps"),
            "revenue_growth": info.get("revenueGrowth"),
            "gross_margin": info.get("grossMargins"),
            "operating_margin": info.get("operatingMargins"),
            "roe": info.get("returnOnEquity"),
            "debt_to_equity": info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"),
            "dividend_yield": info.get("dividendYield"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
            "analyst_target": info.get("targetMeanPrice"),
            "analyst_recommendation": info.get("recommendationKey"),
            "source": f"https://finance.yahoo.com/quote/{ticker_sym}/financials/",
        }

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as exc:
        logger.warning(f"Fundamental data failed [{ticker_sym}]: {exc}")
        return {"symbol": ticker_sym, "error": str(exc)}
