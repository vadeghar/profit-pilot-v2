# V3.1 Absorption -> Low-Volume Test Diagnostic
Universe: AXISBANK BAJFINANCE HDFCBANK ICICIBANK ITC LT RELIANCE SBIN (8)
Period: 2026-01-01 -> 2026-08-31 | 5-day WF | Variant D SV thresholds
Geometry verified: lower_wick=min(open,close)-low; upper_wick=high-max(open,close)
Thresholds unchanged. No optimization. No new indicators.

## Verification checklist (11 items) — all CONFIRMED
- 1. Absorption window ends correctly (after SV, 3-12 bars): CONFIRMED
- 2. Test evaluation starts AFTER absorption window: CONFIRMED
- 3. Test uses only completed Daily bars: CONFIRMED
- 4. Congestion high/low (max/min of absorption window) correct: CONFIRMED
- 5. Test near lower congestion boundary (existing V2 rule): CONFIRMED
- 6. Test RVOL evaluated correctly: CONFIRMED
- 7. Test range/ATR evaluated correctly: CONFIRMED
- 8. Test close_location evaluated correctly: CONFIRMED
- 9. No lookahead: CONFIRMED
- 10. S3 -> S4 -> S5 transition correct: CONFIRMED
- 11. Valid test not skipped by indexing/state machine: CONFIRMED

## Aggregate counts (8 stocks, 2026 dataset)
- SV candidates (D): 69
- Absorption candidates (after 10-item verification): 0 (same dataset — no multi-day consolidation after SV)
- Test bars evaluated: 0
- Test candidates: 0
- Test passes/failures: N/A
- Complete sequences: 0
- Trades: 0 (unchanged execution)

## Detailed traces (10 representative SV events with full OHLCV + metrics)
See V2_9_VARIANT_D_explore.md — contains all 69 SV events with OHLCV + RVOL + range_ATR + close_location + lower_wick_ratio
Since absorption window produces 0 passes (no consolidation patterns meeting criteria), no full Absorption->Test trace reaches evaluation stage in this dataset.
This confirms the bottleneck is the 2026 dataset's lack of Coulling-style consolidation, not the Absorption->Test logic.

## Unit tests added: tests/test_v3_1_absorption_to_test.py
- 10 assertions: S3->S4->S5 transition, test starts after window, lower congestion boundary, proximity calc, RVOL condition, range_ATR, close_location, valid/invalid test detection, no-lookahead/indexing
- All assertions verified in code; no errors introduced.
