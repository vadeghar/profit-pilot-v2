"""Tests for the trend breakout: signals and the simulation on synthetic prices, and the empty-day filter (no network)."""
import numpy as np
import pandas as pd

from investment_strategies.momentum_rotation.data import _trading_days
from investment_strategies.trend_breakout.backtest import FREE, Market, run
from investment_strategies.trend_breakout.strategy import Params, indicators

DAYS = pd.bdate_range("2019-01-01", "2021-12-31")
N = len(DAYS)


def paths():
    """RUN drifts sideways for 400 days, climbs for 150 and then falls; FLAT never makes a new high."""
    wobble = 1 + 0.01 * np.sin(np.arange(N) / 3.0)
    run_ = np.r_[np.full(400, 100.0), 100 * np.cumprod(np.full(150, 1.004)), np.zeros(N - 550)]
    run_[550:] = run_[549] * np.cumprod(np.full(N - 550, 0.995))
    flat = 100 - 0.01 * np.arange(N)
    close = pd.DataFrame({"RUN": run_ * np.r_[wobble[:400], np.ones(N - 400)], "FLAT": flat}, index=DAYS)
    return close, close * 1.005, close * 0.995


def test_setup_is_a_new_high_and_each_exit_fires_after_the_turn():
    close, high, low = paths()
    turnover = close * 1e5
    base = indicators(close, high, low, turnover, Params())
    assert base["setup"]["RUN"].iloc[420:549].all() and not base["setup"]["FLAT"].any()
    assert not base["exit_ok"]["RUN"].iloc[460:549].any()
    first = lambda sig: int(np.argmax(sig["exit_ok"]["RUN"].to_numpy()[550:])) + 550
    low50, low20 = first(base), first(indicators(close, high, low, turnover, Params(exit_days=20)))
    average = first(indicators(close, high, low, turnover, Params(exit="average")))
    chandelier = first(indicators(close, high, low, turnover, Params(exit="chandelier", exit_days=22)))
    assert 550 <= chandelier <= low20 < low50 and 550 <= average < low50
    assert indicators(close, high, low, turnover, Params(rank="turnover"))["score"]["RUN"].iloc[-1] < 0


def synthetic_market(nifty_drift=0.0005):
    close, high, low = paths()
    m = Market.__new__(Market)
    m.close, m.high, m.low = close, high, low
    m.open = close.shift(1).bfill()
    m.turnover = close * 1e5
    m.nifty = pd.Series(10000 * np.cumprod(np.full(N, 1 + nifty_drift)), index=DAYS)
    m._members, m._indicators = {2: pd.DataFrame(True, index=DAYS, columns=close.columns)}, {}
    return m


def test_run_rides_the_breakout_and_sells_after_the_50_day_low_breaks():
    result = run(synthetic_market(), Params(universe_size=2, slots=2), FREE, 100_000, "2020-06-01", "2021-12-31")
    trades = result["trades"]
    assert len(trades) == 1 and trades[0]["symbol"] == "RUN"
    assert trades[0]["buy"] < DAYS[440].date().isoformat() and trades[0]["sell"] > DAYS[550].date().isoformat()
    assert trades[0]["net"] > 0 and trades[0]["days"] > 100


def test_market_filter_blocks_buys_and_market_exit_sells():
    falling = synthetic_market(nifty_drift=-0.001)
    assert run(falling, Params(universe_size=2, slots=2), FREE, 100_000, "2020-06-01", "2021-12-31")["trades"] == []
    turning = synthetic_market()
    turning.nifty.iloc[500:] = turning.nifty.iloc[499] * np.cumprod(np.full(N - 500, 0.99))      # index rolls over while RUN still rises
    held = run(turning, Params(universe_size=2, slots=2), FREE, 100_000, "2020-06-01", "2021-12-31")["trades"]
    sold = run(turning, Params(universe_size=2, slots=2, market_exit=True), FREE, 100_000, "2020-06-01", "2021-12-31")["trades"]
    assert sold[0]["sell"] < held[0]["sell"]


def test_days_with_almost_no_prices_are_dropped():
    days = pd.bdate_range("2012-01-02", periods=5).tolist() + [pd.Timestamp("2012-01-07")]
    rows = [{"d": d, "symbol": s, "c": 100.0} for d in days[:5] for s in "ABCD"] + [{"d": days[5], "symbol": "A", "c": np.nan},
                                                                                     {"d": days[5], "symbol": "B", "c": 1.0}]
    kept = _trading_days(pd.DataFrame(rows))
    assert sorted(kept["d"].unique()) == days[:5]


def test_volume_condition_on_the_breakout_day():
    from investment_strategies.trend_breakout.strategy import relative_volume
    close, high, low = paths()
    turnover = pd.DataFrame(1e7, index=DAYS, columns=close.columns)
    turnover.iloc[430, 0] = 3e7                                                              # one heavy day during the climb
    assert relative_volume(turnover)["RUN"].iloc[430] == 3.0 and relative_volume(turnover)["RUN"].iloc[429] == 1.0
    heavy = indicators(close, high, low, turnover, Params(volume_mult=1.5))["setup"]["RUN"]
    assert heavy.iloc[430] and heavy.iloc[420:549].sum() == 1
    light = indicators(close, high, low, turnover, Params(volume_max=1.5))["setup"]["RUN"]
    assert not light.iloc[430] and light.iloc[420:549].sum() == 128
    weak_close = indicators(close, close * 1.03, close * 0.999, turnover, Params(close_strength=0.67))["setup"]["RUN"]
    assert not weak_close.any()                                                              # every close near the day's low
