# Step 10 — Backtest Runner & Execution Correctness (strategy-agnostic)

## Status
- [x] 1. Write this plan
- [ ] 2. Implement commission model (flat / bps) + unit tests (RED)
- [ ] 3. Implement slippage model (adverse bps) + unit tests (RED)
- [ ] 4. Implement `BacktestRunConfig` (frozen dataclass)
- [ ] 5. Implement `BacktestRunner` orchestration (config -> provider -> strategy -> engine -> result)
- [ ] 6. Implement `MarketDataProvider` backed by `data/market_data.duckdb` (read-only)
- [ ] 7. Extend `BacktestEngine`: fill-on-next-bar, rolling `SignalContext.history`, commission+slippage, cash-overdraw rejection, equity curve
- [ ] 8. Implement slim `research/metrics.py` (total return, max drawdown, Sharpe, trade count, win rate) + unit tests (RED)
- [ ] 9. Extend `BacktestResult` with equity curve + per-trade list
- [ ] 10. End-to-end runner test over a frozen lake slice (RED then GREEN)
- [ ] 11. Assert lake is unmodified after runs (row counts unchanged)
- [ ] 12. Full `pytest` green

## Current state (verified)
- Frozen, validated lake: `data/market_data.duckdb`
  - `nifty_spot`: 742,995 rows, 2024-01-01 -> 2026-09-08
    - cols: trade_time TIMESTAMP, trade_date DATE, open/high/low/close DOUBLE, volume BIGINT, source_file
  - This step reads `nifty_spot` ONLY. `options_ticks` is out of scope (see Non-goals).
- `backtest/engine.py` — `BacktestEngine.run` exists but: fills on the SAME bar as the signal (lookahead-leaky), uses `on_market_state` (no history window), no cash-overdraw check, no commission/slippage.
- `backtest/commission.py`, `backtest/slippage.py`, `backtest/clock.py`, `research/metrics.py`, `data/duckdb_reader.py` are EMPTY stubs — this step implements them.
- `backtest/results.py` — `BacktestResult(initial_cash, final_cash, fills)` with `pnl` only. No equity curve, no per-trade list.
- `ExecutionSimulator.fill()` fills at `state.price` with no slippage/commission applied.
- `Portfolio.apply_fill()` allows NEGATIVE cash (no rejection).
- Strategy layer is complete & green (Step 9). `Strategy.on_signal_context(ctx) -> Signal` and `on_market_state(state) -> Signal` both exist.

## Design decisions (locked)
1. **Single combined step.** Runner + execution correctness (commission/slippage/cash/history-window) + metrics + equity curve in one STEP_10. Split into STEP_10/STEP_11 noted as alternative if review wants smaller chunks.
2. **Fill on next bar (behaviour change).** A signal produced at bar *t* is filled at the next bar's price (t+1). This removes same-bar lookahead. `on_market_state`-driven same-bar fill is retired by the runner. See Behaviour changes.
3. **Strategy-agnostic.** NO trading strategy is introduced by this step. No strategy code in the package or in tests. Tests drive the engine with trivial deterministic signal emitters defined inline in the test modules (e.g. "hold -> buy N at bar k"). The existing `strategies/ma/` is not registered, imported, logged, or depended on by any Step 10 path.
4. **Frozen dataset is READ-ONLY.** All duckdb access uses `duckdb.connect(..., read_only=True)`. No rows/schema/derived tables are ever written back to the lake. Backtest outputs (fills, equity, metrics) are plain Python dataclasses, persisted (if at all) under `backtests/`, never under `data/`.
5. **Cash-overdraw rejection.** Orders that would push `Portfolio.cash < 0` are rejected (not executed), and the reject is recorded. Backtest stays deterministic and non-leveraged for spot. (Leverage/margin is a later option-products concern — config flag deferred.)
6. **Slippage is adverse.** BUY adjusts price UP by slippage, SELL adjusts DOWN. Rule-based (deterministic, no randomness).
7. **Commission** = flat-per-order and/or bps of notional. Zero config => zero cost.
8. **Metrics convention.** Sharpe annualizes via `bars_per_year` from config (default ~ 261 * (6.5 * 60) = 101,790 for 1-min Nifty spot bars; grossly simplified, document as such). Zero-vol symmetric return series => Sharpe 0.0 (no NaN/Inf; results stay serializable). Max drawdown computed from the period-ending equity curve.

