"""Tests for the volume shock strategy: the shock measure, the ranking and one simulated month (no network)."""
import numpy as np
import pandas as pd

from investment_strategies.momentum_rotation.backtest import Costs, Market
from investment_strategies.volume_shock.backtest import run
from investment_strategies.volume_shock.strategy import Params, ranked, shock_ratio

DAYS = pd.bdate_range("2019-01-01", "2021-12-31")
N = len(DAYS)


def frames():
    close = pd.DataFrame({"UP": 100 * np.cumprod(np.full(N, 1.001)), "DOWN": 100 * np.cumprod(np.full(N, 0.999)),
                          "CALM": np.full(N, 100.0)}, index=DAYS)
    turnover = pd.DataFrame(1e7, index=DAYS, columns=close.columns)
    return close, turnover


def test_shock_is_recent_turnover_over_the_median_before_it():
    close, turnover = frames()
    turnover.iloc[295:300, 0] = 4e7
    ratio = shock_ratio(turnover)
    assert ratio["UP"].iloc[299] == 4.0 and ratio["CALM"].iloc[299] == 1.0 and np.isnan(ratio["UP"].iloc[40])
    assert ratio["UP"].iloc[310] == 1.0                                       # five heavy days do not move a 50-day median


def test_ranked_takes_shocks_above_the_threshold_and_filters_by_direction():
    close, turnover = frames()
    turnover.iloc[295:300, 0] = 4e7
    turnover.iloc[295:300, 1] = 3e7
    at, names = DAYS[299], ["UP", "DOWN", "CALM"]
    assert ranked(turnover, close, at, names, Params()).round(1).to_dict() == {"UP": 4.0, "DOWN": 3.0}
    assert ranked(turnover, close, at, names, Params(min_shock=3.5)).index.tolist() == ["UP"]
    assert ranked(turnover, close, at, names, Params(direction="down")).index.tolist() == ["DOWN"]
    assert ranked(turnover, close, at, names, Params(direction="up")).index.tolist() == ["UP"]
    assert ranked(turnover, close, DAYS[30], names, Params()).empty
    assert Params(top_n=4, rebalance_months=3).rotation().hold_buffer == 1.0 and Params(top_n=4).rotation().top_n == 4


def test_run_buys_the_shocked_stock_and_drops_it_the_next_month():
    close, turnover = frames()
    month_end = DAYS[(DAYS.year == 2020) & (DAYS.month == 6)][-1]
    i = DAYS.get_loc(month_end)
    turnover.iloc[i - 4:i + 1, 0] = 4e7
    bench = pd.DataFrame({"NIFTY": 10000 * np.cumprod(np.full(N, 1.0005))}, index=DAYS)
    market = Market(close, close.shift(1).bfill(), turnover, bench)
    result = run(market, Params(universe_size=3, top_n=2), Costs(), 50_000, "2020-06-01", "2020-12-31")
    june = next(p for p in result["picks"] if p["date"] == month_end.date().isoformat())
    assert june["hold"] == ["UP"] and len(result["trades"]) == 1
    assert result["trades"][0]["symbol"] == "UP" and result["trades"][0]["sell"].startswith("2020-08-03")
    assert all(p["hold"] == [] for p in result["picks"] if p["date"] > month_end.date().isoformat())
