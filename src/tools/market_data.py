"""Market quote provider seam.

Trading decisions depend on this module for executable/mark-to-market prices;
they do not know whether the quote came from Yahoo today or a read-only
Shioaji connection in the future. Historical bars and technical indicators
remain in stock_data.py because they have a different freshness contract.
"""

from __future__ import annotations

from typing import Protocol, TypedDict

from src.config import settings
from src.tools.stock_data import get_stock_price


class Quote(TypedDict, total=False):
    symbol: str
    price: float
    bid: float | None
    ask: float | None
    timestamp: str
    is_delayed: bool
    source: str
    error: str


class MarketDataProvider(Protocol):
    async def get_quote(self, symbol: str) -> Quote: ...


class YahooMarketDataProvider:
    """Yahoo-backed quotes. Taiwan exchange quotes are delayed."""

    async def get_quote(self, symbol: str) -> Quote:
        data = await get_stock_price(symbol)
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


async def get_quote(symbol: str) -> Quote:
    return await market_data_provider.get_quote(symbol)
