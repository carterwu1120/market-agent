"""Market quote provider seam.

Trading decisions depend on this module for executable/mark-to-market prices;
they do not know whether the quote came from Yahoo today or a read-only
Shioaji connection in the future. Historical bars and technical indicators
remain in stock_data.py because they have a different freshness contract.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from typing import Protocol, TypedDict

from src.config import settings
from src.tools.stock_data import get_latest_price


class Quote(TypedDict, total=False):
    symbol: str
    price: float
    bid: float | None
    ask: float | None
    timestamp: str
    is_delayed: bool
    is_stale: bool
    source: str
    error: str


class MarketDataProvider(Protocol):
    async def get_quote(self, symbol: str) -> Quote: ...


class YahooMarketDataProvider:
    """Yahoo-backed quotes. Taiwan exchange quotes are delayed."""

    async def get_quote(self, symbol: str) -> Quote:
        data = await get_latest_price(symbol)
        if data.get("error"):
            return {"symbol": data.get("symbol", symbol), "error": data["error"]}

        price = data.get("last_price")
        if price is None:
            return {"symbol": data.get("symbol", symbol), "error": "missing price"}

        return {
            "symbol": data.get("symbol", symbol),
            "price": float(price),
            "bid": None,
            "ask": None,
            "timestamp": data.get("fetched_at", ""),
            "is_delayed": True,
            "is_stale": False,
            "source": data.get("source", "yahoo_finance"),
        }


def create_market_data_provider(name: str | None = None) -> MarketDataProvider:
    provider_name = name or settings.market_data_provider
    if provider_name == "yahoo":
        return YahooMarketDataProvider()
    raise ValueError(f"Unsupported market data provider: {provider_name}")


# Provider instances may eventually own a long-lived streaming connection.
# Construct once at process startup rather than once per quote request.
market_data_provider: MarketDataProvider = create_market_data_provider()
_quote_cache: dict[str, tuple[float, Quote]] = {}
_quote_inflight: dict[str, asyncio.Task[Quote]] = {}
_quote_metrics: Counter = Counter()
_quote_latency_seconds = 0.0


def _normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    return f"{normalized}.TW" if normalized.isdigit() else normalized


async def _fetch_quote(symbol: str) -> Quote:
    global _quote_latency_seconds
    started = time.monotonic()
    try:
        _quote_metrics["provider_requests"] += 1
        try:
            result = await market_data_provider.get_quote(symbol)
        except Exception as exc:
            result = {"symbol": symbol, "error": str(exc)}
        if result.get("error"):
            _quote_metrics["provider_errors"] += 1
        return result
    finally:
        _quote_latency_seconds += time.monotonic() - started


async def get_quote(symbol: str) -> Quote:
    """Return a shared short-lived quote; concurrent callers coalesce.

    A provider failure may return the last successful quote as stale for
    display/valuation. Trading code must reject ``is_stale=True``.
    """
    key = _normalize_symbol(symbol)
    now = time.monotonic()
    cached = _quote_cache.get(key)
    if cached and now - cached[0] <= settings.quote_cache_ttl_seconds:
        _quote_metrics["cache_hits"] += 1
        return dict(cached[1])

    task = _quote_inflight.get(key)
    if task is None:
        task = asyncio.create_task(_fetch_quote(key))
        _quote_inflight[key] = task
    else:
        _quote_metrics["coalesced_requests"] += 1
    try:
        result = await task
    finally:
        if _quote_inflight.get(key) is task:
            _quote_inflight.pop(key, None)

    if not result.get("error") and result.get("price") is not None:
        fresh = dict(result)
        fresh["is_stale"] = False
        _quote_cache[key] = (time.monotonic(), fresh)
        return fresh

    if cached and now - cached[0] <= settings.quote_stale_fallback_seconds:
        _quote_metrics["stale_fallbacks"] += 1
        stale = dict(cached[1])
        stale["is_stale"] = True
        stale["error"] = result.get("error", "quote refresh failed")
        return stale
    return result


def get_quote_metrics() -> dict[str, float | int]:
    requests = int(_quote_metrics["provider_requests"])
    return {
        **dict(_quote_metrics),
        "cache_entries": len(_quote_cache),
        "inflight": len(_quote_inflight),
        "average_provider_latency_ms": round(
            _quote_latency_seconds / requests * 1000, 1
        ) if requests else 0.0,
    }


def reset_quote_state() -> None:
    """Clear process-local quote cache and counters (primarily for tests)."""
    global _quote_latency_seconds
    _quote_cache.clear()
    _quote_inflight.clear()
    _quote_metrics.clear()
    _quote_latency_seconds = 0.0
