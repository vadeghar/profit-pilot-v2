"""Tests for the momentum rotation: the rules and the money simulation, on synthetic prices (no network)."""
import numpy as np
import pandas as pd
import pytest

from investment_strategies.momentum_rotation.backtest import Costs, Market, run, stats
from investment_strategies.momentum_rotation.strategy import Params, month_ends, risk_on, scores, select, universe

DAYS = pd.bdate_range("2019-01-01", "2021-12-31")


def market(drifts, nifty_drift=0.0005, volume=None):
    """Smooth price paths with the given daily drifts; everything opens where it closed the day before."""
    n = len(DAYS)
    wobble = 1 + 0.002 * np.sin(np.arange(n))
    close = pd.DataFrame({s: 100 * np.cumprod(np.full(n, 1 + d)) * wobble for s, d in drifts.items()}, index=DAYS)
    turnover = close * pd.DataFrame({s: (volume or {}).get(s, 1e5) for s in drifts}, index=DAYS)
    bench = pd.DataFrame({"NIFTY": 10000 * np.cumprod(np.full(n, 1 + nifty_drift))}, index=DAYS)
    return Market(close, close.shift(1).bfill(), turnover, bench)


def test_month_ends_are_last_trading_days_and_skip_the_open_month():
    ends = month_ends(DAYS[:45])
    assert [d.strftime("%Y-%m-%d") for d in ends] == ["2019-01-31", "2019-02-28"]
    assert len(month_ends(DAYS, every=6)) == 6


def test_universe_takes_the_most_traded_and_needs_history():
    m = market({"A": 0.001, "B": 0.001, "C": 0.001}, volume={"A": 3e5, "B": 2e5, "C": 1e5})
    at = DAYS[400]
    assert universe(m.turnover, m.close, at, Params(universe_size=2)) == ["A", "B"]
    late = m.close.copy()
    late.loc[:DAYS[300], "A"] = np.nan                                         # listed 100 days ago: not enough history
    assert "A" not in universe(m.turnover, late, at, Params(universe_size=3))


def test_scores_rank_the_strongest_first_in_every_method():
    m = market({"UP": 0.002, "FLAT": 0.0, "DOWN": -0.001})
    for method in ("index", "12-1", "6m"):
        assert scores(m.close, DAYS[400], ["UP", "FLAT", "DOWN"], Params(score=method)).index.tolist() == ["UP", "FLAT", "DOWN"]
    assert scores(m.close, DAYS[100], ["UP", "FLAT"], Params()).empty             # less than 13 months of prices


def test_select_keeps_holdings_inside_the_buffer_and_respects_the_regime():
    ranked = pd.Series([9, 8, 7, 6, 5, 4.0], index=list("ABCDEF"))
    p = Params(top_n=2, hold_buffer=2.0)
    assert select(ranked, [], p, True) == ["A", "B"]
    assert select(ranked, ["D"], p, True) == ["D", "A"]                          # D is 4th: inside top 2 x 2, kept
    assert select(ranked, ["E"], p, True) == ["A", "B"]                          # E is 5th: dropped
    assert select(ranked, ["D"], p, True, affordable=lambda s: s != "A") == ["D", "B"]
    assert select(ranked, ["A"], p, False) == []
    assert select(ranked, ["A", "F"], Params(top_n=2, regime="no_new"), False) == ["A"]
    assert select(ranked, [], Params(top_n=2, regime="none"), False) == ["A", "B"]


def test_regime_reads_the_index_against_its_average():
    falling = pd.Series(np.linspace(100, 60, 300), index=DAYS[:300])
    assert not risk_on(falling, DAYS[299], Params()) and risk_on(falling, DAYS[299], Params(regime="none"))
    assert risk_on(falling[::-1].set_axis(DAYS[:300]), DAYS[299], Params())


def test_delivery_charges():
    c = Costs()
    assert c.fees(10_000, buy=True) == pytest.approx((20 + 0.307) * 1.18 + 10 + 1.5)
    assert c.fees(10_000, buy=False) == pytest.approx((20 + 0.307) * 1.18 + 10 + 18.5)
    assert Costs(brokerage_pct=0.005).fees(10_000, buy=True) == pytest.approx((50 + 0.307) * 1.18 + 10 + 1.5)


def test_run_buys_the_winners_in_whole_shares_at_the_next_open_and_pays_fees():
    m = market({"UP1": 0.002, "UP2": 0.0015, "FLAT": 0.0, "DOWN": -0.001})
    result = run(m, Params(top_n=2, universe_size=4), capital=50_000, start="2020-06-01", end="2021-12-31")
    first = result["picks"][0]
    assert first["date"] == "2020-06-30" and first["hold"] == ["UP1", "UP2"]
    assert set(result["holdings"]) == {"UP1", "UP2"} and all(float(q).is_integer() for q in result["holdings"].values())
    equity = result["equity"]
    assert equity.loc["2020-06-30"] == 50_000 and equity.loc["2020-07-01"] < 50_000    # nothing held before the trade day; fees paid on it
    assert result["fees"] > 0 and equity.iloc[-1] > 50_000 and result["trades"] == []
    assert {t["symbol"] for t in result["open_trades"]} == {"UP1", "UP2"}


def test_run_goes_to_cash_when_the_index_is_below_its_average():
    m = market({"UP1": 0.002, "UP2": 0.0015, "FLAT": 0.0}, nifty_drift=-0.001)
    result = run(m, Params(top_n=2, universe_size=3), capital=50_000, start="2020-06-01", end="2021-06-30")
    assert all(not p["risk_on"] and p["hold"] == [] for p in result["picks"])
    assert (result["equity"] == 50_000).all() and result["fees"] == 0
    assert run(m, Params(top_n=2, universe_size=3, regime="none"), capital=50_000, start="2020-06-01", end="2021-06-30")["holdings"]


def test_a_share_dearer_than_the_slot_is_skipped():
    m = market({"UP1": 0.002, "UP2": 0.0015, "FLAT": 0.0})
    m.close["UP1"] *= 400                                                       # about Rs 80,000 a share
    m.open["UP1"] *= 400
    result = run(m, Params(top_n=2, universe_size=3), capital=50_000, start="2020-06-01", end="2020-12-31")
    assert "UP1" not in result["holdings"] and "UP2" in result["holdings"]


def test_stats():
    equity = pd.Series([100.0, 120.0, 90.0, 144.0], index=pd.to_datetime(["2020-01-01", "2020-12-31", "2021-06-30", "2021-12-31"]))
    s = stats(equity)
    assert s["cagr"] == pytest.approx(20.0, abs=0.1) and s["max_dd"] == 25.0 and s["yearly"] == {2020: 20.0, 2021: 20.0}
