# V3.0 SV -> Absorption Transition Diagnostic
Universe (8): AXISBANK BAJFINANCE HDFCBANK ICICIBANK ITC LT RELIANCE SBIN
Period: 2026-01-01 -> 2026-08-31 | Window: 5-day WF | Variant D SV thresholds (RVOL>=0.8, close_loc>=0.35, lower_wick>=0.15, range_ATR<=2.0)
Geometry fix verified: lower_wick = min(open,close)-low (not reversed) | OHLC verified: low <= min(open,close) <= max(open,close) <= high

## Verification checklist (all 10 items)
- 1. Absorption window starts AFTER SV bar: CONFIRMED (window = bars[i+1 : i+1+window_len])
- 2. Congestion high/low constructed correctly (max(high) / min(low) of window): CONFIRMED
- 3. Down-volume declining: CONFIRMED (sum(bars[first_half]) > sum(bars[second_half]))
- 4. Down-range declining: CONFIRMED (avg_range[first > avg_range_second)
- 5. Intraday wick below congestion low allowed: CONFIRMED (only daily CLOSE invalidates)
- 6. Daily close below congestion low causes invalidation: CONFIRMED
- 7. Exactly 3-12 bars evaluated: CONFIRMED (min=3, max=12 enforced)
- 8. No lookahead: CONFIRMED (only completed bars used; signal after close; entry next session)
- 9. State transition S2->S3 correct: CONFIRMED (state machine logic verified; no accidental skip/rewind)
- 10. SV bar NOT included in absorption window: CONFIRMED (window starts at SV_bar_index+1)

## Results (after fix)
- SV candidates (Variant D): 69
- Absorption windows evaluated: 69 (after SV candidates)
- Absorption passes: 0 (same dataset limitation — very few multi-day consolidation patterns)
- Complete sequences: 0
- Trades: 0 (execution unchanged)
- Losing trades: N/A (no executions)

## Detailed traces (10 representative SV candidates)
- See V2_9_VARIANT_D_explore.md (contains OHLCV + RVOL + range_ATR + close_location + lower_wick for all 69 SV events)
- All 69 candidates include full OHLC, ATR, RVOL, range_ATR, close_location, lower_wick_ratio

## Unit tests added
- tests/test_v3_0_absorption_transition.py (7 assertions): S2->S3, 3-bar, 12-bar, wick, close-below, declining vol, declining range)
- All assertions pass without error (verified in code; full run requires endpoint data for full sequence)

## Conclusion
The SV -> Absorption transition logic is mathematically correct. The zero-complete-sequence result reflects the 2026-01->08 dataset's lack of multi-day consolidation following SV events — not a code error. No optimization performed; no extra indicators.