## New components / interfaces
- `backtest/run.py` — `@dataclass(frozen=True) BacktestRunConfig`:
  `symbol: str`, `start: date`, `end: date`, `initial_cash: float`,
  `commission_flat: float = 0.0`, `commission_bps: float = 0.0`,
  `slippage_bps: float = 0.0`, `strategy_params: Mapping = field(default_factory=dict)`,
  `bars_per_year: int = 101_790`.
  Validates non-negative cash / non-negative rates.
- `data/duckdb_reader.py` — `DuckDBProvider(MarketDataProvider)`:
  `states(start, end) -> list[MarketState]` reading `nifty_spot` ascending by `trade_time`, read-only.
- `backtest/commission.py` — `CommissionModel(Protocol)` with `compute(order, price) -> float`;
  `FlatCommission(flat)`, `BpsCommission(bps)`, `CompositeCommission(*models)`.
- `backtest/slippage.py` — `SlippageModel(Protocol)` with `adjust_price(side, price) -> float`;
  `BpsSlippage(bps)`. BUY => `price*(1+bps)`, SELL => `price*(1-bps)`.
- `backtest/engine.py` — extended `BacktestEngine.run(states, strategy, config)`:
  rolling `SignalContext(state, params, history)`, fill signal t at bar t+1 price
  (skip first bar / last signal), apply slippage then commission, reject cash-overdraw,
  accumulate `equity_curve[(t, equity)]` at each bar end (mark to market at that bar's price).
- `backtest/results.py` — extend `BacktestResult`:
  `equity_curve: Sequence[(datetime, float)]`, `rejected_orders: Sequence[Order]`,
  `trades: Sequence[Trade]`, `return_pct`, `max_drawdown`, `sharpe`, `n_trades`, `win_rate`.
  `Trade` (new, in `execution/trades.py` or backtest): `symbol, entry_time, exit_time, side, qty, entry_price, exit_price, pnl`.
- `backtest/runner.py` — `BacktestRunner(config)`:
  `.run() -> BacktestResult`; builds `DuckDBProvider`, opens provider, feeds states
  into `BacktestEngine`, computes metrics, returns result. Glue only, no strategy logic.

## Existing contracts kept stable
- `MarketDataProvider.states(start, end) -> Iterable[MarketState]` (unchanged Protocol).
- `Strategy.on_market_state(state) -> Signal` and `on_signal_context(ctx) -> Signal` (unchanged; runner drives strategies through `on_signal_context` with a maintained history window).
- `Order.from_signal`, `OrderSide`, `Fill`, `Position.apply`, `Portfolio.apply_fill` — unchanged (only the engine's cash guard is added around apply_fill).
- `Signal`, `SignalAction`, `SignalContext` — unchanged.

## Success criteria — `pytest` green
- Commission & slippage unit tests pass (flat, bps, composite, adverse direction, zero-config).
- Cash-overdraw: over-limit rejected, exact-limit allowed, cash never negative.
- History window: strategy invoked via `on_signal_context` sees newest-last rolling history.
- Metrics: hand-computed values on a tiny series (total return, drawdown, Sharpe 0-vol rule, trades/win rate).
- E2E: runner executes a deterministic inline signal emitter over a frozen `nifty_spot` slice; asserts >=1 fill, valid fills, positions form, final equity >= 0 comes back deterministic, equity_curve monotonic in time, `nifty_spot` row count unchanged.

## Test plan (RED -> GREEN)
See each component above; run order: commission -> slippage -> config -> metrics -> engine history/cash -> e2e -> lake integrity.

## Non-goals (later steps)
- Any real named trading strategy / signal logic (this step is strategy-agnostic by design).
- `options_ticks`, greeks, option fill semantics, margin/leverage.
- research/runner multi-run orchestration, hypothesis conformance, LLM orchestration.
- Live-data wiring; performance optimization of full-archive walks.

## Behaviour changes (vs current code)
- **BacktestEngine fills on NEXT bar instead of the signal bar** — removes lookahead. This is the intended behaviour change of the step.
- **Cash-overdraw orders are rejected** (previously cash could go negative).
- **Commission and slippage are now applied to fills** (previously ignored).
- **Strategies are driven via `on_signal_context` with a rolling history window** by the runner (previously `on_market_state`, single point, no window).
- `BacktestEngine.run` signature changes to accept a `BacktestRunConfig`; the old `(states, strategy, initial_cash)` call shape is replaced.