"""Generic execution-backend seam.

buy()/sell() (src/tools/paper_trading_actions.py) do the business-rule
checks that apply no matter how a trade actually gets executed --
horizon/allocation validation, position-count caps, cash-sufficiency
checks. This Protocol is the boundary "actually executing it" sits
behind, so those business rules never need to know or care which backend
is underneath.

Today the only implementation is PaperBroker (src/tools/paper_broker.py),
a simulated ledger with no real money. A future real-broker
implementation (e.g. 永豐金 Shioaji) would implement this same shape.

Two things are known in advance NOT to carry over cleanly to a real
backend, written down here rather than guessed at now (there's no second
implementation yet to validate a guess against) -- see
docs/adr/0002-execution-backend-seam.md for the fuller discussion:

1. Real order execution is asynchronous. Shioaji submits an order and then
   reports fills via a callback (PendingSubmit -> Submitted -> Filled /
   Cancelled / Failed) -- it does not hand back a final price synchronously
   the way get_stock_price() + an instant DB write does for paper trading.
   A real backend implementing execute_buy()/execute_sell() would need
   either an internal wait-for-fill, or this Protocol needs to grow a
   pending/async state.
2. get_cash() here is whatever ledger the backend keeps. PaperBroker's is a
   simulated ledger replayed from a fixed paper_trading_starting_capital
   (see src/agents/paper_trading.py). A real backend's cash is whatever the
   broker's account API reports right now -- there's no "starting capital"
   concept, so allocation sizing built around a fixed base (calc_allocation)
   would need rethinking too, not just the execution call.
"""

from __future__ import annotations

from typing import Protocol


class Broker(Protocol):
    async def get_cash(self) -> float: ...

    async def get_positions(self, horizon: str | None = None) -> list[dict]: ...

    async def execute_buy(
        self,
        symbol: str,
        price: float,
        shares: int,
        allocation_amount: float,
        reason: str,
        horizon: str,
    ) -> int | None: ...

    async def execute_sell(self, position_id: int, price: float, exit_reason: str) -> None: ...
