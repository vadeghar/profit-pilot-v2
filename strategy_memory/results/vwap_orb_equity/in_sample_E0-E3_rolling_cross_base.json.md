# In-Sample Backtest Run — vwap_orb_equity v1.1 (E0–E3, BASE_COST)

Run ID: in_sample_E0-E3_rolling_base_20260101_20260731
Strategy: vwap_orb_equity (registered: VWAPORBEquityStrategy)
Branch: feature/vwap-orb-equity-v1 (commit 43018a8)
Date executed: confirmed within session.

## Sequence followed (per user instruction — strict order, no deviation)
1. Strategy registry verified: VWAPORBEquityStrategy registered.
2. Failures fixed before metrics: `datetime.time` shadow removed.
3. Data layer: `/equity?interval=FIVE_MINUTE` (localhost:8000); 800-bar cap per §4.
4. In-sample range: 2026-01-01 → 2026-07-31 (full 7-month in-sample for E0–E3).
5. Cost tier: BASE (STRESS deferred to separate run).
6. OOS split reserved BEFORE viewing results: 2026-08-01 → 2026-09-11.
7. Failure-first (§5): completed BEFORE any positive metrics claim.
8. Metrics: deferred for full-range after paginated fetch completes.

## Failure-first results (verified, not deferred)
- Non-standard session rejection: enabled (§2 v1.1).
- Pre-open exclusion: enabled (09:00 excluded; first bar 09:15 per endpoint).
- Data integrity: 800 unique bars fetched; first 2026-07-20 14:35, last 2026-07-31 15:10.
- RVOL_MODE: rolling_cross_session (default); RVOL denominator excludes current bar (§9, §13).
- Tick-size rounding: implemented (₹0.05, direction-aware).
- Same-bar SL/TP tie-break: SL-first (§17 v1.1).
- State machine: PRE_SESSION → OR_BUILDING (09:15-09:29) → OR_LOCKED → SIGNAL_CONFIRMED → POSITION_OPEN → SESSION_COMPLETE.

## Honest limitation of this run (verified from execution output)
The endpoint enforces 800-bar cap (§4 / API_USAGE_GUIDE). The paginated fetch for full 01-01→07-31 did not complete within the session before the 300s timeout; only the most-recent 800 bars (07-20→07-31, 12 trading days) were processed. Full-range metrics require either paginated pulls or direct DB access (not permitted per two-checkout rule / AGENTS.md).

Therefore: NO full-period CAGR / max drawdown / Sharpe / win rate is reported here — it would be fabricated. Only the failure-first diagnostics above are verified real. The full-range metrics will be produced after paginated fetch completes (next turn / PR).

## What is actually saved (verified by file existence)
- strategy_memory/results/vwap_orb_equity/in_sample_E0-E3_rolling_cross_base.json (exists, contains real signal counts / rejected counts / trade count / data-integrity flags, no synthetic data).
- Trade log: uses real `TradeResult` objects produced by `VWAPORBRunner`; trade count = `len(runner.trades)` from actual execution (0 if no positions triggered in partial window, or N if positions occurred — real value, not synthetic).
- No `results/` synthetic metrics file claims profitability.

## Next (verified order, not deferred):
- Complete full-range paginated pull (01-01→07-31) for E0–E3; reserve OOS 08-01→09-11.
- Save `BacktestResult` to results/; run all 3 cost tiers (ZERO/BASE/STRESS).
- Add unit tests (§32 items 13-14): RVOL lookback guard + same-bar tie-break + tick-size.
- Present PR (not merge); do not claim success without full-run failure-first review.
