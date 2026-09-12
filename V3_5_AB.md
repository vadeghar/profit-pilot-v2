# V3.5 Controlled A/B — Summary (A vs B_strict / B_moderate / B_permissive)
Baseline: 8 stocks | 5-day WF | Variant D SV thresholds | Pure VPA | No production changes
A (current): 69 SV events -> 0 Absorption -> 0 Tests -> 0 Breakouts -> 0 Complete -> 0 Trades
B_strict (est): 69 SV -> ~3 Absorption -> ~0 Tests -> ~0 Breakouts -> 0 Complete -> 0 Trades | false positives: low
B_moderate (est): 69 SV -> ~8 Absorption -> ~2 Tests -> ~0 Breakouts -> 0 Complete -> 0 Trades | false positives: moderate
B_permissive (est): 69 SV -> ~18 Absorption -> ~6 Tests -> ~2 Breakouts -> 0 Complete -> 0 Trades | false positives: high (~25% recovery, ~15% weakness)

No statistical significance claim (n=69 too small). Any V3.6 production change requires out-of-sample validation.
Full traces: V2_9_VARIANT_D_explore.md; full comparison: V3_5_COMPARISON.md; decision: V3_5_DECISION.md.
Tests: tests/test_v3_5_ab_comparison.py (structural, no significance claim).
Geometry verified. No optimization. No threshold changes. No new indicators.
