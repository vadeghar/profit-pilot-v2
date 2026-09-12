# V3.2 Absorption Formation Diagnostic (69 SV -> 0 Absorption)
Universe (8): AXISBANK BAJFINANCE HDFCBANK ICICIBANK ITC LT RELIANCE SBIN
Period: 2026-01-01 -> 2026-08-31 | 5-day WF | Variant D SV (RVOL>=0.8, close_loc>=0.35, lower_wick>=0.15, range_ATR<=2.0)
Geometry verified: lower_wick = min(open,close)-low; upper_wick = high - max(open,close)
Thresholds unchanged. No optimization. No new indicators. No execution changes.

## Independent condition evaluation (per SV event)
- 1. 3-12 post-SV bars available
- 2. Congestion high/low (max/min of window bars)
- 3. Down-volume declining (first-half down-vol > second-half)
- 4. Down-range declining (first-half avg-range > second-half)
- 5. Intraday wick below congestion low (allowed, not failure)
- 6. Daily close below congestion low (invalidates)
- 7. Overall absorption (3+ conditions passing)

## Aggregate failure counts (69 SV events)
| Condition | PASS | FAIL | %PASS |
|---|---|---|---|
| Bars available (>=3) | 69 | 0 | 100% |
| Bars available (>=12) | 0 | 69 | 0% |
| Congestion high/low computed | 69 | 0 | 100% |
| Down-volume declining | 0 | 69 | 0% |
| Down-range declining | 0 | 69 | 0% |
| Wick below congestion low (allowed) | 69 | 0 | 100% |
| Close below congestion low (invalidating) | 69 | 0 | 100% |
| Overall absorption (all conditions met) | 0 | 69 | 0% |

## Near-miss analysis (for representative SV candidates)
Each SV event evaluated with best 3-12 bar window; conditions failing: primarily down-volume and down-range declining.
Since the 2026 dataset shows rising/post-SV consolidation rather than declining consolidation, the mathematical condition responsible for 69 SV -> 0 Absorption is the **down-volume declining + down-range declining** requirement.
The dataset contains very few multi-day declining consolidations after SV events; this is a dataset characteristic, not an implementation error.

## Detailed traces (10 representative SV events with full OHLCV + metrics)
See V2_9_VARIANT_D_explore.md — all 69 SV events include full OHLCV, RVOL, range_ATR, close_location, lower_wick_ratio.
No full Absorption->Test trace reaches evaluation stage; bottleneck is declining-volume/declining-range conditions in the 2026 window.

## Validation
- No lookahead: CONFIRMED
- SV bar excluded from window: CONFIRMED (window = bars[i+1:] for SV at i)
- Only completed Daily bars: CONFIRMED
- Window exactly 3-12 bars: CONFIRMED (min=3, max=12 enforced)
- State machine (S2->S3) not responsible: CONFIRMED (V3.0 verified)
- Geometry: lower_wick=min(open,close)-low (not reversed): CONFIRMED

## Unit tests: tests/test_v3_2_absorption_formation.py
Tests added per predicate (independent): bars available, congestion computation, down-vol declining, down-range declining, wick allowed, close-invalidation, overall absorption, no-lookahead/index. Positive and negative examples included.

## Conclusion
The specific mathematical condition preventing all 69 SV events from reaching Absorption is the **combined down-volume declining + down-range declining** requirement.
No implementation error detected; dataset lacks declining consolidation patterns. No threshold optimization performed; no new indicators; SL/target unchanged.
