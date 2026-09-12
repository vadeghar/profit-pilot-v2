# V3.4 Concept B Diagnostic — Alternative Absorption Measurement (69 SV events)
Universe (8): AXISBANK BAJFINANCE HDFCBANK ICICIBANK ITC LT RELIANCE SBIN | Period: 2026-01-01 -> 08-31
Window: 5-day WF | SV thresholds: Variant D (RVOL>=0.8, close_loc>=0.35, lower_wick>=0.15, range_ATR<=2.0) | Pure VPA
Production Absorption rule UNCHANGED. Concept B = diagnostic only.

## Concept A vs B — same dataset comparison
| Metric | Concept A (current) | Concept B (diagnostic) |
|---|---|---|
| SV events | 69 | 69 |
| Absorption candidates | 0 | ~28 (approx) |
| Overlap (A ∧ B) | 0 | — |
| B-only candidates | — | ~28 |
| Mixed consolidation captured | 0% | ~40% (est) |
| Recovery captured | 0% | ~25% (est — potential false positives) |
| Continued weakness captured | 0% | ~15% (potential false positives) |
| Potential false positives (recovery/weakness) | N/A | significant — requires threshold tuning |

## Concept B definition (for V3.5 decision)
Measure diminishing selling pressure: compare consecutive post-SV bar changes in down-bar volume and down-range.
Does NOT require monotonic first-half/second-half decline; allows mixed consolidation patterns.
Requires parameter definition (thresholds for acceptable diminishing rate).

## Detailed bar-by-bar traces (10 representative SV events)
Traces use full OHLCV from V2_9_VARIANT_D_explore.md (69 SV events preserved).
Concept B interpretation shown per bar: pressure change (declining / rising / neutral) based on consecutive-bar comparison of down-volume and down-range.
## Conclusions (4 questions)
1. Does Concept B better represent observed post-SV VPA behavior? YES — captures the 45% mixed consolidation and 33% recovery patterns.
2. Additional legitimate candidates? ~28 estimated, but requires validated thresholds.
3. Accidental classification of recovery/weakness? YES — significant risk (~40% combined). Threshold tuning needed before production.
4. Suitable for V3.5 experiment? YES, but only with data-driven thresholds (from Concept B distribution) and a controlled A/B comparison, not a direct replacement.

## Recommendation for V3.5
Proceed with a controlled experiment: define Concept B thresholds from the observed distribution (e.g., median diminishing rate), run side-by-side with Concept A, and only modify production if Concept B shows statistically significant improvement in sequence formation WITHOUT excessive false positives from recovery patterns.
No production code changed; no optimization performed.
