"""Tests for the stock pullback strategy: indicators and the money simulation, on synthetic prices (no network)."""
import numpy as np
import pandas as pd
import pytest

from investment_strategies.momentum_rotation.backtest import Costs
from trading_strategies.stock_pullback.backtest import FREE, Market, run, summary
from trading_strategies.stock_pullback.strategy import Params, indicators, market_ok, rsi

DAYS = pd.bdate_range("2019-01-01", "2021-12-31")


def test_rsi2_is_wilder_smoothed():
    close = pd.DataFrame({"A": [10.0, 11.0, 12.0, 11.0, 10.0, 12.0]})
    out = rsi(close, 2)["A"]
    assert np.isnan(out.iloc[1]) and out.iloc[2] == 100.0                                    # two gains, no loss
    assert out.iloc[3] == pytest.approx(100 - 100 / (1 + 0.5 / 0.5))                          # avg gain 0.5, avg loss 0.5
    assert out.iloc[4] == pytest.approx(100 - 100 / (1 + 0.25 / 0.75)) and out.iloc[5] > 50


def frames(dip_at=500, drift=0.001, n=len(DAYS)):
    """DIP rises steadily, falls two days at ``dip_at`` and recovers; OTHER just rises."""
    path = 100 * np.cumprod(np.full(n, 1 + drift))
    dip = path.copy()
    dip[dip_at] *= 0.97
    dip[dip_at + 1] *= 0.94
    dip[dip_at + 2:] *= 0.99
    close = pd.DataFrame({"DIP": dip, "OTHER": path}, index=DAYS[:n])
    return close, close * 1.01, close * 0.99


def test_setup_needs_an_uptrend_and_an_oversold_reading():
    close, high, low = frames()
    out = indicators(close, high, low, Params())
    assert out["setup"]["DIP"].iloc[501] and not out["setup"]["DIP"].iloc[499] and not out["setup"]["OTHER"].any()
    assert not indicators(close.iloc[:150].assign(DIP=frames(100, n=150)[0]["DIP"]), high.iloc[:150], low.iloc[:150], Params())["setup"]["DIP"].any()
    assert indicators(close, high, low, Params(trend_ma=0))["setup"]["DIP"].iloc[501]
    ibs = indicators(close, close * 1.03, close * 0.999, Params(signal="ibs", threshold=0.2))
    assert ibs["setup"]["DIP"].iloc[300] and ibs["score"]["DIP"].iloc[300] < 0.2             # closing near the low every day
    falling = pd.Series(np.linspace(100, 60, 300), index=DAYS[:300])
    assert not market_ok(falling, Params()).iloc[-1] and market_ok(falling, Params(market_ma=0)).all()


def synthetic_market(nifty_drift=0.0005):
    close, high, low = frames()
    m = Market.__new__(Market)
    m.close, m.high, m.low = close, high, low
    m.open = close.shift(1).bfill()
    m.turnover = close * 1e5
    m.nifty = pd.Series(10000 * np.cumprod(np.full(len(DAYS), 1 + nifty_drift)), index=DAYS)
    m._members, m._indicators = {2: pd.DataFrame(True, index=DAYS, columns=close.columns)}, {}
    return m


def test_run_buys_the_dip_at_the_next_open_and_sells_the_bounce():
    m = synthetic_market()
    result = run(m, Params(universe_size=2, slots=2), FREE, 100_000, "2020-06-01", "2021-06-30")
    assert len(result["trades"]) == 1
    t = result["trades"][0]
    assert t["symbol"] == "DIP" and t["buy"] == DAYS[501].date().isoformat()                 # oversold on day 500, bought next open
    assert 1 <= t["days"] <= 10 and t["net"] > 0 and summary(result)["win_rate"] == 100.0
    costly = run(m, Params(universe_size=2, slots=2), Costs(), 100_000, "2020-06-01", "2021-06-30")
    assert costly["fees"] > 0 and costly["trades"][0]["net"] < t["net"]


def test_market_filter_and_minimum_order():
    assert run(synthetic_market(nifty_drift=-0.001), Params(universe_size=2, slots=2), FREE, 100_000, "2020-06-01", "2021-06-30")["trades"] == []
    assert run(synthetic_market(), Params(universe_size=2, slots=2), Costs(), 1_500, "2020-06-01", "2021-06-30")["trades"] == []   # slot under Rs 1,000
