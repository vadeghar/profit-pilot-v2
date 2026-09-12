# Phase-Trace & Near-Miss Diagnostic Patterns (V3.0 -> V3.5)

## Trigger
Zero candidates at a VPA phase (Waterfall, SV, Absorption, Test, Breakout) despite verified endpoint data, correct state machine, and correct geometry.

## Required sequence (in order — do not skip)
1. Confirm endpoint / symbol / interval / date range (HTTP only per HERMES.md §6)
2. Confirm candle geometry (lower_wick = min(open,close)-low; close_location = (close-low)/range; zero-range guard; 5 candle types verified)
3. Confirm state-machine / indexing (SV bar excluded; 3-12 bar window enforced; no lookahead; signal after close)
4. Confirm registry / version (current version id + previous results preserved; never overwrite)
5. Run near-miss per-threshold (strict count + per-relaxation counts for RVOL / close_location / lower_wick / range_ATR)
6. Identify first failure phase (Waterfall -> SV -> Absorption -> Test -> Breakout) per-trade
7. Per-failure: record exact condition + observed value + required threshold + PASS/FAIL + reason
8. Preserve per-candidate OHLCV + metrics (symbol/date/OHLCV/RVOL/range_ATR/close_loc/lower_wick) in `V?-?_VARIANT_` / `V3_*` files
9. Classify post-phase behavior independently (declining / mixed / recovery / weakness) before any rule change
10. If a Concept B (alternative measurement) is proposed, derive thresholds from observed distributions; do not choose arbitrarily; run A/B on same 69 events; report overlap / B-only / false positives; compare through full sequence (not absorption alone); do not claim significance from small n
11. Decision: 1=Keep A, 2=B suitable for V3.6 experiment, 3=Neither reliable — with explicit justification; never auto-replace production

## Key outcomes (this session's proven path)
- V2.8: geometry fix (reversed lower_wick) eliminated impossible negative ratios
- V2.9: A/B/C/D variants on 69 SV events; D = most candidates (69) but 0 complete sequences
- V3.0: SV->Absorption state transition verified correct (10-point checklist)
- V3.1: Absorption->Test transition verified correct (11-point checklist + tests)
- V3.2: all 7 absorption predicates independently evaluated; failure = down-volume + down-range declining
- V3.3: behavior classification; dataset = 45% mixed / 33% recovery / 10% weakness / 0% declining
- V3.4: Concept B comparison (A=0, B~28 est); false-positive risk (recovery/weakness) identified
- V3.5: controlled A/B (A=0 / B_strict~3 / B_mod~8 / B_perm~18); complete sequences=0 all; decision=3 (neither reliable with this dataset)

## Pitfall (generalized)
Do NOT relax thresholds blindly when a phase yields 0 candidates — always audit geometry and run independent predicate evaluation first (V3.2 pattern). Do NOT claim statistical significance from a single period with n=69 SV events (V3.5 pattern). Do NOT replace production rules with Concept B until out-of-sample validation supports improvement (V3.5 decision rule).
