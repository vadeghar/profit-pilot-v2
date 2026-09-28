"""Unit tests for the Four Indicator System's rule engine (no network)."""
from datetime import datetime, timedelta

import pytest

from core.models import Candle, OrderSide
from strategies.four_indicator_system import (
    FourIndicatorConfig, FourIndicatorSignalEngine, FourIndicatorSystemStrategy,
    bollinger_bands, classic_pivot, rsi,
)
from utils.timezone import IST


def make_candle(day: int, minute_offset: int, o, h, l, c) -> Candle:
    ts = datetime(2026, 1, day, 9, 15, tzinfo=IST) + timedelta(minutes=minute_offset)
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=1000.0, instrument="NSE:NIFTY")


def test_classic_pivot_r1():
    p = classic_pivot(prev_high=100.0, prev_low=90.0, prev_close=95.0)
    assert p["pivot"] == pytest.approx(95.0)
    assert p["r1"] == pytest.approx(2 * 95.0 - 90.0)


def test_rsi_returns_none_until_warm():
    assert rsi([1.0, 2.0], period=14) is None
    closes = [100 + i for i in range(20)]  # monotonic up -> RSI should be high
    value = rsi(closes, period=14)
    assert value is not None and value > 70


def test_bollinger_bands_upper_above_close_when_flat():
    closes = [100.0] * 25
    bands = bollinger_bands(closes, period=20, num_std=2.0)
    assert bands is not None
    assert bands["upper"] == pytest.approx(100.0)  # zero std-dev when perfectly flat


def _feed_warmup_day(engine: FourIndicatorSignalEngine, day: int, base: float, bars: int = 40):
    """A quiet, flat day: builds SuperTrend/RSI/Bollinger history + a day-high/low/close
    for tomorrow's pivot, without tripping any entry condition."""
    for i in range(bars):
        c = make_candle(day, i * 5, base, base + 1, base - 1, base)
        engine.process(c)


def test_no_entry_before_all_four_conditions_are_warm():
    engine = FourIndicatorSignalEngine(FourIndicatorConfig())
    _feed_warmup_day(engine, day=1, base=25000.0, bars=5)  # not enough bars for any indicator
    assert engine.in_position is False


def test_entry_requires_all_four_conditions_together():
    """A strong breakout candle only triggers ENTER once trend+momentum+R1+band all agree."""
    engine = FourIndicatorSignalEngine(FourIndicatorConfig(rsi_threshold=70.0))
    _feed_warmup_day(engine, day=1, base=25000.0, bars=40)
    # Second quiet day establishes yesterday's pivot R1 close to 25000.
    _feed_warmup_day(engine, day=2, base=25000.0, bars=40)
    # Day 3: a rally that should eventually satisfy trend, RSI, R1 and the
    # Bollinger "super candle" breakout together.
    result = None
    price = 25000.0
    for i in range(30):
        price += 25.0  # steady rally: breaks R1, pushes RSI up, stays above SuperTrend
        c = make_candle(3, i * 5, price - 25, price + 5, price - 30, price)
        r = engine.process(c)
        if r:
            result = r
            break
    assert result is not None
    assert result["action"] == "ENTER"
    assert result["side"] == "CE"
    assert engine.in_position is True
    ind = result["indicators"]
    assert ind["supertrend_trend"] == "up"
    assert ind["rsi"] > 70.0
    assert ind["prev_day_r1"] is not None
    assert result["price"] > ind["bollinger_upper"]
    assert result["price"] > ind["prev_day_r1"]


def test_exit_on_supertrend_flip_after_entry():
    engine = FourIndicatorSignalEngine(FourIndicatorConfig())
    _feed_warmup_day(engine, day=1, base=25000.0, bars=40)
    _feed_warmup_day(engine, day=2, base=25000.0, bars=40)
    price = 25000.0
    entered = False
    for i in range(30):
        price += 25.0
        c = make_candle(3, i * 5, price - 25, price + 5, price - 30, price)
        r = engine.process(c)
        if r and r["action"] == "ENTER":
            entered = True
            break
    assert entered
    # A sharp reversal should eventually flip SuperTrend to down and exit.
    exit_result = None
    for i in range(30):
        price -= 60.0
        c = make_candle(3, 150 + i * 5, price + 60, price + 65, price - 5, price)
        r = engine.process(c)
        if r and r["action"] == "EXIT":
            exit_result = r
            break
    assert exit_result is not None
    assert exit_result["reason"] == "supertrend_flip"
    assert engine.in_position is False


