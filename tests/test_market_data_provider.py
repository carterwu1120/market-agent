from unittest.mock import AsyncMock

import pytest

from src.tools import market_data


@pytest.mark.asyncio
async def test_yahoo_provider_normalizes_stock_data_to_quote(monkeypatch):
    monkeypatch.setattr(
        market_data,
        "get_stock_price",
        AsyncMock(
            return_value={
                "symbol": "2330.TW",
                "last_price": 123.45,
                "fetched_at": "2026-08-25T01:02:03+00:00",
                "source": "https://finance.yahoo.com/quote/2330.TW",
            }
        ),
    )

    quote = await market_data.YahooMarketDataProvider().get_quote("2330")

    assert quote == {
        "symbol": "2330.TW",
        "price": 123.45,
        "bid": None,
        "ask": None,
        "timestamp": "2026-08-25T01:02:03+00:00",
        "is_delayed": True,
        "source": "https://finance.yahoo.com/quote/2330.TW",
    }


@pytest.mark.asyncio
async def test_yahoo_provider_preserves_fetch_errors(monkeypatch):
    monkeypatch.setattr(
        market_data,
        "get_stock_price",
        AsyncMock(return_value={"symbol": "2330.TW", "error": "timeout"}),
    )

    assert await market_data.YahooMarketDataProvider().get_quote("2330") == {
        "symbol": "2330.TW",
        "error": "timeout",
    }


def test_factory_builds_yahoo_provider():
    provider = market_data.create_market_data_provider("yahoo")
    assert isinstance(provider, market_data.YahooMarketDataProvider)


def test_factory_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unsupported market data provider"):
        market_data.create_market_data_provider("unknown")
