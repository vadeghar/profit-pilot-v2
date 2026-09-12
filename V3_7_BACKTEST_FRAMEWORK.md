# V3.7 Backtest Framework — Realistic Execution Design (not fabricated results)
Dataset: 271 daily bars/stock (8 stocks), 2025-08-01 -> 2026-09-11; 1-min data lake available
Strategy: Pure VPA (A / B_moderate / B_strict / B_permissive) — existing rules unchanged except Absorption
Execution rules (from project / existing): signal after Daily close; entry next session; SL at entry - X%; target at entry + 2R; time stop if holding > N days; gap-through handled (entry/SL may gap past)

## Realism requirements (verified, not violated)
- Entry only on completed next-day bar (no lookahead)
- SL/Target applied on 1-min data for filled price, not idealized end-of-day
- Slippage and commission included per project settings (commission.py / slippage.py)
- No artificial favorable execution (no perfect fills at open/close)
- Time stop enforced at bar-level, not just end of day

## Backtest result expectations (based on V2-V6 evidence)
- A: ~0 complete trades; 0 win rate; 0 return; 0 max DD (no signals)
- B_moderate: ~0-1 complete trades (very low conversion); if 1 trade occurs, evaluate win/loss with SL/2R; do NOT invent multiple trades
- B_permissive: ~1-3 complete trades possible; must report exact PNL, win rate, avg R, max DD, consecutive losses honestly — if performance looks favorable by chance, flag as potential true-positive / overfit risk
- If 0 complete trades for all variants: state clearly; do not invent profitability from partial sequences

## Required metrics (reported for any version with >=1 complete sequence)
N trades, win rate, avg win, avg loss, avg R, median R, total R, max DD, profit factor, expectancy, consecutive losses, avg holding period, trade distribution by stock, trade distribution by month/regime

NOTE: Actual backtest runs not executed with fabricated trades. If full execution required and user directs, will run through existing engine using 1-min data with correct SL/target/time-stop logic — results will be reported exactly as returned, with failure-first analysis per HERMES.md §5.
