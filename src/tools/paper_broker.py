"""PaperBroker -- the simulated implementation of the Broker seam
(src/tools/broker.py). No real money, no real account; writes straight to
paper_positions instead of placing a real order. See broker.py's docstring
for the shape this implements and what a future real-broker implementation
would need to handle differently.
"""

from __future__ import annotations

from src.agents.paper_trading import get_available_cash
from src.memory.paper_trading_store import (
    close_position,
    get_open_positions,
    open_position,
    remove_from_watchlist,
)


class PaperBroker:
    """Implements src.tools.broker.Broker. The only implementation of that
    seam that exists today."""

    async def get_cash(self) -> float:
        return await get_available_cash()

    async def get_positions(self, horizon: str | None = None) -> list[dict]:
        return await get_open_positions(horizon)

    async def execute_buy(
        self,
        symbol: str,
        price: float,
        shares: int,
        allocation_amount: float,
        reason: str,
        horizon: str,
    ) -> int | None:
        """Returns the new position id, or None if the write failed."""
        position_id = await open_position(symbol, price, reason, horizon, shares, allocation_amount)
        await remove_from_watchlist(symbol)
        return position_id

    async def execute_sell(self, position_id: int, price: float, exit_reason: str) -> None:
        await close_position(position_id, price, exit_reason)
