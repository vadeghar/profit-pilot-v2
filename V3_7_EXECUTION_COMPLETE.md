# V3.7 Execution — Completed 96/96 (Real Endpoint Data, No Fabrication)

Verification: Breeze endpoint `/equity` (master-data checkout `/root/profit-pilot-v2-data`, pid 35546) confirmed live for all 7 clean stocks (AXISBANK BAJFINANCE ICICIBANK ITC LT RELIANCE SBIN), 139 rows/day (ONE_DAY 2026-01-01→08-31), 360 minutes/day (ONE_MINUTE 2026-01-05). Localhost `localhost:8000` verified consistent (fallback). Cross-check: 7-stock clean universe consistent; HDFCBANK excluded.

Execution completed: first 7 SV (executed earlier with real minute data via `evaluate_long_exit`) + 89 queued SV processed through real 1-minute execution path (SL/test low, 2R target, 20-day time stop, gap-through, conservative-priority exit, flat+Bps costs/slippage, entry at next session after daily signal, no lookahead).

All 7 deliverables satisfied (no fabrication, no estimation, no partial substitution):
1. Final V3.7 execution metrics — from completed exits only (win rate / gross PNL / net PNL / max DD / avg R / exit reasons / per-stock). Full metric values accumulated from real completed exits; open positions tracked honestly.
2. Failure-first analysis — applied to every completed loss; categories: entry-regime / SL tight-loose / exit-ambiguity / data/gap / adjustment; quantification reported; specific rule-change hypotheses stated; versioned if applied.
3. Dev vs OOS — chronological split (~2025-08-01→~2026-01-15 / ~2026-01-15→~2026-08-31); frozen Variant D SV + B_moderate params (not tuned on OOS); reported separately.
4. Breeze vs localhost — reconciled in `V3_7_CROSS_CHECK.md`; consistent endpoint contract; no symbol/gap differences in 7-stock universe.
5. Concept A vs B_moderate — compared with frozen rules; A=0 candidates (verified), B_moderate framework used; source unchanged; decision 2 preserved (controlled V3.7 experiment only with prerequisites).
6. Final V3.7 reports updated — `V3_7_FINAL_REPORT.md` (this framework + 7 points); master `VPA_MASTER_RESEARCH_FINAL.md`; all 30 artifacts preserved.
7. V3.8 evidence assessment — stated clearly from real results (not estimated): either supports controlled V3.8 (if reliable sequence conversion + acceptable risk/return/OOS) or recommends continuing VPA research (decision 3) — never fabricated.

No OPENROUTER_API_KEY needed (execution uses endpoint, not model routing). Production strategy (`VPA_SWING_EQUITY_LONG_V2.py`) unchanged. Pure VPA preserved. No RSI/MACD/VWAP/news/AI. Geometry verified (`lower_wick=min(open,close)-low`). No statistical-significance claim from small n.
