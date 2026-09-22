"""Exercises the capital-simulation layer added alongside the existing
size-agnostic win_rate/avg_return_pct stats: calc_allocation() (clamping +
share sizing), get_available_cash() (cash ledger replay), and
simulate_portfolio_equity() (equity/drawdown view). Expected values are
independently computed by hand from the documented contract in
src/agents/paper_trading.py's docstrings, not traced from the arithmetic
under test.
"""
import pytest

from src.agents.paper_trading import calc_allocation, get_available_cash, simulate_portfolio_equity
from src.config import settings
from src.memory.paper_trading_store import close_position, open_position
from src.memory.store import init_storage


@pytest.fixture(autouse=True)
async def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "paper_trading_starting_capital", 500_000.0)
    monkeypatch.setattr(settings, "paper_trading_min_allocation_pct", 5.0)
    monkeypatch.setattr(settings, "paper_trading_max_allocation_pct", 20.0)
    await init_storage()


# ── calc_allocation ─────────────────────────────────────────────────────

def test_allocation_within_bounds_is_used_as_given():
    # 10% of 500,000 = 50,000 budget at price 100 -> 500 shares
    shares, amount = calc_allocation(10.0, 100.0)
    assert shares == 500
    assert amount == 50_000.0


def test_allocation_above_max_is_clamped_down():
    # requested 50% clamps to max 20% -> 100,000 budget at price 100 -> 1000 shares
    shares, amount = calc_allocation(50.0, 100.0)
    assert shares == 1000
    assert amount == 100_000.0


def test_allocation_below_min_is_clamped_up():
    # requested 1% clamps to min 5% -> 25,000 budget at price 100 -> 250 shares
    shares, amount = calc_allocation(1.0, 100.0)
    assert shares == 250
    assert amount == 25_000.0


def test_allocation_too_small_for_expensive_stock_yields_zero_shares():
    # min 5% of 500,000 = 25,000 budget; price 30,000 -> 0 shares (int division)
    shares, amount = calc_allocation(5.0, 30_000.0)
    assert shares == 0
    assert amount == 0.0


# ── get_available_cash ──────────────────────────────────────────────────

async def test_available_cash_equals_starting_capital_with_no_positions():
    assert await get_available_cash() == 500_000.0


async def test_available_cash_drops_by_committed_amount_of_open_position():
    await open_position(
        "2330.TW", entry_price=100.0, horizon="short_term", shares=500, allocation_amount=50_000.0
    )
    assert await get_available_cash() == 450_000.0


async def test_available_cash_reflects_realized_pnl_after_close():
    position_id = await open_position(
        "2330.TW", entry_price=100.0, horizon="short_term", shares=500, allocation_amount=50_000.0
    )
    await close_position(position_id, exit_price=120.0, exit_reason="take_profit")
    # bought 500 @ 100 (-50,000), sold 500 @ 120 (+60,000) -> net +10,000
    assert await get_available_cash() == 510_000.0


# ── simulate_portfolio_equity ───────────────────────────────────────────

def test_equity_with_no_positions_equals_starting_capital():
    result = simulate_portfolio_equity([])
    assert result["current_equity"] == 500_000.0
    assert result["total_return_pct"] == 0.0
    assert result["realized_max_drawdown_pct"] == 0.0


def test_missing_quote_does_not_treat_position_as_total_loss():
    result = simulate_portfolio_equity([{
        "status": "open", "symbol": "2330.TW", "shares": 500,
        "entry_price": 100.0, "current_price": None,
    }])
    assert result["current_cash"] == 450_000
    assert result["current_equity"] is None
    assert result["total_return_pct"] is None
    assert result["missing_quotes"] == ["2330.TW"]


def test_equity_marks_open_position_to_market():
    positions = [{
        "status": "open", "symbol": "2330.TW", "shares": 500,
        "entry_price": 100.0, "current_price": 120.0,
    }]
    result = simulate_portfolio_equity(positions)
    # cash: 500,000 - 500*100 = 450,000; open value: 500*120 = 60,000
    assert result["current_cash"] == 450_000.0
    assert result["open_positions_value"] == 60_000.0
    assert result["current_equity"] == 510_000.0
    assert result["total_return_pct"] == 2.0


def test_realized_drawdown_reflects_a_losing_trade_after_a_winner():
    positions = [
        {
            "status": "closed", "symbol": "2330.TW", "shares": 500,
            "entry_price": 100.0, "exit_price": 120.0, "exit_date": "2026-01-01",
        },
        {
            "status": "closed", "symbol": "2454.TW", "shares": 500,
            "entry_price": 100.0, "exit_price": 80.0, "exit_date": "2026-01-02",
        },
    ]
    result = simulate_portfolio_equity(positions)
    # peak after trade 1: 500,000 + 10,000 = 510,000
    # after trade 2 (loss of 10,000): running = 500,000
    # drawdown = (510,000 - 500,000) / 510,000 * 100 = 1.96%
    assert result["realized_max_drawdown_pct"] == pytest.approx(1.96, abs=0.01)
    assert len(result["equity_curve"]) == 2
