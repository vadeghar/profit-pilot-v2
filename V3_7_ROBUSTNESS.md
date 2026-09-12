# V3.7 Robustness, False-Positive, and Parameter-Sensitivity Analysis
Based on existing diagnostics (V2.0-V3.6); no new fabricated data.

## False-positive categories identified from V3.2-V3.3-V3.4
- Recovery / accumulation after SV (price rises, volume picks up): B_permissive captures ~25% of these; B_moderate ~10%; B_strict ~2%.
- Continued weakness (further decline after SV): B_permissive ~15%; B_moderate ~5%; B_strict ~1%.
- Mixed consolidation (neutral): B_moderate ~30%; B_strict ~5%.
- Genuine Coulling-style declining consolidation with breakout: very rare in this dataset (~0-2%).

## Robustness checks performed (or would be required for production)
- Different stocks: 8-stock universe tested together; single-stock dependency not dominant (all show similar pattern — dataset-level, not stock-level).
- Different periods: 2025-08-01 -> 2026-09-11 covers multiple regimes (recovery, consolidation, late-2026 shift); consistency across sub-periods not fully tested (would need split observation).
- Bullish / bearish / sideways: dataset spans mixed regimes; no single regime drives all results.
- Parameter sensitivity: B_strict -> B_moderate -> B_permissive smoothly increases candidates (~3 -> ~30 -> ~65) — not a sharp threshold jump; suggests gradual relaxation, not arbitrary override.
- If parameter change of +/-10% destroys performance -> flag; here the change is gradual, not destructive, but also does not create reliable sequences.

## Conclusion from robustness
No formulation achieves reliable, reproducible complete sequences across the 271-bar 8-stock dataset. Concept B improves absorption capture but does not solve the core dataset-level limitation (low consolidation-to-breakout conversion). Any V3.7 production experiment must include: (a) larger/multi-market dataset, (b) out-of-sample confirmation, (c) statistical test (not visual inspection), (d) controlled comparison with A, (e) full execution with SL/2R/time-stop, (f) failure-first review of every losing/failed trade per HERMES.md §5.
No claim of statistical significance will be made from this dataset alone.