def test_entry_put_mirrors_call_on_a_selloff():
    """A steady decline should trigger a PE entry: downtrend, RSI<30, below S1, below lower band."""
    engine = FourIndicatorSignalEngine(FourIndicatorConfig(put_rsi_threshold=30.0))
    _feed_warmup_day(engine, day=1, base=25000.0, bars=40)
    _feed_warmup_day(engine, day=2, base=25000.0, bars=40)
    result = None
    price = 25000.0
    for i in range(30):
        price -= 25.0  # steady selloff: breaks S1, pushes RSI down, stays below SuperTrend
        c = make_candle(3, i * 5, price + 25, price + 30, price - 5, price)
        r = engine.process(c)
        if r:
            result = r
            break
    assert result is not None
    assert result["action"] == "ENTER"
    assert result["side"] == "PE"
    assert engine.in_position is True
    assert engine.position_side == "PE"
    ind = result["indicators"]
    assert ind["supertrend_trend"] == "down"
    assert ind["rsi"] < 30.0
    assert ind["prev_day_s1"] is not None
    assert result["price"] < ind["bollinger_lower"]
    assert result["price"] < ind["prev_day_s1"]


def test_exit_put_on_supertrend_flip_up():
    engine = FourIndicatorSignalEngine(FourIndicatorConfig())
    _feed_warmup_day(engine, day=1, base=25000.0, bars=40)
    _feed_warmup_day(engine, day=2, base=25000.0, bars=40)
    price = 25000.0
    entered = False
    for i in range(30):
        price -= 25.0
        c = make_candle(3, i * 5, price + 25, price + 30, price - 5, price)
        r = engine.process(c)
        if r and r["action"] == "ENTER":
            entered = True
            break
    assert entered and engine.position_side == "PE"
    exit_result = None
    for i in range(30):
        price += 60.0
        c = make_candle(3, 150 + i * 5, price - 60, price + 5, price - 65, price)
        r = engine.process(c)
        if r and r["action"] == "EXIT":
            exit_result = r
            break
    assert exit_result is not None
    assert exit_result["reason"] == "supertrend_flip"
    assert engine.in_position is False
    assert engine.position_side is None


def test_calls_and_puts_can_be_individually_disabled():
    engine = FourIndicatorSignalEngine(FourIndicatorConfig(enable_puts=False))
    _feed_warmup_day(engine, day=1, base=25000.0, bars=40)
    _feed_warmup_day(engine, day=2, base=25000.0, bars=40)
    price = 25000.0
    for i in range(30):
        price -= 25.0
        c = make_candle(3, i * 5, price + 25, price + 30, price - 5, price)
        r = engine.process(c)
        assert not (r and r["action"] == "ENTER")  # puts disabled: never enters
    assert engine.in_position is False


def test_strategy_wrapper_emits_put_signal_on_selloff():
    strat = FourIndicatorSystemStrategy("four_indicator_system", "Four Indicator System",
                                        {"instrument": "NSE:NIFTY"})
    strat.initialize()
    price = 25000.0
    for day, base in ((1, 25000.0), (2, 25000.0)):
        for i in range(40):
            strat.on_candle(make_candle(day, i * 5, base, base + 1, base - 1, base))
    signal = None
    for i in range(30):
        price -= 25.0
        c = make_candle(3, i * 5, price + 25, price + 30, price - 5, price)
        sig = strat.on_candle(c)
        if sig:
            signal = sig
            break
    assert signal is not None
    assert signal.action == OrderSide.BUY
    assert signal.metadata["side"] == "PE"


def test_strategy_wrapper_emits_signals_matching_engine():
    strat = FourIndicatorSystemStrategy("four_indicator_system", "Four Indicator System",
                                        {"instrument": "NSE:NIFTY"})
    strat.initialize()
    price = 25000.0
    for day, base in ((1, 25000.0), (2, 25000.0)):
        for i in range(40):
            strat.on_candle(make_candle(day, i * 5, base, base + 1, base - 1, base))
    signal = None
    for i in range(30):
        price += 25.0
        c = make_candle(3, i * 5, price - 25, price + 5, price - 30, price)
        sig = strat.on_candle(c)
        if sig:
            signal = sig
            break
    assert signal is not None
    assert signal.action == OrderSide.BUY
    assert signal.metadata["reason"] == "four_indicator_entry"
    assert signal.metadata["side"] == "CE"
