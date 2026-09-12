id: vwap_orb_equity
version: 1.1
name: VWAP + ORB Equity (Intraday)
instrument_scope: NSE cash equities (V1: 8 symbols via /equity endpoint)
status: backtesting
target_return_monthly: 4.5%
capital_required: 100000
entry_rules: |
  V1 fixed: ORB=15min, 5min bars, session VWAP slope=3 bars,
  RVOL_MODE=rolling_cross_session (default), RVOL>=1.5,
  candle_strength long>=0.70 / short<=0.30, entry=next_bar.
  State machine: PRE_SESSION → OR_BUILDING → OR_LOCKED →
  SIGNAL_CONFIRMED → POSITION_OPEN → POSITION_CLOSED → SESSION_COMPLETE.
  Hard reject: non-standard session, RVOL unavailable, wick-only breakout,
  same-bar SL/TP tie-break=SL-first, tick rounding=0.05.
exit_rules: |
  SL=OR level ±0.10*OR_RANGE; TP=2R; force_exit=15:15; max 1 trade/symbol/day.
  No overnight holds; no breakeven in V1.
code_path: strategies/vwap_orb_equity.py
changelog: |
  v1.1 (final spec): RVOL_MODE explicit (rolling_cross_session default),
  signal window pinned to bar-open time, same-bar SL/TP tie-break=stop_loss_first,
  tick-size rounding (₹0.05), point-in-time universe, non-standard session rejection.
assumptions: |
  - V1 restricted to 8 /equity symbols (RELIANCE, SBIN, etc.); point-in-time
    universe not fully enforceable until larger data layer available.
  - RVOL_MODE=rolling_cross_session used in V1; same_session_only reserved for E13.
  - E0-E3 baseline only (no retest, no trailing, no market filter).
  - Dedicated state-machine strategy (straddle pattern); output adapts to TradeResult.
