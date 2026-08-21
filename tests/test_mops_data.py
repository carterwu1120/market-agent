"""Expected values below are hand-computed from a fake whole-market dump I
construct in each test, not by running the batch functions first and copying
their output. The one real external boundary (_fetch_market_dump's HTTP call)
is mocked; the matching/filtering logic itself is exercised for real.
"""
import pytest

from src.tools import mops_data


@pytest.fixture
def fake_material_dump():
    return [
        {"公司代號": "2330", "發言日期": "1150821", "發言時間": "090000", "主旨": "台積電公告A"},
        {"公司代號": "2330", "發言日期": "1150821", "發言時間": "103000", "主旨": "台積電公告B"},
        {"公司代號": "1101", "發言日期": "1150821", "發言時間": "094500", "主旨": "台泥公告"},
        {"公司代號": "9999", "發言日期": "1150821", "發言時間": "080000", "主旨": "不相關公司公告"},
    ]


@pytest.mark.asyncio
async def test_material_info_batch_groups_multiple_items_per_symbol(
    monkeypatch, fake_material_dump
):
    async def fake_fetch(url):
        return fake_material_dump

    monkeypatch.setattr(mops_data, "_fetch_market_dump", fake_fetch)

    result = await mops_data.get_material_info_batch(["2330.TW", "1101.TW"])

    assert [item["subject"] for item in result["2330.TW"]] == ["台積電公告A", "台積電公告B"]
    assert [item["subject"] for item in result["1101.TW"]] == ["台泥公告"]
    assert "9999.TW" not in result  # not requested, must not leak in


@pytest.mark.asyncio
async def test_material_info_batch_includes_requested_symbol_with_no_matches(
    monkeypatch, fake_material_dump
):
    async def fake_fetch(url):
        return fake_material_dump

    monkeypatch.setattr(mops_data, "_fetch_market_dump", fake_fetch)

    result = await mops_data.get_material_info_batch(["2330.TW", "2454.TW"])

    # docstring: "Keys match the input symbols list exactly" -- 2454 has no
    # announcements today but must still appear as a key with an empty list.
    assert result["2454.TW"] == []
    assert len(result["2330.TW"]) == 2


@pytest.mark.asyncio
async def test_material_info_batch_returns_empty_dict_on_fetch_failure(monkeypatch):
    async def fake_fetch(url):
        return None

    monkeypatch.setattr(mops_data, "_fetch_market_dump", fake_fetch)

    result = await mops_data.get_material_info_batch(["2330.TW"])
    assert result == {}


@pytest.fixture
def fake_financial_dump():
    return [
        {"公司代號": "2330", "公司名稱": "台積電", "營業收入": "1000"},
        {"公司代號": "1101", "公司名稱": "台泥", "營業收入": "200"},
    ]


@pytest.mark.asyncio
async def test_financial_summary_batch_only_includes_matched_symbols(
    monkeypatch, fake_financial_dump
):
    async def fake_fetch(url):
        return fake_financial_dump

    monkeypatch.setattr(mops_data, "_fetch_market_dump", fake_fetch)

    # docstring: "Keys match the input symbols list (only symbols with a
    # match present)" -- unlike material_info_batch, an unmatched requested
    # symbol must NOT appear as a key at all.
    result = await mops_data.get_financial_summary_batch(["2330.TW", "2454.TW"])

    assert result["2330.TW"]["營業收入"] == "1000"
    assert "2454.TW" not in result
