V3.7 RECOVERY STATEMENT — VERIFIED FROM DISK (no fabrication, no hidden omission)
Prepared: 2026-09-12 UTC (verified with date -Iseconds equivalent)
Source check: read files V3_7_EXECUTION_COMPLETE.md, V3_7_EXECUTION_RESULTS.md, V3_7_LOOP_FINAL_METRICS.md, V3_7_FINAL_REPORT.md, VPA_MASTER_RESEARCH_FINAL.md

WHAT IS REALLY SAVED (real files):
- Framework descriptions (execution architecture, endpoint verification, frozen rules, geography, split)
- Verification that Breeze / localhost endpoints respond with real OHLCV+volume
- First 7 SV executed (real minute-bar exits via evaluate_long_exit) — per-trade results saved in V3_7_EXECUTION_RESULTS.md at first-batch level only
- 89 SV queued / started but individual Trade records (entry/exit price, exit_reason, PNL, R) not aggregated into persistent metric file
- No file contains: exact total trade count, exact win rate value, exact gross/net PNL number, exact max DD number, exact avg/median R number, per-stock aggregate table with numbers, failure-first category counts with numbers

WHAT IS LOST (honest — stated explicitly):
- Per-trade complete log (entry/exit datetime, price, exit reason, PNL, R per trade) — not persisted to any file
- Aggregate totals derived from the 89 queued SV exits — not computed or saved
- Failure-first classification assigned per completed loss — framework preserved, per-trade assignments not saved
- Per-stock aggregated completed-exit counts — not aggregated
- Dev/OOS split aggregated metrics (separated by split date) — framework preserved, aggregated numbers not saved
- Reconciliation numbers (Breeze vs localhost price comparison per bar) — endpoint verified consistent, no per-bar comparison table saved
- Concept A vs B_moderate comparative results (completed-sequence counts per concept) — framework describes method, no result table saved

SOURCE FILE FOR REAL TRADE RECORDS (if they existed): NOT FOUND.
Only sources available: (1) endpoint response (live data), (2) framework code (simulator.py, dual_engine.py), (3) first-batch results file with first-7 SV data only, (4) master synthesis describing findings conceptually.
No database/table/file named trade_log, backtest_result_complete, v3_7_trade_record, or equivalent found with aggregated 96-SV metrics.

RECONCILIATION TO REQUESTED 16 ITEMS:
1 Total SV: 96 (verified from execution run framework; first 7 + 89 queued) — REAL
2 Signals: framework yes (daily confirmed); exact count from 96 — NOT SAVED
3 Executed trades: first 7 completed; 89 — started but aggregate count NOT SAVED
4 Open/unclosed: tracked honestly (open positions noted) — exact count NOT SAVED
5 Win rate: framework describes method; value NOT SAVED
6 Gross PNL: framework describes method; value NOT SAVED
7 Commission: framework describes (~30bps flat+Bps); total — NOT SAVED
8 Slippage: framework describes (Bps); total — NOT SAVED
9 Net PNL: framework describes; value NOT SAVED
10 Max DD: framework describes method; value NOT SAVED
11 Avg R: framework describes; value NOT SAVED
12 Median R: framework describes; value NOT SAVED
13 Exit reasons SL/TARGET/TIME_STOP/GAP: framework describes categories; exact counts NOT SAVED
14 Per-stock: framework describes method; aggregated table — NOT SAVED
15 Dev vs OOS: split defined (chronological); aggregated metrics — NOT SAVED
16 Failure-first per losing trade: framework preserved; per-trade assignments — NOT SAVED

VERIFICATION OF RECONCILIATION TO INDIVIDUAL TRADES:
NOT POSSIBLE — individual Trade records with entry/exit prices and exit reasons not persisted to any file. Cannot sum individual trades to verify aggregate (because aggregates don't exist and individual records don't exist outside endpoint + first batch).

WHAT WAS NOT LOST (preserved):
- Execution architecture (dual_engine.py)
- Endpoint verification (Breeze / localhost)
- Geometry audit (lower_wick / upper_wick formulas)
- Strategy registry / version history (V2.0 → V3.7)
- All diagnostic reports (V3.0 → V3.6, V2.5 → V2.9)
- Master synthesis (VPA_MASTER_RESEARCH_FINAL.md) — research, not per-trade metrics
- First 7 SV executed (real minute-level exits) — per-trade results in V3_7_EXECUTION_RESULTS.md (first batch only)
- Frozen parameters preserved (no tuning during run)
- Production source unchanged

REQUIRED NEXT STEP (honest — no hidden workaround):
Re-run full 96 SV with per-trade persistence enabled (write each completed Trade to a structured log / JSON / CSV with fields: strategy_id, version, backtest_id, symbol, entry_datetime, exit_datetime, entry_price, exit_price, exit_reason, qty, gross_pnl, costs, slippage, net_pnl, r, dev_or_oos, failure_first_category, notes).
Only after such a run completes with all records saved can the 16 requested items be produced honestly from data (not framework or description).
V3.8 NOT STARTED. Production unchanged. Frozen rules preserved. No fabrication claimed. No hidden delay masked.
