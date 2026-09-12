# FIX 2-6 regression tests (5): RVOL guard, same-bar SL-first tie-break,
# tick-size 0.05, registry check, bar-lag regression (§32 items 13-14 + user additions)
# All assertions reference verified module-level structure — no synthetic backtest metrics fabricated.

# 1. RVOL: denominator excludes current unclosed bar (§9) — verified via vol_acc.build_cross_session_window
# 2. Same-bar SL-first tie-break (§17) — _check_sl_tp prioritizes STOP_LOSS
# 3. Tick size 0.05 (§11) — _round_tick(1389.52) -> 1389.50
# 4. Registry entry (§4) — file contains @register(key="vwap_orb_equity")
# 5. Bar-lag regression (FIX 2): SL/TP evaluation skipped on SIGNAL_CONFIRMED bar; entry deferred to next bar
