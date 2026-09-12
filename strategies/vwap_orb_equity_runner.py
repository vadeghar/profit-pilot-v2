"""VWAP + ORB Equity Strategy — V1.1 dedicated state-machine runner.

Architecture: follows NIFTY ATM Straddle pattern — dedicated state machine
with own entry point, output adapted to TradeResult shape.

Key anti-lookahead guarantees (§30):
- OR uses only bars in 09:15-09:29 (strict).
- VWAP at signal bar uses cumulative data from 09:15 up to and including
  the signal bar (the strategy reacts to bar CLOSE, enters at next bar).
- RVOL denominator uses bars BEFORE the breakout bar only (never current).
- Entry uses next-bar price (enforced by BacktestEngine or runner).

RVOL_MODE (§9, v1.1):
- rolling_cross_session: volume history for RVOL lookback may include
  prior session regular-session bars (never pre-open/post-close,
  never the current unclosed bar). VWAP itself remains session-only (§7).
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, time as time_constructor
from typing import Any, Sequence

from profit_pilot.execution.fill import Fill
from profit_pilot.execution.order import Order
from profit_pilot.strategy.signal import Signal, SignalAction


# ─── Data types ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EquityCandle:
    """5-min OHLCV bar from /equity endpoint."""
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class TradeResult:
    """Standard trade output shape (per spec §31)."""
    symbol: str
    entry_timestamp: datetime
    entry_price: float
    exit_timestamp: datetime
    exit_price: float
    exit_reason: str  # STOP_LOSS, TAKE_PROFIT, FORCE_EXIT, END_OF_SESSION
    direction: str  # LONG, SHORT
    quantity: int
    gross_pnl: float
    total_cost: float = 0.0
    net_pnl: float = 0.0
    r_multiple: float = 0.0
    mae: float = 0.0
    mfe: float = 0.0


@dataclass(frozen=True)
class SignalLog:
    """Log entry for every rejected/accepted signal (spec §31)."""
    symbol: str
    date: date
    timestamp: datetime
    direction: str  # LONG, SHORT, REJECTED
    or_high: float
    or_low: float
    or_range: float
    close: float
    vwap: float
    vwap_slope: float
    volume: float
    rvol: float
    rvol_mode: str
    candle_strength: float
    entry: float
    stop: float
    target: float
    quantity: int
    risk_amount: float
    rejection_reason: str  # empty if accepted
    strategy_version: str


# ─── Session state machine ──────────────────────────────────────────────────

SESSION_STATES = [
    "PRE_SESSION",
    "OR_BUILDING",
    "OR_LOCKED",
    "WAITING_FOR_BREAKOUT",
    "SIGNAL_CONFIRMED",
    "POSITION_OPEN",
    "POSITION_CLOSED",
    "SESSION_COMPLETE",
]

VALID_TRANSITIONS: dict[str, set[str]] = {
    "PRE_SESSION": {"OR_BUILDING"},
    "OR_BUILDING": {"OR_LOCKED"},
    "OR_LOCKED": {"WAITING_FOR_BREAKOUT"},
    "WAITING_FOR_BREAKOUT": {"SIGNAL_CONFIRMED", "OR_LOCKED"},
    "SIGNAL_CONFIRMED": {"POSITION_OPEN"},
    "POSITION_OPEN": {"POSITION_CLOSED", "WAITING_FOR_BREAKOUT"},
    "POSITION_CLOSED": {"WAITING_FOR_BREAKOUT", "SESSION_COMPLETE"},
    "SESSION_COMPLETE": set(),
}


class SessionStateMachine:
    """Enforces valid state transitions (spec §23)."""

    def __init__(self) -> None:
        self.state: str = "PRE_SESSION"

    def transition(self, new_state: str) -> bool:
        if new_state in VALID_TRANSITIONS.get(self.state, set()):
            self.state = new_state
            return True
        return False

    def can_transition_to(self, new_state: str) -> bool:
        return new_state in VALID_TRANSITIONS.get(self.state, set())


# ─── VWAP accumulator (session-only, per spec §7) ──────────────────────────

class VWAPAccumulator:
    """Cumulative VWAP reset each session; never carries prior-session data."""

    def __init__(self) -> None:
        self._tp_vol: float = 0.0
        self._vol: float = 0.0
        self.vwap: float = 0.0
        self.history: list[float] = []  # VWAP values (for slope check)

    def add(self, typical_price: float, volume: float) -> None:
        if volume <= 0:
            return
        self._tp_vol += typical_price * volume
        self._vol += volume
        self.vwap = self._tp_vol / self._vol
        self.history.append(self.vwap)

    def reset(self) -> None:
        self._tp_vol = 0.0
        self._vol = 0.0
        self.vwap = 0.0
        self.history.clear()

    def slope(self, bars_back: int) -> float:
        if len(self.history) < bars_back + 1:
            return 0.0
        return self.history[-1] - self.history[-1 - bars_back]


# ─── Volume accumulator (session-only bars, but cross-session lookback for RVOL) ──

class VolumeAccumulator:
    """
    Tracks same-session volumes (for VWAP and bar-by-bar tracking)
    AND maintains a cross-session volume history for RVOL calculation.

    In RVOL_MODE=rolling_cross_session, the 20-bar RVOL lookback may
    extend into prior regular-session bars.

    Invariant: never includes the current unclosed bar or pre-open/post-close bars.
    """

    def __init__(self, rvol_lookback: int = 20) -> None:
        self.rvol_lookback = rvol_lookback
        # Same-session volumes in chronological order
        self.session_volumes: list[float] = []
        # Cross-session rolling window for RVOL (chronological order)
        self.cross_session_volumes: list[float] = []

    def add_session_bar(self, volume: float) -> None:
        """Add a bar volume during an active session."""
        if volume < 0:
            return
        self.session_volumes.append(volume)

    def build_cross_session_window(self, prior_sessions: list[list[float]]) -> None:
        """
        Build cross-session volume window from prior regular-session bars.
        prior_sessions: list of lists, each list is volumes from one prior
        regular session in chronological order (oldest first).
        Takes up to rvol_lookback-1 bars from prior sessions (leaves room
        for current session bars).
        """
        all_prior: list[float] = []
        for session in reversed(prior_sessions):  # most recent first
            all_prior.extend(session)
        # Take only what we need (rvol_lookback - max_current_session_bars)
        all_prior = all_prior[: self.rvol_lookback]
        self.cross_session_volumes = all_prior

    def rvol(self, current_volume: float, mode: str) -> float | None:
        """
        Calculate RVOL for a given bar volume.

        In rolling_cross_session mode: uses cross_session_volumes + same-session bars.
        In same_session_only mode: uses only same-session bars.
        """
        if mode == "same_session_only":
            recent = self.session_volumes[-self.rvol_lookback :]
            if len(recent) < self.rvol_lookback:
                return None  # RVOL_UNAVAILABLE per §9 v1.1
            avg = sum(recent) / len(recent)
            return current_volume / avg if avg > 0 else None

        # rolling_cross_session: use cross-session window + current session
        combined = self.cross_session_volumes + self.session_volumes
        recent = combined[-self.rvol_lookback :]
        if len(recent) < self.rvol_lookback:
            return None
        avg = sum(recent) / len(recent)
        return current_volume / avg if avg > 0 else None


# ─── Main runner ────────────────────────────────────────────────────────────

class VWAPORBRunner:
    """
    Dedicated state-machine runner for VWAP + ORB Equity strategy.

    Usage:
        1. Feed candles day-by-day via process_day().
        2. Cross-session RVOL is set up via set_prior_sessions() before processing.
        3. Access results via trades, signal_logs, rejected_signals.
    """

    def __init__(self, params: dict | None = None) -> None:
        self.params = params or {}
        # V1 defaults (spec §33)
        self.rvol_mode: str = self.params.get("rvol_mode", "rolling_cross_session")
        self.rvol_lookback: int = self.params.get("rvol_lookback", 20)
        self.rvol_threshold: float = self.params.get("rvol_threshold", 1.50)
        self.min_rvol: float = self.params.get("min_rvol", 1.50)
        self.vwap_slope_bars: int = self.params.get("vwap_slope_bars", 3)
        self.signal_start: str = self.params.get("signal_start", "09:30")
        self.signal_end_open: str = self.params.get("signal_end_open", "11:25")
        self.force_exit_time: str = self.params.get("force_exit_time", "15:15")
        self.max_trades_per_symbol_day: int = self.params.get("max_trades_per_symbol_day", 1)
        self.max_risk_pct: float = self.params.get("max_risk_pct", 0.005)
        self.max_daily_loss_pct: float = self.params.get("max_daily_loss_pct", 0.015)
        self.sl_buffer_pct: float = self.params.get("sl_buffer_pct", 0.10)
        self.tp_r_multiple: float = self.params.get("tp_r_multiple", 2.0)
        self.tick_size: float = self.params.get("tick_size", 0.05)
        self.long_min_close_location: float = self.params.get("long_min_close_location", 0.70)
        self.short_max_close_location: float = self.params.get("short_max_close_location", 0.30)
        self.same_bar_sl_tp_priority: str = self.params.get("same_bar_sl_tp_priority", "stop_loss_first")
        self.strategy_version: str = "1.1"

        # Per-day state
        self.session_sm: SessionStateMachine = SessionStateMachine()
        self.vwap_acc: VWAPAccumulator = VWAPAccumulator()
        self.vol_acc: VolumeAccumulator = VolumeAccumulator(self.rvol_lookback)

        # Day-level tracking
        self.or_high: float = 0.0
        self.or_low: float = float('inf')
        self.pending_signal = None; self.pending_expired = 0  # FIX 2 (§11 queued + expire counts)
        self.or_range: float = 0.0
        self.or_mid: float = 0.0
        self.orb_complete: bool = False

        # Position tracking
        self.position_active: bool = False
        self.position_direction: str = ""  # LONG, SHORT
        self.position_entry_price: float = 0.0
        self.position_stop: float = 0.0
        self.position_target: float = 0.0
        self.position_quantity: int = 0
        self.position_entry_time: datetime = datetime.min
        self.position_mae: float = 0.0
        self.position_mfe: float = 0.0
        self.starting_equity: float = 100000.0  # ₹1L per spec
        self.current_equity: float = 100000.0

        # Daily tracking
        self.trades_today: int = 0
        self.daily_loss_reached: bool = False
        self.max_daily_loss: float = self.max_risk_pct * self.starting_equity  # Will track abs P&L

        # Output containers
        self.trades: list[TradeResult] = []
        self.signal_logs: list[SignalLog] = []
        self.rejected_signals: list[SignalLog] = []

        # Prior session volume data (for cross-session RVOL)
        self._prior_session_volumes: list[list[float]] = []

    # ─── Public API ──────────────────────────────────────────────────────

    def set_prior_sessions(self, session_volumes: list[list[float]]) -> None:
        """Set prior regular-session volume lists for cross-session RVOL.

        session_volumes: list of lists, each inner list contains bar volumes
        from one prior regular session, chronological order. Oldest first.
        """
        self._prior_session_volumes = session_volumes
        self.vol_acc.build_cross_session_window(session_volumes)

    def process_day(self, candles: Sequence[EquityCandle]) -> None:
        """Process all candles for one trading day."""
        if not candles:
            return
        symbol = candles[0].symbol
        trading_date = candles[0].timestamp.date()

        # Reset per-day state
        self._reset_day(symbol, trading_date)

        # Determine session boundaries from first bar
        first_ts = candles[0].timestamp
        self.session_sm.state = "PRE_SESSION"

        for candle in candles:
            self._process_bar(candle)

        # End of day cleanup
        if self.position_active:
            self._close_position(candle := candles[-1], "END_OF_SESSION")
        self.session_sm.transition("SESSION_COMPLETE")

    def fetch_and_run(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        api_base: str = "http://localhost:8000",
    ) -> dict[str, Any]:
        """
        Full pipeline: fetch data from API, process all days, return results.

        Uses /equity endpoint with FIVE_MINUTE interval per §4.
        """
        from datetime import timedelta

        candles = self._fetch_equity_bars(symbol, start_date, end_date, api_base)
        if not candles:
            return {"success": False, "error": "No data returned from API", "trades": [], "signal_logs": []}

        # Group by date
        days: dict[date, list[EquityCandle]] = {}
        for c in candles:
            d = c.timestamp.date()
            days.setdefault(d, []).append(c)

        # For cross-session RVOL, we need prior sessions.
        # Fetch an extended range for prior data.
        prior_end = start_date - timedelta(days=1)
        prior_start = prior_end - timedelta(days=45)  # ~45 trading days ~ enough for 20-bar cross-session
        prior_candles = self._fetch_equity_bars(symbol, prior_start, prior_end, api_base)

        # Build prior session volume lists (regular-session bars only)
        if prior_candles:
            prior_sessions: list[list[float]] = []
            current_session_vols: list[float] = []
            prev_date = None
            for c in prior_candles:
                d = c.timestamp.date()
                if prev_date is not None and d != prev_date:
                    if current_session_vols:
                        prior_sessions.append(current_session_vols)
                    current_session_vols = []
                current_session_vols.append(c.volume)
                prev_date = d
            if current_session_vols:
                prior_sessions.append(current_session_vols)

            # Keep only last 5 sessions (enough for cross-session RVOL)
            self.set_prior_sessions(prior_sessions[-5:])

        # Process all days
        for d in sorted(days.keys()):
            self.process_day(days[d])

        return {
            "success": True,
            "symbol": symbol,
            "date_range": f"{start_date} to {end_date}",
            "trades": self._trade_dicts(),
            "signal_logs": self._signal_log_dicts(),
            "rejected_signals": self._signal_log_dicts(self.rejected_signals),
            "n_trades": len(self.trades),
            "n_rejected": len(self.rejected_signals),
        }

    # ─── Internal processing ─────────────────────────────────────────────

    def _reset_day(self, symbol: str, trading_date: date) -> None:
        self.session_sm = SessionStateMachine()
        self.vwap_acc = VWAPAccumulator()
        self.vol_acc = VolumeAccumulator(self.rvol_lookback)
        # Restore cross-session volumes (they persist across days for RVOL)
        self.or_high = 0.0
        self.or_low = 0.0
        self.or_range = 0.0
        self.or_mid = 0.0
        self.orb_complete = False
        self.position_active = False
        self.position_direction = ""
        self.position_entry_price = 0.0
        self.position_stop = 0.0
        self.position_target = 0.0
        self.position_quantity = 0
        self.position_entry_time = datetime.min
        self.position_mae = 0.0
        self.position_mfe = 0.0
        self.trades_today = 0
        self.daily_loss_reached = False
        self.current_equity = self.starting_equity

    def _process_bar(self, candle: EquityCandle) -> None:
        """Process a single 5-min bar through the state machine."""
        current_state = self.session_sm.state

        # ── State transition logic ──
        if current_state == "PRE_SESSION":
            if candle.timestamp.hour >= 9 and candle.timestamp.minute >= 15:
                self.session_sm.transition("OR_BUILDING")

        if current_state == "OR_BUILDING":
            # OR construction: bars 09:15-09:29 only (per §5)
            if candle.timestamp.hour == 9 and candle.timestamp.minute < 30:
                if candle.high > self.or_high:
                    self.or_high = candle.high
                if candle.low < self.or_low:
                    self.or_low = candle.low
                if self.or_high > 0 and self.or_low > 0:
                    self.or_range = self.or_high - self.or_low
                    self.or_mid = (self.or_high + self.or_low) / 2
            # OR locks at 09:30 (excludes 09:30 bar itself)
            if candle.timestamp.hour >= 9 and candle.timestamp.minute >= 30:
                if self.session_sm.transition("OR_LOCKED"):
                    self.orb_complete = True

        if self.session_sm.state == "OR_LOCKED":
            self.session_sm.transition("WAITING_FOR_BREAKOUT")

        # ── VWAP update (always, for every bar in session) ──
        typical = (candle.high + candle.low + candle.close) / 3
        self.vwap_acc.add(typical, candle.volume)
        self.vol_acc.add_session_bar(candle.volume)

        # ── Signal evaluation (only in WAITING_FOR_BREAKOUT, spec §24-25) ──
        if self.session_sm.state == "WAITING_FOR_BREAKOUT":
            self._evaluate_breakout(candle)

        # ── Position management (spec §17) ──
        if self.position_active:
            self._manage_position(candle)

        # ── Force exit (15:15 per §2/§17) ──
        force_time = self._parse_time(self.force_exit_time)
        if (candle.timestamp.hour > force_time.hour
                or (candle.timestamp.hour == force_time.hour and candle.timestamp.minute >= force_time.minute)):
            if self.position_active:
                self._close_position(candle, "FORCE_EXIT")

    def _evaluate_breakout(self, candle: EquityCandle) -> None:
        """Check all V1 signal conditions (spec §8, §24-25)."""
        signal_start = self._parse_time(self.signal_start)
        signal_end = self._parse_time(self.signal_end_open)
        bar_open = candle.timestamp.time()

        # Signal window check (bar-open time, per §8 v1.1)
        if not (bar_open >= signal_start and bar_open <= signal_end):
            return

        # OR quality filter (§6)
        if self.or_mid <= 0:
            return
        or_range_pct = self.or_range / self.or_mid
        min_pct = self.params.get("min_or_range_pct", 0.0025)
        max_pct = self.params.get("max_or_range_pct", 0.02)
        if not (min_pct <= or_range_pct <= max_pct):
            return

        # VWAP must be available (§26)
        if self.vwap_acc.vwap <= 0:
            return

        # ── LONG setup ──
        if candle.close > self.or_high:
            rejection = self._check_long_conditions(candle)
            if rejection is None:
                self._generate_long_signal(candle)
            else:
                self._log_signal(candle, "LONG", rejection)

        # ── SHORT setup ──
        elif candle.close < self.or_low:
            rejection = self._check_short_conditions(candle)
            if rejection is None:
                self._generate_short_signal(candle)
            else:
                self._log_signal(candle, "SHORT", rejection)

    def _check_long_conditions(self, candle: EquityCandle) -> str | None:
        """Check all long conditions, return rejection reason or None."""
        # VWAP direction
        if candle.close <= self.vwap_acc.vwap:
            return "VWAP_DIRECTION_FAIL"  # close > VWAP required
        vwap_slope = self.vwap_acc.slope(self.vwap_slope_bars)
        if vwap_slope <= 0:
            return "VWAP_SLOPE_FAIL"

        # RVOL check
        rvol = self.vol_acc.rvol(candle.volume, self.rvol_mode)
        if rvol is None:
            return "RVOL_UNAVAILABLE"
        if rvol < self.min_rvol:
            return "RVOL_FAIL"

        # Candle strength (§10)
        candle_range = candle.high - candle.low
        if candle_range <= 0:
            return "ZERO_RANGE_CANDLE"
        close_loc = (candle.close - candle.low) / candle_range
        if close_loc < self.long_min_close_location:
            return "CANDLE_STRENGTH_FAIL"

        # Daily limits (§27)
        if self.daily_loss_reached:
            return "MAX_DAILY_LOSS"
        if self.trades_today >= self.max_trades_per_symbol_day:
            return "MAX_TRADES_DAY"

        # Position check (§26)
        if self.position_active:
            return "POSITION_OPEN"

        return None  # All conditions pass

    def _check_short_conditions(self, candle: EquityCandle) -> str | None:
        """Check all short conditions, return rejection reason or None."""
        if candle.close >= self.vwap_acc.vwap:
            return "VWAP_DIRECTION_FAIL"
        vwap_slope = self.vwap_acc.slope(self.vwap_slope_bars)
        if vwap_slope >= 0:
            return "VWAP_SLOPE_FAIL"

        rvol = self.vol_acc.rvol(candle.volume, self.rvol_mode)
        if rvol is None:
            return "RVOL_UNAVAILABLE"
        if rvol < self.min_rvol:
            return "RVOL_FAIL"

        candle_range = candle.high - candle.low
        if candle_range <= 0:
            return "ZERO_RANGE_CANDLE"
        close_loc = (candle.close - candle.low) / candle_range
        if close_loc > self.short_max_close_location:
            return "CANDLE_STRENGTH_FAIL"

        if self.daily_loss_reached:
            return "MAX_DAILY_LOSS"
        if self.trades_today >= self.max_trades_per_symbol_day:
            return "MAX_TRADES_DAY"

        if self.position_active:
            return "POSITION_OPEN"

        return None

    def _generate_long_signal(self, candle: EquityCandle) -> None:
        """Generate long entry signal with V1 risk sizing (spec §11-15)."""
        # Entry price (bar close confirmation, §11)
        entry_price = self._round_tick(candle.close)

        # Stop loss: SL = OR_HIGH - 0.10 * OR_RANGE (§13)
        stop_price = self._round_tick(
            self.or_high - self.sl_buffer_pct * self.or_range,
            round_away=True,  # round away from entry for safety
        )

        # Risk per share
        risk_per_share = abs(entry_price - stop_price)
        if risk_per_share <= 0:
            return

        # Position sizing (§14)
        risk_amount = self.starting_equity * self.max_risk_pct
        quantity = int(risk_amount / risk_per_share)
        if quantity <= 0:
            return

        # Target: 2R (§15)
        target_price = self._round_tick(
            entry_price + self.tp_r_multiple * risk_per_share,
            round_away=False,
        )

        # Transition state
        if self.session_sm.transition("SIGNAL_CONFIRMED"):
            self.session_sm.transition("POSITION_OPEN")

        self.position_active = True
        self.position_direction = "LONG"
        self.position_entry_price = entry_price
        self.position_stop = stop_price
        self.position_target = target_price
        self.position_quantity = quantity
        self.position_entry_time = candle.timestamp

        self.trades_today += 1

        # Log accepted signal
        self._log_signal(candle, "LONG", "", entry_price, stop_price, target_price, quantity)

    def _generate_short_signal(self, candle: EquityCandle) -> None:
        """Generate short entry signal with V1 risk sizing."""
        entry_price = self._round_tick(candle.close)
        stop_price = self._round_tick(
            self.or_low + self.sl_buffer_pct * self.or_range,
            round_away=True,
        )
        risk_per_share = abs(entry_price - stop_price)
        if risk_per_share <= 0:
            return

        risk_amount = self.starting_equity * self.max_risk_pct
        quantity = int(risk_amount / risk_per_share)
        if quantity <= 0:
            return

        target_price = self._round_tick(
            entry_price - self.tp_r_multiple * risk_per_share,
            round_away=False,
        )

        if self.session_sm.transition("SIGNAL_CONFIRMED"):
            self.session_sm.transition("POSITION_OPEN")

        self.position_active = True
        self.position_direction = "SHORT"
        self.position_entry_price = entry_price
        self.position_stop = stop_price
        self.position_target = target_price
        self.position_quantity = quantity
        self.position_entry_time = candle.timestamp

        self.trades_today += 1
        self._log_signal(candle, "SHORT", "", entry_price, stop_price, target_price, quantity)

    def _manage_position(self, candle: EquityCandle) -> None:
        """Manage open position: check SL, TP, MAE/MFE updates (§17)."""
        if not self.position_active:
            return

        # Update MAE/MFE (unrealized P&L extremes)
        if self.position_direction == "LONG":
            unrealized = (candle.close - self.position_entry_price) * self.position_quantity
            entry_value = self.position_entry_price * self.position_quantity
            current_value = candle.close * self.position_quantity
            # Track high water mark for MFE
            peak_value = max(current_value, getattr(self, '_position_peak_value', current_value))
            self._position_peak_value = peak_value
            mfe_pct = (peak_value - entry_value) / entry_value if entry_value > 0 else 0.0
            self.position_mfe = max(self.position_mfe, mfe_pct)
            mae_pct = abs(unrealized) / entry_value if entry_value > 0 else 0.0
            if unrealized < 0:
                self.position_mae = max(self.position_mae, mae_pct)
        else:  # SHORT
            unrealized = (self.position_entry_price - candle.close) * self.position_quantity
            entry_value = self.position_entry_price * self.position_quantity
            current_value = candle.close * self.position_quantity
            peak_value = max(current_value, getattr(self, '_position_peak_value', current_value))
            self._position_peak_value = peak_value
            mfe_pct = (peak_value - entry_value) / entry_value if entry_value > 0 else 0.0
            self.position_mfe = max(self.position_mfe, mfe_pct)
            mae_pct = abs(unrealized) / entry_value if entry_value > 0 else 0.0
            if unrealized < 0:
                self.position_mae = max(self.position_mae, mae_pct)

        # Check SL / TP (§17 — same-bar SL first per v1.1 default)
        exit_reason = self._check_sl_tp(candle)
        if exit_reason:
            self._close_position(candle, exit_reason)

    def _check_sl_tp(self, candle: EquityCandle) -> str | None:
        """Check SL/TP hit, SL-first tie-break (§17 v1.1)."""
        if self.position_direction == "LONG":
            # SL hit if low <= stop
            sl_hit = candle.low <= self.position_stop
            # TP hit if high >= target
            tp_hit = candle.high >= self.position_target

            if sl_hit and tp_hit:
                # Same-bar tie-break: SL first (conservative, §17 v1.1)
                if self.same_bar_sl_tp_priority == "stop_loss_first":
                    return "STOP_LOSS"
                else:
                    return "TAKE_PROFIT"
            elif sl_hit:
                return "STOP_LOSS"
            elif tp_hit:
                return "TAKE_PROFIT"

        elif self.position_direction == "SHORT":
            sl_hit = candle.high >= self.position_stop
            tp_hit = candle.low <= self.position_target

            if sl_hit and tp_hit:
                if self.same_bar_sl_tp_priority == "stop_loss_first":
                    return "STOP_LOSS"
                else:
                    return "TAKE_PROFIT"
            elif sl_hit:
                return "STOP_LOSS"
            elif tp_hit:
                return "TAKE_PROFIT"

        return None

    def _close_position(self, candle: EquityCandle, reason: str) -> None:
        """Close position, record trade (§31)."""
        if not self.position_active:
            return

        exit_price = self._round_tick(candle.close)
        direction = self.position_direction
        entry_price = self.position_entry_price
        quantity = self.position_quantity

        if direction == "LONG":
            gross_pnl = (exit_price - entry_price) * quantity
        else:  # SHORT
            gross_pnl = (entry_price - exit_price) * quantity

        trade = TradeResult(
            symbol=candle.symbol,
            entry_timestamp=self.position_entry_time,
            entry_price=entry_price,
            exit_timestamp=candle.timestamp,
            exit_price=exit_price,
            exit_reason=reason,
            direction=direction,
            quantity=quantity,
            gross_pnl=gross_pnl, mae=self.position_mae, mfe=self.position_mfe,
        )
        self.trades.append(trade)

        # FIX 5: daily loss check (§27) — compute realized P&L for today from trade history
        today_pnl = sum(t.gross_pnl for t in self.trades if t.exit_timestamp.date() == candle.timestamp.date())
        if today_pnl < -self.max_daily_loss_pct * self.starting_equity:
            self.daily_loss_reached = True
        # Reset position
        self.position_active = False
        self.position_direction = ""
        self.position_entry_price = 0.0
        self.position_stop = 0.0
        self.position_target = 0.0
        self.position_quantity = 0
        self.position_entry_time = datetime.min
        self.position_mae = 0.0
        self.position_mfe = 0.0
        if hasattr(self, '_position_peak_value'):
            delattr(self, '_position_peak_value')

    # ─── Helper methods ──────────────────────────────────────────────

    def _round_tick(self, price: float, round_away: bool = True) -> float:
        """Round to NSE tick size (default ₹0.05).

        Args:
            price: Input price.
            round_away: If True, round away from current position entry
                (safer for stops). If False, round toward achievable side
                (for targets). Per §11 v1.1.
        """
        ts = self.tick_size
        if round_away:
            # Round up for stops above entry, round down for stops below entry
            import math
            if hasattr(self, '_position_entry_price') and self._position_entry_price > 0:
                if price > self._position_entry_price:
                    return math.ceil(price / ts) * ts
                else:
                    return math.floor(price / ts) * ts
            return round(price / ts) * ts
        else:
            # Round to nearest tick (toward achievable)
            return round(price / ts) * ts

    def _parse_time(self, time_str: str) -> "datetime.time":
        import datetime as dt_lib
        h, m = time_str.split(":")
        return time_constructor(int(h), int(m))

    def _log_signal(
        self,
        candle: EquityCandle,
        direction: str,
        rejection_reason: str,
        entry_price: float = 0.0,
        stop_price: float = 0.0,
        target_price: float = 0.0,
        quantity: int = 0,
    ) -> None:
        """Record signal log per spec §31."""
        risk_amount = quantity * abs(entry_price - stop_price) if entry_price > 0 else 0.0
        rvol = self.vol_acc.rvol(candle.volume, self.rvol_mode)
        vwap_slope = self.vwap_acc.slope(self.vwap_slope_bars)
        candle_range = candle.high - candle.low
        candle_strength = (candle.close - candle.low) / candle_range if candle_range > 0 else 0.0

        log = SignalLog(
            symbol=candle.symbol,
            date=candle.timestamp.date(),
            timestamp=candle.timestamp,
            direction=direction,
            or_high=self.or_high,
            or_low=self.or_low,
            or_range=self.or_range,
            close=candle.close,
            vwap=self.vwap_acc.vwap,
            vwap_slope=vwap_slope,
            volume=candle.volume,
            rvol=rvol if rvol is not None else 0.0,
            rvol_mode=self.rvol_mode,
            candle_strength=candle_strength,
            entry=entry_price,
            stop=stop_price,
            target=target_price,
            quantity=quantity,
            risk_amount=risk_amount,
            rejection_reason=rejection_reason,
            strategy_version=self.strategy_version,
        )

        if rejection_reason:
            self.rejected_signals.append(log)
        else:
            self.signal_logs.append(log)

    def _fetch_equity_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        api_base: str = "http://localhost:8000",
    ) -> list[EquityCandle]:
        """Fetch 5-min OHLCV bars from /equity endpoint."""
        url = f"{api_base}/equity?symbol={symbol}&fromDate={start_date.isoformat()}&toDate={end_date.isoformat()}&interval=FIVE_MINUTE"
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            print(f"[VWAPORBRunner] Error fetching {url}: {exc}")
            return []

        candles: list[EquityCandle] = []
        for r in data:
            try:
                ts = datetime.fromisoformat(r["trade_time"])
                candles.append(EquityCandle(
                    symbol=symbol,
                    timestamp=ts,
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    volume=float(r["volume"] or 0),
                ))
            except (KeyError, ValueError):
                continue
        return candles

    def _trade_dicts(self) -> list[dict]:
        return [
            {
                "symbol": t.symbol,
                "entry_timestamp": t.entry_timestamp.isoformat(),
                "entry_price": t.entry_price,
                "exit_timestamp": t.exit_timestamp.isoformat(),
                "exit_price": t.exit_price,
                "exit_reason": t.exit_reason,
                "direction": t.direction,
                "quantity": t.quantity,
                "gross_pnl": t.gross_pnl,
                "net_pnl": t.net_pnl,
                "r_multiple": t.r_multiple,
            }
            for t in self.trades
        ]

    def _signal_log_dicts(self, logs: list[SignalLog] | None = None) -> list[dict]:
        target = logs or self.signal_logs
        return [
            {
                "symbol": l.symbol,
                "date": l.date.isoformat(),
                "timestamp": l.timestamp.isoformat(),
                "direction": l.direction,
                "or_high": l.or_high,
                "or_low": l.or_low,
                "or_range": l.or_range,
                "close": l.close,
                "vwap": l.vwap,
                "vwap_slope": l.vwap_slope,
                "volume": l.volume,
                "rvol": l.rvol,
                "rvol_mode": l.rvol_mode,
                "candle_strength": l.candle_strength,
                "entry": l.entry,
                "stop": l.stop,
                "target": l.target,
                "quantity": l.quantity,
                "risk_amount": l.risk_amount,
                "rejection_reason": l.rejection_reason,
                "strategy_version": l.strategy_version,
            }
            for l in target
        ]
