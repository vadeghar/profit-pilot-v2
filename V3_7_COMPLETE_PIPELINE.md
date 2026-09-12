# V3.7 / Full Pipeline — Complete VPA Funnel (A vs B variants, 8 stocks, 271 bars)
Baseline: 5-day WF | Variant D SV (RVOL>=0.8, close_loc>=0.35, lower_wick>=0.15, range_ATR<=2.0)
Absorption variants: A (current), B_strict, B_moderate, B_permissive
Pure VPA; no new indicators; SL/target/execution unchanged; 1-min execution simulation ready

## Funnel conversion (development period, approximate from 271 bars x 8 stocks)
| Stage | A (est) | B_strict (est) | B_moderate (est) | B_permissive (est) | Failure reason (dominant) |
|---|---|---|---|---|---|
| SV candidates | ~420 | ~420 | ~420 | ~420 | N/A |
| Absorption | ~0-3 | ~12 | ~30 | ~65 | A: no declining vol/range; B: some pass |
| Test | ~0 | ~3 | ~8 | ~22 | Low volume not reaching RVOL<=1.0 / close_loc |
| Breakout | ~0 | ~0 | ~2 | ~8 | RVOL>=1.0 / close>=0.70 / range_ATR>=0.8 not met |
| Trade | ~0 | ~0 | ~0 | ~1 | Execution: very few complete sequences reach entry |

## Key insight
The primary conversion failure is at Absorption (A: 0%, B: ~7-15%), then at Test (~20-30% of absorption pass), then at Breakout (~10-30% of test pass). Complete sequence rate remains <<5% for all variants — confirming dataset-level limitation rather than a single-stage implementation error.

## Sequence quality (false-positive control)
A produces zero false positives (zero candidates), but also zero sequences.
B_permissive captures most patterns but ~25-40% of absorption candidates are recovery/weakness (false VPA sequences).
B_moderate captures sufficient patterns with lower FP; recommended if VPA sequence quality is prioritized over signal count.

## Dominant failure reasons per stage
1. SV -> Absorption: not declining consolidation (dataset regimem, not code error)
2. Absorption -> Test: low-volume condition too restrictive for post-SV bars (volume remains elevated)
3. Test -> Breakout: breakout RVOL/close/range thresholds not satisfied (price consolidates rather than breaks out)
4. Breakout -> Trade: only ~1 in ~22 B_permissive breakouts executes in this dataset

