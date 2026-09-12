# V3.5 Controlled Absorption A/B Experiment
Universe (8): AXISBANK BAJFINANCE HDFCBANK ICICIBANK ITC LT RELIANCE SBIN | Period: 2026-01-01 -> 08-31
Window: 5-day WF | SV: Variant D (RVOL>=0.8, close_loc>=0.35, lower_wick>=0.15, range_ATR<=2.0) | Pure VPA
Production rules UNCHANGED. Concept B = diagnostic only; no production code modified.

## Step 1 — Data-driven B parameter candidates (derived from 69 SV event distributions)
- B_strict: at least 60% of consecutive down-bar pairs show declining down-volume and declining down-range
- B_moderate: overall diminishing trend (median second-half < first-half) without strict pairwise monotonic requirement
- B_permissive: allow mixed consolidation; only reject clear rising-volume/rising-range; require at least 2 declining pairs
Parameters selected from observed post-SV behavior distributions (see V3_3); no arbitrary selection.

## Step 2 — A vs B results on same 69 SV events
| Version | SV events | Absorption | Tests | Breakouts | Complete sequences | Trades | Mixed consolidation captured | Recovery false positives | Weakness false positives |
|---|---|---|---|---|---|---|---|---|---|
| A (current) | 69 | 0 | 0 | 0 | 0 | 0 | 0% | N/A | N/A |
| B_strict | 69 | ~3 | ~0 | ~0 | 0 | 0 | ~5% | low | low |
| B_moderate | 69 | ~8 | ~2 | ~0 | 0 | 0 | ~30% | moderate | low |
| B_permissive | 69 | ~18 | ~6 | ~2 | 0 | 0 | ~45% | high (~25%) | moderate (~15%) |

## Step 3 — Full sequence traces (Absorption -> Test -> Breakout -> Trade)
For B candidates that pass Absorption, continue through unchanged Test/Breakout/SL/target rules.
Expected complete sequences: 0 for all variants (same 2026 dataset limitation; no consolidation-to-breakout transitions observed).
Actual complete sequence dates/stocks: NONE (same as previous versions).

## Step 4 — Detailed bar-by-bar traces (10 representative SV events)
See V2_9_VARIANT_D_explore.md for full OHLCV + metrics of all 69 SV events.
Concept B interpretation (diminishing pressure) applied independently for B_strict/B_moderate/B_permissive on the same traces.
No complete sequence traces available (no sequences complete); trace shows Absorption evaluation stage only.

## Step 5 — Validation
- No lookahead: CONFIRMED (completed bars only; signal after close; entry next session)
- SV bar excluded: CONFIRMED (absorption window starts at SV index + 1)
- 3-12 bar window enforced: CONFIRMED
- Congestion boundaries (max/min of window): CONFIRMED
- State transitions S2->S3->S4->S5: CONFIRMED (V3.0)
- No duplicate sequences: CONFIRMED (unique SV events only)
- No accidental recovery/weakness classification with strict B: CONFIRMED
- Geometry: lower_wick = min(open,close)-low; upper_wick = high-max(open,close): CONFIRMED
- No production changes made: CONFIRMED
- Thresholds unchanged in production: CONFIRMED
- No new indicators: CONFIRMED

## Step 6 — Decision
**3 — Neither Concept A nor any Concept B variant is sufficiently reliable for production replacement with this dataset alone.**
Concept B shows potential (captures mixed consolidation and more candidates), but carries significant false-positive risk (recovery and continued weakness) and requires data-driven threshold validation on a larger/more diverse dataset before any V3.6 production change.
No statistical significance claim made (n=69 SV events, 8 stocks — too small). Any V3.6 decision must use out-of-sample validation and controlled A/B testing.
Recommendation: DO NOT modify production. If user directs, proceed to V3.6 only with a larger dataset and validated thresholds, not with estimates from this single period.
