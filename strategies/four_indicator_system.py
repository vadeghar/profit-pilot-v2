"""Four Indicator System - rule-based intraday NIFTY option buying (calls + puts).

Source: "The 4 Indicator System for Option Buying" podcast transcript (Darin
Dharan / Upsurge, Sep-2026), plus the symmetric put-side rules given directly
by the user (the source itself only detailed the call side on-air).

Call side (as defined on-air):
  1. SuperTrend(10, 3) on the underlying candle close defines the trend: a
     candle closing above the SuperTrend line is an uptrend ("it's just an
     escalator" - no sideways state).
  2. RSI(14) above 70 confirms momentum is with the trend - used deliberately
     against the textbook overbought-reversal reading: the system buys calls
     *because* RSI is hot, not despite it.
  3. Previous trading day's classic pivot R1 level: the close must be above
     R1 - "at least one level of resistance should have been broken".
  4. Bollinger Bands(20, 2 std-dev): the candle must close above the *upper*
     band - the "super candle" filter that keeps entries to the strongest
     ~5% of candles on the chart.
  Entry: all four conditions true on the same candle close -> buy a call.
  Exit: the SuperTrend flips from up to down.

Put side (exact mirror, as specified by the user):
  1. SuperTrend(10, 3): candle closing below the line is a downtrend.
  2. RSI(14) below 30 confirms downside momentum.
  3. Previous day's classic pivot S1 level: close must be below S1 - "one
     support level broken".
  4. Bollinger Bands(20, 2 std-dev): close must be below the *lower* band.
  Entry: all four conditions true -> buy a put.
  Exit: the SuperTrend flips from down to up.

In both cases the SuperTrend flip doubles as the trailing stop while the
trade is open - the source defines no separate stop-loss - and strike
selection is not ATM: the strike is chosen so its option premium is close to
1% of the underlying spot (NIFTY @ 25,000 -> ~Rs 250 premium), the exact
scaling rule given in the source for any underlying price. That selection
needs real option-chain prices, so it lives in
``backtest.four_indicator_backtest`` (historical) and the live paper/broker
integration, not in this module.
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
    rsi_threshold: float = 70.0            # call-side momentum floor (RSI above)
    put_rsi_threshold: float = 30.0        # put-side momentum ceiling (RSI below)
    bollinger_period: int = 20
    bollinger_std: float = 2.0
    timeframe: str = "5m"
    enable_calls: bool = True
    enable_puts: bool = True


class FourIndicatorSignalEngine:
    """Incremental, single source of truth for the entry/exit rules.

    Feed candles for the underlying strictly in time order via ``process``.
    Live trading and the historical backtest both drive this same engine so
    the two can never diverge. Call and put entries are exact mirrors of each
    other (SuperTrend direction, RSI side, Pivot R1/S1, Bollinger upper/lower)
    and are mutually exclusive since SuperTrend can only be up or down on any
    one candle.
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
        self._prev_day_s1: Optional[float] = None
        self.in_position = False
        self.position_side: Optional[str] = None  # "CE" | "PE"
        self.last_indicators: Dict[str, Any] = {}

    def _roll_day(self, candle_date: date) -> None:
        if self._current_day is None:
            self._current_day = candle_date
            return
        if candle_date != self._current_day:
            if self._day_high is not None:
                pivots = classic_pivot(self._day_high, self._day_low, self._day_close)
                self._prev_day_r1, self._prev_day_s1 = pivots["r1"], pivots["s1"]
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
        r1, s1 = self._prev_day_r1, self._prev_day_s1

        self.last_indicators = {
            "supertrend_trend": st["trend"] if st else None,
            "supertrend_line": st["line"] if st else None,
            "rsi": rsi_value,
            "bollinger_upper": bands["upper"] if bands else None,
            "bollinger_lower": bands["lower"] if bands else None,
            "prev_day_r1": r1,
            "prev_day_s1": s1,
        }

        if self.in_position:
            flipped = (self.position_side == "CE" and st and st["trend"] == "down") or \
                      (self.position_side == "PE" and st and st["trend"] == "up")
            if flipped:
                self.in_position = False
                self.position_side = None
                return {"action": "EXIT", "at": ts, "price": close, "reason": "supertrend_flip",
                        "indicators": dict(self.last_indicators)}
            return None

        if st is None or rsi_value is None or bands is None or r1 is None or s1 is None:
            return None  # still warming up: not enough history for one of the four rules

        entry_call_ok = (
            self.config.enable_calls
            and st["trend"] == "up"
            and rsi_value > self.config.rsi_threshold
            and close > r1
            and close > bands["upper"]
        )
        entry_put_ok = (
            self.config.enable_puts
            and st["trend"] == "down"
            and rsi_value < self.config.put_rsi_threshold
            and close < s1
            and close < bands["lower"]
        )
        if entry_call_ok:
            self.in_position, self.position_side = True, "CE"
            return {"action": "ENTER", "side": "CE", "at": ts, "price": close,
                    "indicators": dict(self.last_indicators)}
        if entry_put_ok:
            self.in_position, self.position_side = True, "PE"
            return {"action": "ENTER", "side": "PE", "at": ts, "price": close,
                    "indicators": dict(self.last_indicators)}
        return None


class FourIndicatorSystemStrategy(StrategyBase):
    """Live/generic-engine adapter around :class:`FourIndicatorSignalEngine`.

    Runs on the underlying's own candles (default NSE:NIFTY) and emits a BUY
    signal to enter a call or put, a SELL signal to flatten it. Resolving and
    pricing the real CE/PE contract (1%-of-spot premium strike) is the
    caller's job - see ``backtest.four_indicator_backtest`` for the
    historical runner that does this against real option data.
    """

    def _init_indicators(self) -> None:
        cfg = FourIndicatorConfig(
            supertrend_period=int(self.params.get("supertrend_period", 10)),
            supertrend_multiplier=float(self.params.get("supertrend_multiplier", 3.0)),
            rsi_period=int(self.params.get("rsi_period", 14)),
            rsi_threshold=float(self.params.get("rsi_threshold", 70.0)),
            put_rsi_threshold=float(self.params.get("put_rsi_threshold", 30.0)),
            bollinger_period=int(self.params.get("bollinger_period", 20)),
            bollinger_std=float(self.params.get("bollinger_std", 2.0)),
            timeframe=str(self.params.get("timeframe", "5m")),
            enable_calls=bool(self.params.get("enable_calls", True)),
            enable_puts=bool(self.params.get("enable_puts", True)),
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
                         metadata={"reason": "four_indicator_entry", "side": result["side"],
                                  **result["indicators"]})
        return Signal(strategy_id=self.strategy_id, instrument=inst, action=OrderSide.SELL,
                     quantity=self.quantity, order_type=OrderType.MARKET,
                     metadata={"reason": "supertrend_flip_exit", **result["indicators"]})


__all__ = [
    "FourIndicatorConfig", "FourIndicatorSignalEngine", "FourIndicatorSystemStrategy",
    "SuperTrendState", "rsi", "bollinger_bands", "classic_pivot", "sma", "stdev", "true_range",
]
