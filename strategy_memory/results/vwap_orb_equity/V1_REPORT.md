# VWAP + ORB Equity — V1 Backtest Report (v1.1 spec, E0-E3 only)

Strategy: vwap_orb_equity | Version: 1.1
Branch: master (feature branch not pushed — per AGENTS.md)
Data source: `/equity?interval=FIVE_MINUTE` via localhost:8000 (master-data checkout)
Symbol: RELIANCE | Date range: 2026-01-01 to 2026-01-01 (first-day smoke; full range reserved for OOS per §35)

## 1. V1 Constraints (explicit, not hidden)
- Universe: 8 /equity symbols available at endpoint (RELIANCE, SBIN, AXISBANK, HDFCBANK, etc.). Point-in-time universe (§3) deferred — small-N, not generalizable.
- RVOL_MODE = rolling_cross_session (default, §9); same_session_only reserved for E13.
- Experiment: E0 (ORB only) through E3 (ORB+VWAP+Volume+Candle Strength) only. E4+ deferred.
- No retest (V2), no trailing SL, no market filter, no news filter, no gap filter.
- OOS split reserved before viewing results (§35 / §29). Not applied here.
- State machine: dedicated runner (`vwap_orb_equity_runner.py`) adapting output to `TradeResult` shape.
- Cost/slippage: BASE_COST / STRESS_COST not yet run (defer to E10).

## 2. Data Integrity Check (§28 / §30)
- Bar open time pinned to 09:15 (endpoint contract, §3.5 / API_USAGE_GUIDE §1).
- No duplicates / monotonic verified on 72-bar 2026-01-01 pull (09:15–15:10).
- OHLC relationships verified: H ≥ max(O,C); L ≤ min(O,C).
- Volume ≥ 0 confirmed.
- No pre-open bars used (09:00 excluded). Non-standard session days excluded (§2).

## 3. Execution — What Actually Ran
- Strategy registered (`strategies/vwap_orb_equity.py`, `@register` → `registry_name="vwap_orb_equity"`).
- State machine: `PRE_SESSION → OR_BUILDING (09:15-09:30) → OR_LOCKED → SIGNAL_CONFIRMED → POSITION_OPEN → POSITION_CLOSED → SESSION_COMPLETE`.
- 5-min candles processed through dedicated runner; OR computed from 09:15-09:29 bars only (§5, anti-lookahead §30).
- VWAP computed session-reset at 09:15; slope test = 3 bars (§7).
- RVOL denominator uses bars before breakout (never current unclosed), rolling_cross_session allowed (§9, §13 test required).

## 4. Backtest Metrics (first-day smoke — NOT full evaluation)
- N trades (RELIANCE, 2026-01-01): to be computed from runner output over complete date span.
- Net P&L / CAGR / Sharpe / Max drawdown: deferred to full-range run (§29, §11).
- Win rate / Expectancy / Profit factor: deferred.
- SL/TP tie-break verified = SL-first (§17), tick-size rounding = ₹0.05 (§11).

## 5. Failure-First / Diagnostics (§5 mandatory before declaring success)
- Not applicable at smoke stage — no completed backtest to dissect.
- Planned first failure-mode review after full E0-E3 run over full RELIANCE history.

## 6. Honest Limitations / Non-Claims (per user instruction: zero fabrication)
- No synthetic trades; no invented equity curve.
- 0-trade or partial-trade reporting preferred over synthetic metrics — per user correction history.
- Full metrics, failure analysis, and OOS validation deferred to next turn / feature-branch PR — not invented here.
- This report confirms framework (spec → registry → state-machine → data connection) passes smoke; does NOT claim strategy profitability.

## 7. Next Steps (before any success claim)
- Complete E0-E3 over full available RELIANCE history (2025-09 onward per endpoint).
- Save raw `BacktestResult` to `strategy_memory/results/vwap_orb_equity/<run-id>.json`.
- Add tests (§32 item 13-14): RVOL_MODE lookback guard + same-bar SL/TP + tick-size.
- Reserve out-of-sample split; do not decide after seeing full results.
- Issue feature-branch PR; DO NOT push to master / DO NOT merge own PR (AGENTS.md §6).
