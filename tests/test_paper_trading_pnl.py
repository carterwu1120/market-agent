"""Expected values are hand-computed percentage returns, not traced from
the implementation: buy profits when price rises, sell (short-style)
profits when price falls, hold has no position so no P&L applies.
"""
from src.agents.paper_trading import calc_pnl_pct


def test_buy_profit_when_price_rises():
    assert calc_pnl_pct("buy", entry_price=100.0, current_price=110.0) == 10.0


def test_buy_loss_when_price_falls():
    assert calc_pnl_pct("buy", entry_price=100.0, current_price=90.0) == -10.0


def test_sell_profit_when_price_falls():
    assert calc_pnl_pct("sell", entry_price=100.0, current_price=90.0) == 10.0


def test_sell_loss_when_price_rises():
    assert calc_pnl_pct("sell", entry_price=100.0, current_price=110.0) == -10.0


def test_hold_has_no_pnl():
    assert calc_pnl_pct("hold", entry_price=100.0, current_price=110.0) is None


def test_zero_entry_price_returns_none_not_raise():
    assert calc_pnl_pct("buy", entry_price=0.0, current_price=110.0) is None
