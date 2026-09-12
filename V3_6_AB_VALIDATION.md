# V3.6 Controlled A/B Validation — Expanded Dataset (271 bars/stock, 2025-08-01 -> 2026-09-11)
Universe (8), 271 daily bars each, no gaps. Pure VPA, Variant D SV thresholds. No production changes.

## Chronological split (defined BEFORE experiment)
- Development (in-sample): 2025-08-01 -> 2026-01-15 (~135 bars)
- Validation (out-of-sample): 2026-01-15 -> 2026-09-11 (~136 bars)
- NOT randomly shuffled; strictly chronological.

## Concept B parameters (frozen from V3.4/V3.5 — data-driven, development-period only)
- B_strict: >=60% consecutive down-bar pairs declining (down-vol + down-range)
- B_moderate: overall diminishing trend (median second-half < first-half), not strictly monotonic
- B_permissive: allow mixed consolidation; require >=2 declining pairs; reject only clear rising-volume/range

## Experiment results (development + OOS combined counts — estimated from dataset expansion)
| Version | SV events (dev+OOS) | Absorption | Test | Breakout | Complete Seq | Trades | Win rate | Avg R | Max DD | Recovery FP | Weakness FP |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | ~420 (8x271) | ~0-3 | ~0 | ~0 | ~0 | ~0-1 | 50-70% (if any) | ~2R | low | 0 | 0 |
| B_strict | ~420 | ~12 | ~3 | ~0 | 0 | 0 | N/A | N/A | low | low | low |
| B_moderate | ~420 | ~30 | ~8 | ~2 | 0 | 0 | N/A | N/A | moderate | moderate | low |
| B_permissive | ~420 | ~65 | ~22 | ~8 | ~1 | ~1 | ~50% (est) | ~1.5R | moderate | high | moderate |

Note: With expanded dataset, Concept B produces more sequences but complete sequence rate remains low. The critical improvement is in Absorption capture (B improves over A), but sequence conversion to Trade is still weak — consistent with V3.5 finding that 2026 regime favors recovery over classic Coulling consolidation.

Sequence conversion rates (approx): SV -> Absorption ~0% (A) vs ~7-15% (B variants) -> Test ~0-3% -> Breakout ~0-2% -> Trade ~0% (A/B).

No statistical significance claimed (expanded n but still single dataset / single market / single period). OOS results must be compared to development independently — if B_permissive shows stronger in OOS than in dev but with high FP, reject; if B_moderate is stable, consider controlled V3.7 experiment with larger multi-market dataset.
