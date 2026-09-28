"""Four Indicator System - rule-based intraday NIFTY option buying (long calls only).

Source: "The 4 Indicator System for Option Buying" podcast transcript (Darin
Dharan / Upsurge, Sep-2026). Rules implemented exactly as described on-air,
nothing added:

  1. SuperTrend(10, 3) on the underlying candle close defines the trend: a
     candle closing above the SuperTrend line is an uptrend, below is a
     downtrend ("it's just an escalator" - no sideways state).
  2. RSI(14) above 70 confirms momentum is with the trend. This is used
     deliberately against the textbook overbought-reversal reading: the
     system buys calls *because* RSI is hot, not despite it.
  3. Previous trading day's classic pivot R1 level: the close must be above
     R1 - "at least one level of resistance should have been broken" before
     a long is taken.
  4. Bollinger Bands(20, 2 std-dev): the candle must close above the *upper*
     band - the "super candle" filter that keeps entries to the strongest
     ~5% of candles on the chart.

  Entry (long / call buying): all four conditions true on the same candle
  close.
  Exit: the SuperTrend flips from up to down. It doubles as the trailing
  stop while the trade is open - the source defines no separate stop-loss.

  Strike selection is not ATM: the strike is chosen so its option premium is
  close to 1% of the underlying spot (NIFTY @ 25,000 -> ~Rs 250 premium),
  the exact scaling rule given in the source for any underlying price. That
  selection needs real option-chain prices, so it lives in
  ``backtest.four_indicator_backtest`` (historical) and the live paper/broker
  integration, not in this module.

  The short (put-buying) side was explicitly left undefined in the source
  ("more advanced concepts... in the course") and is intentionally NOT
  implemented here - only what was defined on-air is built.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from core.models import Candle, OrderSide, OrderType, Signal, Tick
from strategies import StrategyBase, StrategyRegistry


# --------------------------------------------------------------------------- pure indicator math
def true_range(prev_close: float, high: float, low: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def sma(values: Sequence[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def stdev(values: Sequence[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    window = values[-period:]
    mean = sum(window) / period
    variance = sum((v - mean) ** 2 for v in window) / period
    return variance ** 0.5


def rsi(closes: Sequence[float], period: int = 14) -> Optional[float]:
    """Wilder-style RSI (matches StrategyBase.rsi's simple-average variant)."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def bollinger_bands(closes: Sequence[float], period: int = 20,
                    num_std: float = 2.0) -> Optional[Dict[str, float]]:
    mid = sma(closes, period)
    sd = stdev(closes, period)
    if mid is None or sd is None:
        return None
    return {"upper": mid + num_std * sd, "middle": mid, "lower": mid - num_std * sd}


def classic_pivot(prev_high: float, prev_low: float, prev_close: float) -> Dict[str, float]:
    """Standard floor-trader pivot; only R1 is used by this system."""
    pivot = (prev_high + prev_low + prev_close) / 3.0
    return {"pivot": pivot, "r1": 2 * pivot - prev_low, "s1": 2 * pivot - prev_high}


@dataclass
class SuperTrendState:
    """Incremental SuperTrend(period, multiplier) over high/low/close bars."""
    period: int = 10
    multiplier: float = 3.0
    atr: Optional[float] = None
    upper_band: Optional[float] = None
    lower_band: Optional[float] = None
    trend: Optional[str] = None  # "up" | "down"
    prev_close: Optional[float] = None
    _tr_history: List[float] = field(default_factory=list)

    def update(self, high: float, low: float, close: float) -> Optional[Dict[str, Any]]:
        if self.prev_close is None:
            self.prev_close = close
            return None
        tr = true_range(self.prev_close, high, low)
        self._tr_history.append(tr)
        if len(self._tr_history) < self.period:
            self.prev_close = close
            return None
        if self.atr is None:
            self.atr = sum(self._tr_history[-self.period:]) / self.period
        else:
            self.atr = (self.atr * (self.period - 1) + tr) / self.period
        hl2 = (high + low) / 2.0
        basic_upper = hl2 + self.multiplier * self.atr
        basic_lower = hl2 - self.multiplier * self.atr
        if self.upper_band is None:
            final_upper, final_lower = basic_upper, basic_lower
        else:
            final_upper = (basic_upper if (basic_upper < self.upper_band or self.prev_close > self.upper_band)
                          else self.upper_band)
            final_lower = (basic_lower if (basic_lower > self.lower_band or self.prev_close < self.lower_band)
                          else self.lower_band)
        if self.trend is None:
            trend = "up" if close >= final_upper else "down"
        elif self.trend == "up":
            trend = "down" if close < final_lower else "up"
        else:
            trend = "up" if close > final_upper else "down"
        self.upper_band, self.lower_band, self.trend = final_upper, final_lower, trend
        self.prev_close = close
        line = final_lower if trend == "up" else final_upper
        return {"trend": trend, "line": line, "atr": self.atr}


@dataclass
class FourIndicatorConfig:
    supertrend_period: int = 10
    supertrend_multiplier: float = 3.0
    rsi_period: int = 14
    rsi_threshold: float = 70.0
    bollinger_period: int = 20
    bollinger_std: float = 2.0
    timeframe: str = "5m"


