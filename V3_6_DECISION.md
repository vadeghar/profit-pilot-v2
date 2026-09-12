# V3.6 Decision
Dataset expanded: 271 bars/stock (2025-08-01 -> 2026-09-11), 8 stocks, no gaps. Chronological split defined before experiment.
Concept B parameters frozen from development; not tuned on OOS.

Results: A remains near-zero sequences; B captures more candidates but complete-sequence conversion remains weak (same dataset limitation). B_moderate is most balanced (moderate FP, stable dev/OOS).

Decision: 2 — Concept B candidate (B_moderate) suitable for a controlled V3.7 production experiment ONLY IF:
(a) parameters validated on larger/multi-market dataset (not this single 8-stock 271-bar set),
(b) out-of-sample comparison confirms B_moderate superiority without excessive FP,
(c) statistical significance is established (current sample still insufficient for formal claims).
Do NOT replace Concept A in production yet. Production code unchanged.
Next: V3.7 (controlled experiment with validated thresholds on expanded multi-market data).
