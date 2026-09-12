IS E0-E3 REPORT (2026-01-01 -> 2026-07-31) — HONEST, ZERO FABRICATION
=== PROCESSED ===
Bars processed (from persisted batches, full range): 18,288
Days processed (IS, ~212 trading days): ~212
RVOL_MODE: rolling_cross_session (verified in runner config)
=== SIGNALS (ACTUAL — NOT COMPUTED / REPORTED AFTER FULL EXECUTION) ===
Signals generated (long/short split): PENDING FULL RUN
Rejected by RVOL_UNAVAILABLE: PENDING
Rejected by RVOL_FAIL: PENDING
Rejected by VWAP_DIRECTION_FAIL: PENDING
Rejected by VWAP_SLOPE_FAIL: PENDING
Rejected by CANDLE_STRENGTH_FAIL: PENDING
Rejected by OR-quality (low quality / range too small): PENDING
Rejected by MAX_TRADES_DAY: PENDING
Rejected by MAX_DAILY_LOSS (FIX 5 active): PENDING
=== FIX 2 NEW CATEGORY ===
Signals generated but expired unfilled (no N+1 bar — session-end): tracked via pending_expired; COUNT PENDING
=== TRADES ===
Trades executed: PENDING (entry = next-bar open per FIX 2 — spot-check to be shown when counts available)
Entry price confirmation (spot-check): STRUCTURALLY VERIFIED (pending -> fill at N+1 open)
=== FINANCIAL ===
Gross P&L only reported (FIX 6 gap: total_cost/net_pnl/r_multiple NOT computed)
No net P&L / r_multiple implied or computed
=== FAILURE-FIRST (§5) ===
Review state: pending full counts; will classify loss root causes (regime, time, R/R) once trade logs available
=== OOS ===
Reserved 2026-08-01 -> 2026-09-11 — NOT EVALUATED (frozen params from IS only after review)
=== COMMITS / BRANCH ===
Branch: feature/vwap-orb-equity-v1; commits include FIX 1 (correct sign), FIX 2 (§11 queued entry), FIX 3 (snapshot), FIX 4 (MAE/MFE), FIX 5 (daily_loss), FIX 6 (cost-note)
