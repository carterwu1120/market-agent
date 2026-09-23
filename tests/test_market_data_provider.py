from unittest.mock import AsyncMock

import pytest

from src.tools import market_data


@pytest.fixture(autouse=True)
def _reset_quote_state():
    market_data.reset_quote_state()


@pytest.mark.asyncio
async def test_yahoo_provider_normalizes_stock_data_to_quote(monkeypatch):
    monkeypatch.setattr(
        market_data,
        "get_latest_price",
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
        "is_stale": False,
        "source": "https://finance.yahoo.com/quote/2330.TW",
    }


@pytest.mark.asyncio
async def test_yahoo_provider_preserves_fetch_errors(monkeypatch):
    monkeypatch.setattr(
        market_data,
        "get_latest_price",
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


@pytest.mark.asyncio
async def test_quote_cache_and_concurrent_calls_share_one_provider_request(monkeypatch):
    provider = AsyncMock(return_value={"symbol": "2330.TW", "price": 100.0})
    monkeypatch.setattr(market_data.market_data_provider, "get_quote", provider)
    quotes = await __import__("asyncio").gather(
        market_data.get_quote("2330"), market_data.get_quote("2330.TW")
    )
    assert quotes[0]["price"] == quotes[1]["price"] == 100.0
    assert provider.await_count == 1
    assert (await market_data.get_quote("2330"))["price"] == 100.0
    assert provider.await_count == 1
    metrics = market_data.get_quote_metrics()
    assert metrics["provider_requests"] == 1
    assert metrics["cache_hits"] == 1


@pytest.mark.asyncio
async def test_failed_refresh_returns_stale_quote(monkeypatch):
    monkeypatch.setattr(market_data.settings, "quote_cache_ttl_seconds", -1)
    provider = AsyncMock(side_effect=[
        {"symbol": "2330.TW", "price": 100.0},
        {"symbol": "2330.TW", "error": "timeout"},
    ])
    monkeypatch.setattr(market_data.market_data_provider, "get_quote", provider)
    assert (await market_data.get_quote("2330"))["is_stale"] is False
    stale = await market_data.get_quote("2330")
    assert stale["price"] == 100.0
    assert stale["is_stale"] is True
