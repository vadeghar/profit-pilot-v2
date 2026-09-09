# Step 9 — Strategy Layer + First Real Strategies

## Status
- [x] 1. Write this plan
- [ ] 2. Install package editable + pytest (verify `profit_pilot` imports)
- [ ] 3. `Strategy` base: concrete abstract base with params + hooks
- [ ] 4. `Signal` engine: calc-position hook, HOLD/BUY/SELL mapping, validation
- [ ] 5. `SignalContext` (bars, indicators, params, bookkeeping)
- [ ] 6. Strategy registry (decorator, get, registered names)
- [ ] 7. First real strategies built on frozen data (Nifty spot + options)
- [ ] 8. Strategy unit tests (RED then GREEN)
- [ ] 9. Registry smoke test
- [ ] 10. Commit

## Current state (verified this session)
- Frozen, validated lake: `data/market_data.duckdb`
  - `nifty_spot`: 742,995 rows, 2024-01-01 → 2026-09-08
    - cols: trade_time TIMESTAMP, trade_date DATE, open/high/low/close DOUBLE, volume BIGINT, source_file
  - `options_ticks`: 46.16M rows, 2024-01-01 → 2026-08-25
    - cols: trade_time, trade_date, expiry_date, strike_price, option_type (CE/PE/INDEX/FUT), price, volume, open_interest, iv, delta, gamma, theta, vega (greeks nullable)
  - `expiry_calendar` (73), `holiday_calendar` (21)
- Backtest/execution layer exists (engine, simulator, commission/slippage stubs, portfolio/position/fill/order) but is NOT the target of this step.
- `profit_pilot` is NOT installed in `.venv` (only duckdb, no pytest). No `pyproject.toml`/`[project]` yet.
- All docs/README were empty placeholders; `llm/`, `research/runner`, `research/metrics` are empty — future steps.

## Deliverables
- `src/profit_pilot/strategy/base.py` — concrete-ish abstract base `Strategy` with `params` + `name` + pre-compute; keep compatibility with `Strategy(Protocol)` for existing backtest imports.
- `src/profit_pilot/strategy/signal.py` — extend with `SignalContext` + `Signal.compute` (action, symbol, quantity; BUY/SELL/HOLD; protects `strategy.signal.py` existing `Signal` fields).
- `src/profit_pilot/strategy/context.py` — `SignalContext`: state + params + history buffer + reference fields.
- `src/profit_pilot/strategy/registry.py` — `STRATEGY_REGISTRY`, `register` decorator, `get_strategy`, registered names.
- `strategies/` — first real strategies: e.g. `ma/__init__.py`, `ma/moving_average_cross.py` (Nifty spot), `ma/sma_breakout.py`; keep under repo `strategies/`.
- `tests/` — strategy layer unit tests + registry smoke test.

## Interface contracts (keep stable)
- `Strategy.on_market_state(state) -> Signal` (unchanged, used by backtest engine).
- `Signal(SignalAction, symbol, quantity, metadata)` with validate `__post_init__`.
- `Order.from_signal` maps SignalAction → OrderSide (BUY→BUY, SELL→SELL, HOLD→None).
- Registry keys by class name; `@register` decorator.

## Success criteria — tests pass (`pytest .venv`/editable install)
- Strategy emits BUY→order BUY; SELL→SELL; HOLD→None.
- Indicator params (fast/slow windows) applied; cross produces correct signal on a fixture series.
- Registry registers and retrieves MA strategy; unregistered raises.

## Non-goals (later steps)
- Wiring `research/runner`, metrics, LLM orchestration, backtest execution of strategies end-to-end.