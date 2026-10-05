"""NIFTY Afternoon Momentum: buy an ATM weekly option at 14:25 in the direction of the day, on high-VIX days only.

Rules (all times IST, NIFTY 50 index on 5-minute candles):

  1. Regime gate   - India VIX previous close >= ``vix_min`` (15). Below it the strategy does not trade.
  2. Signal 14:25  - r = index / previous close - 1 at the close of the 14:20 candle. sigma = the average of
                     |index / day open - 1| at that same time of day over the last 14 sessions (the "noise"
                     a normal day shows by then). Trade only if |r| >= ``threshold`` x sigma.
  3. Entry         - r > 0 buys the ATM call, r < 0 buys the ATM put, nearest weekly expiry that is at least
                     ``min_dte`` (1) day away, so never an expiry-day contract. One lot, one trade a day.
  4. Exit          - 15:10 on the clock, or earlier if the premium falls ``premium_stop`` (30%) below entry.

Sources and the evidence for and against these rules: docs/trading/NIFTY_AFTERNOON_MOMENTUM.md.
The rule functions here are shared by the backtest (backtest.py) and the candle strategy class below.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.models import Candle, OrderSide, OrderType, Signal, Tick
from core.strategy import StrategyBase
from market_data.expiries import nifty_lot_size, weekly_expiry_weekday
from market_data.trading_days import TradingCalendar
from utils.timezone import ensure_ist

STRIKE_STEP = 50
SESSION_OPEN_MINUTE = 9 * 60 + 15
BAR_MINUTES = 5
BARS_PER_DAY = 75              # 09:15 ... 15:25 candle starts


@dataclass(frozen=True)
class Params:
    vix_min: float = 15.0
    threshold: float = 1.0                # |day return| needed, in units of sigma
    sigma_lookback: int = 14              # sessions in the time-of-day noise average
    decide_time: time = time(14, 25)      # signal is read at the close of the candle ending here
    exit_time: time = time(15, 10)        # flat by here (the spot index freezes at 15:15)
    premium_stop: float = 0.30
    min_dte: int = 1
    itm_steps: int = 0                    # 0 = ATM, 1 = one strike in the money
    sides: Tuple[str, ...] = ("CE", "PE")
    lots: int = 1
    max_premium_pct: float = 0.5          # skip the trade if the premium outlay exceeds this share of capital

    @classmethod
    def from_dict(cls, values: Optional[Dict[str, Any]]) -> "Params":
        known = {k: v for k, v in (values or {}).items() if k in cls.__dataclass_fields__}
        for key in ("decide_time", "exit_time"):
            if isinstance(known.get(key), str):
                known[key] = time.fromisoformat(known[key])
        if "sides" in known:
            known["sides"] = tuple(known["sides"])
        return cls(**known)


def bar_index(ts: datetime) -> int:
    """Index of the 5-minute candle that starts at ``ts`` (09:15 -> 0, 14:20 -> 61, 15:05 -> 70)."""
    return (ts.hour * 60 + ts.minute - SESSION_OPEN_MINUTE) // BAR_MINUTES


def closing_bar_index(clock: time) -> int:
    """Index of the candle that *closes* at ``clock`` (14:25 -> 61)."""
    return (clock.hour * 60 + clock.minute - SESSION_OPEN_MINUTE) // BAR_MINUTES - 1


def noise_sigma(history: List[Dict[int, float]], index: int, lookback: int) -> Optional[float]:
    """Average |close / day open - 1| at candle ``index`` over the last ``lookback`` sessions.

    ``history`` holds one {candle index: abs move} dict per earlier session, oldest first.
    None until ``lookback`` sessions exist."""
    if len(history) < lookback:
        return None
    values = [day[index] for day in history[-lookback:] if index in day]
    return sum(values) / len(values) if values else None


def decide(prev_close: float, price: float, sigma: Optional[float], vix: Optional[float], params: Params) -> Optional[str]:
    """"CE", "PE" or None for the 14:25 decision."""
    if not prev_close or sigma is None or vix is None or vix < params.vix_min:
        return None
    r = price / prev_close - 1.0
    if abs(r) < params.threshold * sigma:
        return None
    right = "CE" if r > 0 else "PE"
    return right if right in params.sides else None


def weekly_expiry(day: date, min_dte: int, calendar: TradingCalendar) -> date:
    """Nearest NIFTY weekly expiry at least ``min_dte`` calendar days after ``day``."""
    d = day + timedelta(days=min_dte)
    while True:
        candidate = d + timedelta(days=(weekly_expiry_weekday(d) - d.weekday()) % 7)
        expiry = calendar.previous_trading_day(candidate)
        if (expiry - day).days >= min_dte:
            return expiry
        d = candidate + timedelta(days=1)


def pick_strike(spot: float, right: str, itm_steps: int = 0) -> int:
    atm = int(round(spot / STRIKE_STEP) * STRIKE_STEP)
    return atm - itm_steps * STRIKE_STEP if right == "CE" else atm + itm_steps * STRIKE_STEP


class NiftyAfternoonMomentumStrategy(StrategyBase):
    """Candle form of the rules: feed NIFTY 5-minute candles in time order, get a BUY at 14:25 and a SELL at 15:10.

    India VIX (previous close) is supplied through ``set_vix`` each morning or ``params["vix"]``. The
    premium stop needs option quotes, so it is left to whoever executes the signal
    (``metadata["premium_stop_pct"]``); call ``on_exit_fill`` when it fires."""

    def _init_indicators(self) -> None:
        self.p = Params.from_dict(self.params)
        self.capital = float(self.params.get("capital", 50_000.0))
        self.calendar = TradingCalendar()
        self._vix: Optional[float] = self.params.get("vix")
        self._history: List[Dict[int, float]] = []
        self._day: Optional[date] = None
        self._day_open = 0.0
        self._moves: Dict[int, float] = {}
        self._prev_close: Optional[float] = None
        self._last_close: Optional[float] = None
        self._position: Optional[Dict[str, Any]] = None
        self._traded_today = False
        self._decide_i = closing_bar_index(self.p.decide_time)
        self._exit_i = closing_bar_index(self.p.exit_time)

    def set_vix(self, previous_close: float) -> None:
        self._vix = float(previous_close)

    def on_tick(self, tick: Tick) -> Optional[Signal]:
        return None

    def on_entry_fill(self, instrument: str, quantity: int, price: float) -> None:
        if self._position:
            self._position["entry"] = price

    def on_exit_fill(self) -> None:
        self._position = None

    def on_candle(self, candle: Candle) -> Optional[Signal]:
        if not hasattr(self, "p"):
            self._init_indicators()
        ts = ensure_ist(candle.timestamp)
        i = bar_index(ts)
        if i < 0 or i >= BARS_PER_DAY:
            return None
        if ts.date() != self._day:
            if self._day is not None:
                self._history.append(self._moves)
                self._prev_close = self._last_close
            self._day, self._day_open, self._moves = ts.date(), candle.open, {}
            self._traded_today, self._position = False, None
        self._moves[i] = abs(candle.close / self._day_open - 1.0)
        self._last_close = candle.close

        if self._position and i >= self._exit_i:
            pos, self._position = self._position, None
            return self._signal(pos["instrument"], OrderSide.SELL, pos["quantity"], ts, "afternoon_momentum_time_exit",
                                {"right": pos["right"], "strike": pos["strike"], "expiry": pos["expiry"]})
        if i != self._decide_i or self._traded_today or self._prev_close is None:
            return None
        sigma = noise_sigma(self._history, i, self.p.sigma_lookback)
        right = decide(self._prev_close, candle.close, sigma, self._vix, self.p)
        if right is None:
            return None
        expiry = weekly_expiry(ts.date(), self.p.min_dte, self.calendar)
        strike = pick_strike(candle.close, right, self.p.itm_steps)
        quantity = nifty_lot_size(ts.date()) * self.p.lots
        instrument = f"NIFTY {expiry:%d%b%y}".upper() + f" {strike} {right}"
        self._traded_today = True
        self._position = {"instrument": instrument, "quantity": quantity, "right": right, "strike": strike,
                          "expiry": expiry.isoformat()}
        return self._signal(instrument, OrderSide.BUY, quantity, ts, "afternoon_momentum_entry", {
            "right": right, "strike": strike, "expiry": expiry.isoformat(), "spot": candle.close,
            "day_return": candle.close / self._prev_close - 1.0, "sigma": sigma, "vix": self._vix,
            "premium_stop_pct": self.p.premium_stop, "exit_time": self.p.exit_time.isoformat(),
            "max_premium": self.capital * self.p.max_premium_pct / quantity})

    def _signal(self, instrument: str, side: OrderSide, quantity: int, ts: datetime, reason: str,
                metadata: Dict[str, Any]) -> Signal:
        signal = Signal(strategy_id=self.strategy_id, instrument=instrument, action=side, quantity=quantity,
                        order_type=OrderType.MARKET, reason=reason, metadata=metadata, timestamp=ts)
        self.add_signal(signal)
        return signal
