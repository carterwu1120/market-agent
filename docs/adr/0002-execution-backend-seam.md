# Introduce a Broker seam between trading business rules and execution

`paper_trading_actions.py`'s `buy()`/`sell()` used to do two different things in one function:
business-rule checks that apply no matter how a trade actually happens (horizon/allocation
validation, position-count caps, cash-sufficiency checks) and the actual execution (fetch a real
price, write directly to `paper_positions`). The user wants to eventually be able to plug in a
real broker (永豐金 Shioaji is the one under consideration) without rewriting the business rules,
so the two were split behind a generic `Broker` `Protocol` (`src/tools/broker.py`): `get_cash()`,
`get_positions()`, `execute_buy()`, `execute_sell()`. `PaperBroker` (`src/tools/paper_broker.py`)
is the only implementation today — a simulated ledger with no real money, doing exactly what
`buy()`/`sell()` did before. `paper_trading_actions.py` now holds a module-level
`_broker: Broker = PaperBroker()` and only ever calls the four Protocol methods.

We deliberately did **not** design this Protocol by imagining what a real-broker implementation
would need — there's no second implementation yet to validate the shape against, and guessing
risks getting the async-order-lifecycle bits wrong (see Consequences). The shape here only
captures what `PaperBroker`'s existing behavior already needed.

## Considered Options

- **Write the interface later, when real-broker work actually starts.** Rejected per explicit user
  request: the naming/structure of `paper_trading_actions.py` should not hard-code "the execution
  is always a simulated DB write" even before a second backend exists, so that adding one later is
  a new class, not a rewrite of the business-rule code.
- **Design the Protocol around Shioaji's actual async order/callback model now.** Rejected for this
  pass — no real integration work has started, and designing for a callback-driven fill model we
  haven't actually built against risks over-fitting to assumptions that turn out wrong once real
  work begins. Documented as an open gap instead (see below).

## Consequences

- New files: `src/tools/broker.py` (the `Broker` Protocol, generically named — not
  paper-specific — since a real backend will implement the same shape) and
  `src/tools/paper_broker.py` (`PaperBroker`, the simulated implementation; "paper" naming is
  reserved for this concrete implementation, not the interface).
- `paper_trading_actions.py`'s `buy()`/`sell()` are unchanged in behavior (all 96 existing tests
  pass without modification) but now depend only on `Broker`'s four methods, not on
  `paper_trading_store.py`'s `open_position`/`close_position`/`get_open_positions` or
  `paper_trading.py`'s `get_available_cash` directly.
- `evaluate_paper_trades()`/`simulate_portfolio_equity()` (the `/performance`/`/status` reporting
  path) were **not** routed through `Broker` in this pass — reporting is a separate concern from
  execution, and a real backend's reporting would need to read from the broker's own account
  anyway (see below), which is a bigger change than this pass was scoped for.
- Two gaps are known and written down in `broker.py`'s docstring rather than solved now:
  1. **Real order execution is asynchronous.** Shioaji submits an order and reports fills via a
     callback (`PendingSubmit` → `Submitted` → `Filled`/`Cancelled`/`Failed`), not a synchronous
     return value. `Broker.execute_buy()`/`execute_sell()` currently assume a synchronous
     fill-at-fetched-price, which only holds for `PaperBroker`. A real implementation needs either
     an internal wait-for-fill or the Protocol needs an async/pending state added — deferred until
     real integration starts.
  2. **`get_cash()`'s meaning differs by backend.** `PaperBroker`'s is a simulated ledger replayed
     from a fixed `paper_trading_starting_capital` (see `src/agents/paper_trading.py`). A real
     backend's cash is whatever the broker's account API reports right now — there's no "starting
     capital" concept, so `calc_allocation()`'s "% of a fixed base" sizing model would need
     rethinking too, not just the execution call.
- Automation-risk posture (mechanical stop-loss/conditions firing without human confirmation,
  generous position caps as a "safety net not an operational limit") was explicitly designed
  around "no real money at risk." None of that risk calibration was revisited here — swapping in
  a real `Broker` implementation later must not be read as "therefore safe to also keep full
  automation," and deserves its own explicit discussion when real integration is actually planned.
