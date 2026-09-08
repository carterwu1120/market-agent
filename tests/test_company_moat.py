from unittest.mock import AsyncMock

import pytest

from src.tools import company_moat


@pytest.mark.asyncio
async def test_cache_hit_avoids_company_lookup_and_web_search(monkeypatch):
    cached = {
        "symbol": "2330.TW",
        "cached": False,
        "evidence": {"technology": []},
    }
    cache_mock = AsyncMock(return_value=cached)
    lookup_mock = AsyncMock()
    search_mock = AsyncMock()
    monkeypatch.setattr(company_moat, "get_cached", cache_mock)
    monkeypatch.setattr(company_moat, "_get_company_name_async", lookup_mock)
    monkeypatch.setattr(company_moat, "search_web", search_mock)

    result = await company_moat.get_company_moat_evidence("2330")

    assert result["cached"] is True
    lookup_mock.assert_not_awaited()
    search_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_collects_groups_deduplicates_and_caches(monkeypatch):
    monkeypatch.setattr(
        company_moat, "_get_company_name_async", AsyncMock(return_value="測試公司")
    )
    search_mock = AsyncMock(
        side_effect=[
            [{"title": "新技術完成專利", "snippet": "研發成果", "url": "https://a.test/1"}],
            [{"title": "客戶認證並量產", "snippet": "取得訂單", "url": "https://a.test/2"}],
            [
                {"title": "競爭對手推出替代品", "snippet": "市占風險", "url": "https://a.test/3"},
                {"title": "重複", "snippet": "技術", "url": "https://a.test/1"},
            ],
        ]
    )
    set_mock = AsyncMock()
    monkeypatch.setattr(company_moat, "search_web", search_mock)
    monkeypatch.setattr(company_moat, "set_cached", set_mock)

    result = await company_moat.get_company_moat_evidence("2330.TW", refresh=True)

    assert search_mock.await_count == 3
    assert result["cached"] is False
    assert result["evidence"]["technology"][0]["url"] == "https://a.test/1"
    assert result["evidence"]["commercialization"][0]["url"] == "https://a.test/2"
    assert result["evidence"]["competition_risk"][0]["url"] == "https://a.test/3"
    all_urls = {
        item["url"]
        for entries in result["evidence"].values()
        for item in entries
    }
    assert all_urls == {"https://a.test/1", "https://a.test/2", "https://a.test/3"}
    set_mock.assert_awaited_once()


def test_research_tool_lists_include_company_moat_analysis():
    from src.llm import PAPER_TRADING_TOOL_NAMES, USER_FACING_TOOL_NAMES

    assert "company_moat_analysis" in USER_FACING_TOOL_NAMES
    assert "company_moat_analysis" in PAPER_TRADING_TOOL_NAMES
