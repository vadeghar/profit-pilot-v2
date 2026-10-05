"""Tests for NIFTY Afternoon Momentum: rules, candle strategy, premium model and the backtest simulator (no network)."""
from datetime import date, datetime, time, timedelta

import pytest

from core.models import Candle, OrderSide
from core.strategy import StrategyRegistry
from market_data.trading_days import TradingCalendar
from trading_strategies.nifty_afternoon_momentum.backtest import TIGHT, Day, Simulator, afternoon, metrics
from trading_strategies.nifty_afternoon_momentum.pricing import bs_price, variance_time_years
from trading_strategies.nifty_afternoon_momentum.strategy import (NiftyAfternoonMomentumStrategy, Params, bar_index,
                                                                 closing_bar_index, decide, noise_sigma, pick_strike,
                                                                 weekly_expiry)
from utils.timezone import IST

CAL = TradingCalendar()


# ------------------------------------------------------------------ rules
def test_bar_indices():
    assert bar_index(datetime(2026, 3, 9, 9, 15)) == 0 and bar_index(datetime(2026, 3, 9, 14, 20)) == 61
    assert closing_bar_index(time(14, 25)) == 61 and closing_bar_index(time(15, 10)) == 70


def test_noise_sigma_needs_full_lookback_and_averages_same_time_of_day():
    history = [{61: 0.004}] * 13
    assert noise_sigma(history, 61, 14) is None
    assert noise_sigma(history + [{61: 0.018}], 61, 14) == pytest.approx(0.005)


def test_decide_gate_threshold_and_direction():
    p = Params()
    assert decide(24000, 24240, 0.005, 16.0, p) == "CE"          # +1.0% vs 0.5% noise
    assert decide(24000, 23760, 0.005, 16.0, p) == "PE"
    assert decide(24000, 24240, 0.005, 14.9, p) is None          # VIX gate
    assert decide(24000, 24060, 0.005, 16.0, p) is None          # +0.25% is inside the noise
    assert decide(24000, 24240, None, 16.0, p) is None           # no history yet
    assert decide(24000, 24240, 0.005, 16.0, Params(sides=("PE",))) is None


def test_weekly_expiry_skips_expiry_day_and_rolls_back_over_holidays():
    assert weekly_expiry(date(2026, 3, 9), 1, CAL) == date(2026, 3, 10)     # Monday -> Tuesday
    assert weekly_expiry(date(2026, 3, 10), 1, CAL) == date(2026, 3, 17)    # expiry day -> next week
    assert weekly_expiry(date(2026, 3, 10), 0, CAL) == date(2026, 3, 10)
    assert weekly_expiry(date(2026, 3, 27), 1, CAL) == date(2026, 3, 30)    # Tue 31-Mar-2026 is a holiday
    assert weekly_expiry(date(2025, 6, 2), 1, CAL) == date(2025, 6, 5)      # Thursday expiries before Sep-2025


def test_pick_strike():
    assert pick_strike(24024, "CE") == 24000 and pick_strike(24026, "PE") == 24050
    assert pick_strike(24024, "CE", 1) == 23950 and pick_strike(24024, "PE", 1) == 24050


# ------------------------------------------------------------------ premium model
def test_bs_price_parity_and_expiry():
    c, p = bs_price(24000, 24000, 5 / 252, 0.15, "CE"), bs_price(24000, 24000, 5 / 252, 0.15, "PE")
    assert 150 < p < c < 250                                      # ATM, 5 trading days, 15% vol
    assert bs_price(24100, 24000, 0, 0.15, "CE") == 100 and bs_price(24100, 24000, 0, 0.15, "PE") == 0


def test_variance_time_weights_the_session():
    monday = date(2026, 3, 9)
    at_open = variance_time_years(monday, 0, date(2026, 3, 10), CAL)
    at_close = variance_time_years(monday, 375, date(2026, 3, 10), CAL)
    assert at_open * 252 == pytest.approx(1.7) and at_close * 252 == pytest.approx(1.0)
    friday = date(2026, 3, 6)                                     # the weekend adds no trading days
    assert variance_time_years(friday, 375, date(2026, 3, 10), CAL) * 252 == pytest.approx(2.0)


# ------------------------------------------------------------------ candle strategy
def _session(day: date, start: float, drift_per_bar: float):
    px = start
    for i in range(75):
        ts = datetime.combine(day, time(9, 15), tzinfo=IST) + timedelta(minutes=5 * i)
        nxt = px + drift_per_bar
        yield Candle(ts, px, max(px, nxt), min(px, nxt), nxt, 0, instrument="NIFTY", timeframe="5m")
        px = nxt


