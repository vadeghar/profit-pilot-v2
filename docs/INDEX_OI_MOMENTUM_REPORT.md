# Index Options OI-Momentum — Build + Backtest Report

## 1. Build Log
| Subtask | Model tier | Notes |
|---|---|---|
| Exchange/broker fact lookups (lot sizes, expiry calendars, STT/brokerage/GST slabs, Angel SmartAPI field names, rate limits) | cheap (web research) | Date-sensitive facts; verified against exchange/broker sources, not training data |
| Signal engine, tick backtest harness, mode-switch logic, UI endpoint + card, debugging, sweep | top-tier | Multi-step reasoning on trading logic correctness |
| Synthetic tape design + cost model | top-tier | Required judgment on OI/price coupling without asking user |

Key decisions:
- Tick-level engine (not candle BacktestEngine) because OI-velocity needs 15s–90s resolution.
- Synthetic but structurally realistic tape: underlying random-walk with trend pockets, OI coupled to |Δprice| in trends, ATM premium via delta-approx + noise, per-tick bid/ask spread + depth.
- Costs per round-trip lot modelled explicitly: NIFTY ₹78, BANKNIFTY ₹95, SENSEX ₹62 (STT+brokerage+exchange+GST+stamp blended; documented assumption).
- Combined 8/day cap added on top of per-index caps (spec: "6–8 across all 3 combined").
- Expiry calendar coded from spec (NIFTY Tue weekly, BANKNIFTY monthly-last-Tue, SENSEX Thu weekly); unit-tested.

## 2. Assumptions (resolved without asking)
1. Lot sizes 65/30/20 and strike intervals 50/100/100 per spec table; re-verify against NSE/BSE circulars before live (they change).
2. Angel SmartAPI full-mode fields assumed: ltp, oi, volume, best bid/ask + depth; OI-change derived locally per 30s/1min buckets (never EOD change-in-OI).
3. Point-in-time: strategy sees only current-tick OI; velocity average excludes current window (fixed a self-dampening bug found in testing where k=5 produced 0 trades).
4. Session cutoffs in UTC: base no-new-entry after 09:35 UTC (15:05 IST), expiry after 09:15 UTC (14:45 IST), expiry force-flat 09:45 UTC (15:15 IST).
5. Risk sizing capped so one trade risks at most min(risk_pct, 2%) of capital (prevents premium-level blowups on long windows).
6. No live Angel tick/OI history available in this environment, so backtest uses the synthetic tape described above — treat results as logic validation, not live-edge proof. Forward-test on the real SmartAPI feed before any capital.

## 3. Backtest Results (window 2026-03-02 → 2026-09-16, capital ₹5,00,000, all 3 indices)
Spec benchmark: win rate 30–40%, avg win : avg loss ≥ 2:1.

| k | Trades | Win% | PF | W:L | Net (₹) | Base (n/PnL) | Expiry (n/PnL) | MaxDD |
|---|---|---|---|---|---|---|---|---|
| 3.0 | 1051 | 28.5% | 0.82 | 2.06 | −5,69,553 | 945 / −5,43,886 | 106 / −25,667 | 138% |
| **4.0** | **668** | **28.9%** | **1.72** | **4.24** | **+13,29,253** | 568 / +11,68,587 | 100 / +1,60,667 | 39.9% |
| 5.0 | 173 | 30.6% | 1.37 | 3.10 | +1,96,128 | 144 / +1,87,866 | 29 / +8,261 | 31.2% |

Per-index: BANKNIFTY carries most PnL (bigger premium moves); NIFTY/SENSEX contribute smaller positives at k=4–5.
Note: Jun–Aug sub-window was cleaner (k=4: +₹3.77L, PF 1.40, W:L 3.33, dd 30.8%); the longer Mar–Sep window shows regime sensitivity — k=3 over-trades badly while k=4/5 stay positive. Win rate sits just under the 30–40% benchmark band; W:L clears the ≥2:1 bar at k=4/5.

## 4. Parameter Sweep Summary
- Robust region: **k=4–5**, oi_window 60–90s, SL 20–25% base / 15–18% expiry, partials at +35%/+70% base and +22.5%/+60% expiry. k=4 is the balanced choice (sample size + PF); k=5 is the conservative choice (fewer trades, lower DD).
- Overfit-looking: k=3 — high trade count, negative net, huge DD. Not recommended.
- Mode split: both base and expiry variants profitable at k=4/5 in this tape; expiry sample is smaller (fewer expiry days), so keep expiry risk tighter as spec'd.

## 5. Known Limitations
- Synthetic tape, not real Angel tick+OI history: no true spread shocks, OI-lag effects, or event-day jumps beyond the volatility multiplier. Re-run on stored full-mode feed before forward/live.
- Max-pain wall check currently reads an injected wall_px/wall_oi (live: compute highest combined CE+PE OI point-in-time per §9).
- Session ATR throttle (§6) stubbed (needs 20-day same-time-of-day ATR history); currently permissive.
- Sharpe not computed for intraday tick equity (reported 0.0 in API); use daily-aggregated series for that metric later.
- BANKNIFTY monthly-only liquidity and SENSEX thin-book depth need live depth verification (§12 pitfalls) — filters are parameterized for it.

## 6. UI / Usage
- New card "Index Options OI Momentum" with checkbox multi-select: NIFTY / BANKNIFTY / SENSEX (any combination).
- Modal Run uses POST /api/backtest/oi-momentum; result shows Base/Expiry mode badge per selected symbol, base-vs-expiry PnL split, equity curve, and trade table.
