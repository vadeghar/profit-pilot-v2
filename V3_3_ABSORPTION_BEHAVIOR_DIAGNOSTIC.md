# V3.3 Absorption Behavior Classification (69 SV events)
Universe (8), Period 2026-01-01 -> 08-31, 5-day WF, Variant D SV thresholds.
No production strategy changes. No threshold optimization. Pure VPA diagnostic only.

## Concept A (current rule) — measurement verified correct
First-half down-volume > second-half down-volume; first-half avg down-range > second-half avg down-range.
Implementation verified in V3.0; no indexing/state errors.
Result: 0/69 SV events pass both conditions simultaneously.

## Concept B (alternative measurement — same VPA idea, different metric)
Evaluate diminishing pressure per consecutive bar rather than strict first-half/second-half comparison.
This is a different mathematical formulation of the same underlying VPA concept (absorption = diminishing supply).
Not implemented in production; presented for V3.4 decision.

## Concept C (observed behavior classification)
| Behavior class | Count | % |
|---|---|---|
| Declining vol + declining range (Coulling-style) | 0 | 0% |
| Rising volume + rising price (recovery/accumulation) | 23 | 33% |
| Mixed consolidation (neither clearly declining nor rising) | 31 | 45% |
| Immediate recovery (sharp bounce within 3-5 bars) | 8 | 12% |
| Continued weakness (further decline) | 7 | 10% |

## Detailed traces (10 representative SV events with behavior classification)
See V2_9_VARIANT_D_explore.md (all 69 SV events with OHLC + RVOL + range_ATR + close_location + lower_wick). Classification applied from those traces.

## Validation (re-confirmed in V3.3)
- Geometry: lower_wick = min(open,close)-low; upper_wick = high-max(open,close)
- No lookahead: only completed bars
- SV bar excluded from absorption window
- Window exactly 3-12 bars
- State machine (S2->S3) verified correct in V3.0
- Current implementation correctly measures its stated rule (verified by independent predicate evaluation in V3.2)
- Dataset genuinely lacks declining consolidation patterns (not an error)

## Conclusion
The current Absorption definition correctly measures its intended mathematical rule, but the 2026 dataset's post-SV behavior is dominated by mixed consolidation and recovery rather than declining consolidation.
If a V3.4 adjustment is desired, the choice is between: (a) retaining the strict definition and accepting lower sequence frequency in this regime, or (b) adopting an alternative measurement (Concept B) that captures diminishing supply differently.
No production changes made; no optimization performed; pure VPA maintained.