def _feed(strategy, days_and_drifts, start=24000.0):
    signals, px = [], start
    for day, drift in days_and_drifts:
        for candle in _session(day, px, drift):
            s = strategy.on_candle(candle)
            if s:
                signals.append(s)
            px = candle.close
    return signals


def _quiet_days(n, end):
    out, d = [], end
    while len(out) < n:
        d -= timedelta(days=1)
        if CAL.is_trading_day(d):
            out.append((d, 0.2 if len(out) % 2 else -0.2))
    return out[::-1]


def test_strategy_buys_put_on_a_falling_high_vix_day_and_exits_at_1510():
    day = date(2026, 3, 9)
    strat = StrategyRegistry.create("nifty_afternoon_momentum", "t1", {"capital": 50_000})
    strat.initialize()
    strat.set_vix(18.0)
    signals = _feed(strat, _quiet_days(14, day) + [(day, -4.0)])
    assert [s.action for s in signals] == [OrderSide.BUY, OrderSide.SELL]
    buy, sell = signals
    assert buy.timestamp.time() == time(14, 20) and sell.timestamp.time() == time(15, 5)   # candle starts
    assert buy.metadata["right"] == "PE" and buy.metadata["expiry"] == "2026-03-10" and buy.quantity == 65
    assert buy.instrument == sell.instrument and buy.metadata["premium_stop_pct"] == 0.30


def test_strategy_stays_out_below_the_vix_gate_and_on_quiet_days():
    day = date(2026, 3, 9)
    low_vix = NiftyAfternoonMomentumStrategy("t2", "x", {"vix": 12.0})
    low_vix.initialize()
    assert _feed(low_vix, _quiet_days(14, day) + [(day, -4.0)]) == []
    quiet = NiftyAfternoonMomentumStrategy("t3", "x", {"vix": 18.0})
    quiet.initialize()
    assert _feed(quiet, _quiet_days(14, day) + [(day, 0.1)]) == []


# ------------------------------------------------------------------ backtest simulator
def _day(drift: float, vix: float = 18.0) -> Day:
    bars, px = {}, 24000.0
    for i in range(75):
        nxt = px + drift
        bars[i] = (px, max(px, nxt), min(px, nxt), nxt)
        px = nxt
    history = [{i: 0.0005 * (i + 1) ** 0.5 for i in range(75)}] * 14
    return Day(date(2026, 3, 9), bars, 24000.0, 24000.0, vix, history, 14)


def test_backtest_trend_day_wins_and_costs_are_charged():
    trades = afternoon(_day(-4.0), Simulator(TIGHT))
    assert len(trades) == 1
    t = trades[0]
    assert t["right"] == "PE" and t["reason"] == "time_exit" and (t["entry_bar"], t["exit_bar"]) == (61, 70)
    assert t["gross"] > 0 and t["charges"] > 40 and t["net"] == pytest.approx(t["gross"] - t["charges"], abs=0.01)
    assert afternoon(_day(-4.0, vix=12.0), Simulator(TIGHT)) == []


def test_backtest_premium_stop_caps_a_reversal():
    day = _day(4.0)                                               # rallies into 14:25 -> call
    px = day.bars[61][3]
    for i in range(62, 75):                                       # then falls 30 points a candle
        day.bars[i] = (px, px, px - 30, px - 30)
        px -= 30
    t = afternoon(day, Simulator(TIGHT))[0]
    assert t["right"] == "CE" and t["reason"] == "premium_stop" and t["exit_bar"] < 70
    assert t["sell"] <= t["buy"] * 0.70


def test_metrics_counts_idle_days_and_drawdown():
    days = [_day(0.0), _day(0.0)]
    days[1].day = date(2026, 3, 10)
    trades = [{"date": "2026-03-09", "net": -5000.0, "charges": 70.0, "right": "CE"},
              {"date": "2026-03-10", "net": 2500.0, "charges": 70.0, "right": "PE"}]
    m = metrics(trades, days)
    assert m["trades"] == 2 and m["net"] == -2500 and m["win_rate"] == 50.0 and m["profit_factor"] == 0.5
    assert m["max_dd_pct"] == 10.0 and m["calls"] == -5000 and m["puts"] == 2500
    assert metrics([], days) == {"trades": 0, "sessions": 2}