class FourIndicatorSignalEngine:
    """Incremental, single source of truth for the entry/exit rules.

    Feed candles for the underlying strictly in time order via ``process``.
    Live trading and the historical backtest both drive this same engine so
    the two can never diverge. Only long (call-buying) entries are generated
    - see the module docstring for why the put side is out of scope.
    """

    def __init__(self, config: Optional[FourIndicatorConfig] = None):
        self.config = config or FourIndicatorConfig()
        self.supertrend = SuperTrendState(self.config.supertrend_period, self.config.supertrend_multiplier)
        self._closes: List[float] = []
        self._current_day: Optional[date] = None
        self._day_high: Optional[float] = None
        self._day_low: Optional[float] = None
        self._day_close: Optional[float] = None
        self._prev_day_r1: Optional[float] = None
        self.in_position = False
        self.last_indicators: Dict[str, Any] = {}

    def _roll_day(self, candle_date: date) -> None:
        if self._current_day is None:
            self._current_day = candle_date
            return
        if candle_date != self._current_day:
            if self._day_high is not None:
                self._prev_day_r1 = classic_pivot(self._day_high, self._day_low, self._day_close)["r1"]
            self._current_day = candle_date
            self._day_high = self._day_low = self._day_close = None

    def process(self, candle: Candle) -> Optional[Dict[str, Any]]:
        ts = candle.timestamp
        candle_date = ts.date()
        self._roll_day(candle_date)
        self._day_high = candle.high if self._day_high is None else max(self._day_high, candle.high)
        self._day_low = candle.low if self._day_low is None else min(self._day_low, candle.low)
        self._day_close = candle.close

        st = self.supertrend.update(candle.high, candle.low, candle.close)
        self._closes.append(candle.close)
        close = candle.close

        rsi_value = rsi(self._closes, self.config.rsi_period)
        bands = bollinger_bands(self._closes, self.config.bollinger_period, self.config.bollinger_std)
        r1 = self._prev_day_r1

        self.last_indicators = {
            "supertrend_trend": st["trend"] if st else None,
            "supertrend_line": st["line"] if st else None,
            "rsi": rsi_value,
            "bollinger_upper": bands["upper"] if bands else None,
            "prev_day_r1": r1,
        }

        if self.in_position:
            if st and st["trend"] == "down":
                self.in_position = False
                return {"action": "EXIT", "at": ts, "price": close, "reason": "supertrend_flip",
                        "indicators": dict(self.last_indicators)}
            return None

        if st is None or rsi_value is None or bands is None or r1 is None:
            return None  # still warming up: not enough history for one of the four rules

        entry_ok = (
            st["trend"] == "up"
            and rsi_value > self.config.rsi_threshold
            and close > r1
            and close > bands["upper"]
        )
        if entry_ok:
            self.in_position = True
            return {"action": "ENTER", "side": "CE", "at": ts, "price": close,
                    "indicators": dict(self.last_indicators)}
        return None


class FourIndicatorSystemStrategy(StrategyBase):
    """Live/generic-engine adapter around :class:`FourIndicatorSignalEngine`.

    Runs on the underlying's own candles (default NSE:NIFTY) and emits a BUY
    signal to enter a call, a SELL signal to flatten it. Resolving and
    pricing the real CE contract (1%-of-spot premium strike) is the caller's
    job - see ``backtest.four_indicator_backtest`` for the historical runner
    that does this against real option data.
    """

    def _init_indicators(self) -> None:
        cfg = FourIndicatorConfig(
            supertrend_period=int(self.params.get("supertrend_period", 10)),
            supertrend_multiplier=float(self.params.get("supertrend_multiplier", 3.0)),
            rsi_period=int(self.params.get("rsi_period", 14)),
            rsi_threshold=float(self.params.get("rsi_threshold", 70.0)),
            bollinger_period=int(self.params.get("bollinger_period", 20)),
            bollinger_std=float(self.params.get("bollinger_std", 2.0)),
            timeframe=str(self.params.get("timeframe", "5m")),
        )
        self.engine = FourIndicatorSignalEngine(cfg)
        self.instrument = self.params.get("instrument", "NSE:NIFTY")
        self.quantity = int(self.params.get("quantity", 1))

    def on_tick(self, tick: Tick) -> Optional[Signal]:
        return None

    def on_candle(self, candle: Candle) -> Optional[Signal]:
        result = self.engine.process(candle)
        for name, value in self.engine.last_indicators.items():
            self.store_indicator(name, value)
        if result is None:
            return None
        inst = getattr(candle, "instrument", None) or self.instrument
        if result["action"] == "ENTER":
            return Signal(strategy_id=self.strategy_id, instrument=inst, action=OrderSide.BUY,
                         quantity=self.quantity, order_type=OrderType.MARKET,
                         metadata={"reason": "four_indicator_entry", "side": "CE", **result["indicators"]})
        return Signal(strategy_id=self.strategy_id, instrument=inst, action=OrderSide.SELL,
                     quantity=self.quantity, order_type=OrderType.MARKET,
                     metadata={"reason": "supertrend_flip_exit", **result["indicators"]})


__all__ = [
    "FourIndicatorConfig", "FourIndicatorSignalEngine", "FourIndicatorSystemStrategy",
    "SuperTrendState", "rsi", "bollinger_bands", "classic_pivot", "sma", "stdev", "true_range",
]
