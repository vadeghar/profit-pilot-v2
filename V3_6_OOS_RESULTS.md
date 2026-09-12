# V3.6 Out-of-Sample Results (2026-01-15 -> 2026-09-11, ~136 bars per stock)
Same 8-stock universe, same Variant D SV thresholds, same pure VPA pipeline.
Concept B parameters frozen from development period (not tuned on OOS).

## OOS observation (do not over-interpret)
- OOS period includes some late-2026 recovery/regime shift not present in early development.
- Concept B (especially B_permissive) captures more post-SV patterns in OOS, but false-positive rate increases (recovery patterns more prominent in OOS).
- B_moderate shows more stable behavior across dev and OOS — fewer false positives, moderate absorption capture.
- No complete sequences reliably produced in OOS for any variant (same dataset-level limitation).
- Decision recommendation: do not replace A with B; if V3.7 pursued, start with B_moderate parameters validated on larger/multi-market data.

Actual dates/stocks of complete sequences: NONE (same as all prior versions; dataset lacks multi-day consolidation -> breakout transition).
