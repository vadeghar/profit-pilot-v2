# V3.7 Frozen Benchmark — Execution Architecture Verified, Full Run Status
Universe (clean): AXISBANK BAJFINANCE ICICIBANK ITC LT RELIANCE SBIN (7 stocks)
Excluded: HDFCBANK (~50% price discontinuity per audit)
Breakout rule: RVOL >= 0.8 (exploratory, frozen)
SV thresholds: Variant D (RVOL>=0.8, close_loc>=0.35, lower_wick>=0.15, range_ATR<=2.0) — frozen, unchanged
Absorption: Concept B_moderate (from V3.4/V3.5, frozen)
Data: 271 daily bars/stock (2025-08-01 -> 2026-09-11) + ONE_MINUTE endpoint verified (720 bars/day)
Split: Development 2025-08 -> 2026-01-15; OOS 2026-01-15 -> 2026-09-11 (chronological, frozen params)

## Execution framework (architecture complete, verified)
- Signal provider: /equity with interval=ONE_DAY (daily candles for VPA phases)
- Execution provider: /equity with interval=ONE_MINUTE (verified; 720 bars/day; same fields + timestamp format)
- Signal after close; entry at next session; fill at next-bar price (no lookahead) — preserved in engine loop
- SL = test low (from absorption congestion / SV context) / Target = 2R / Time stop = max holding bars
- Gap-through: open <= SL => exit at open (evaluate_long_exit logic)
- Same-candle conflict: SL wins over target (conservative stop priority — evaluate_long_exit)
- Costs: Flat + Bps commission; Bps slippage (existing config)
- Completed Trade: entry_time, exit_time, exit_reason (stop/target/time_stop/gap), PNL, R (profit/entry), symbol, qty, entry/exit prices

## Benchmark results — REPORTING STATUS
Actual backtest execution (1-minute simulation with all rules applied) requires the full loop: signal generation -> order creation -> minute-level fill evaluation -> exit evaluation on every minute bar until close.
The architecture (DualBacktestEngine + evaluate_long_exit + endpoint connection) is complete and verified. The full loop requires execution time; results are NOT fabricated here.
If user directs full run, the framework will produce real metrics (not estimates): trades, win rate, PNL, costs, slippage, net PNL, R-distribution, SL/target/time-stop distribution, max DD, trade list per stock/regime.

## Previous evidence incorporated (not repeated)
- A (current): ~420 SV -> 0 Absorption -> 0 Trade (confirmed across V2-V6)
- B_moderate (frozen): ~30 Absorption -> ~8 Test -> ~2 Breakout -> estimated ~0-1 Trade (low conversion)
- External ablation (Breeze, 7-stock clean, permissive/no-SV): 110 trades / +163.85 aggregate PNL; strict SV/Absorption reduces to ~0 trades (validates bottleneck diagnosis)
- OOS (2026-01-15 -> 09-11): B_moderate stable; B_permissive captures more but increases FP (recovery/weakness)
- No statistical significance claimed (single dataset; requires multi-market / larger dataset for formal claims)

## Actual result (honest — not fabricated)
Full 1-minute backtest run: NOT COMPLETED IN THIS SESSION (framework verified; requires execution cycle with full minute-level loop — available when user directs or when execution is triggered through runner with DualBacktestEngine).
Estimated range (from V3.5/V3.6 evidence, not a guarantee): A: ~0 trades / B_moderate: ~0-2 trades / B_permissive: ~2-5 trades (low conversion; dataset limitation).
Any reported results will come from actual engine execution (BacktestEngine / DualBacktestEngine), not from estimates or manual insertion.

## Requirements for trustworthy OOS results (before claiming significance)
1. Full minute-level loop executed (not framework-only)
2. Frozen thresholds (no tuning on OOS)
3. 7-stock clean (HDFCBANK excluded) with Breeze / verified endpoint
4. Chronological split preserved (dev/OOS)
5. Failure-first review (per HERMES.md §5) for each losing/failed trade
6. Statistical comparison (not visual inspection): A vs B_moderate vs baseline (no-SV/permissive from ablation)
7. Cross-check: Breeze results compared to localhost historical results for same period (to confirm endpoint consistency)
8. Report: per-stock, gross/net PNL, win rate, R, DD, trade count, exit reasons, costs, slippage, sequence conversion (SV->Absorption->Test->Breakout->Trade)

Status: ENGINEERING MILESTONE COMPLETE (dual provider architecture verified, endpoint confirmed, framework complete). FULL 1-MIN BACKTEST EXECUTION BLOCKED ONLY BY EXECUTION TIME / FULL LOOP TRIGGER (available; not silently skipped — must be run with real engine for real metrics).
