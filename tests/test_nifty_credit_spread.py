"""Tests for the NIFTY credit spread: cycle and strike rules, trade management and the money (no network)."""
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from trading_strategies.nifty_credit_spread.backtest import LOT, NO_SLIPPAGE, Market, metrics, trade
from trading_strategies.nifty_credit_spread.strategy import Params, cycles, expiries, manage, strikes, trend_side


def weekdays(start, end):
    return [d.date() for d in pd.bdate_range(start, end)]


def test_expiries_are_thursdays_then_tuesdays_and_move_back_over_holidays():
    days = [d for d in weekdays("2025-08-18", "2025-09-12") if d != date(2025, 8, 28)]       # Thu 28-Aug a holiday
    assert expiries(days) == [date(2025, 8, 21), date(2025, 8, 27), date(2025, 9, 2), date(2025, 9, 9)]
    assert cycles(days) == [(date(2025, 8, 22), date(2025, 8, 27)), (date(2025, 8, 29), date(2025, 9, 2)),
                            (date(2025, 9, 3), date(2025, 9, 9))]


def test_trend_side():
    closes = pd.Series(np.r_[np.full(49, 100.0), 110.0], index=weekdays("2024-01-01", "2024-03-08"))
    day = date(2024, 3, 11)
    assert trend_side(closes, day, Params()) == "PE" and trend_side(closes * 0 + np.r_[np.full(49, 100.0), 90.0], day, Params()) == "CE"
    assert trend_side(closes.iloc[:30], day, Params()) is None and trend_side(closes, day, Params(side="CE")) == "CE"


def test_strikes_sit_one_expected_move_out_and_round_away_from_spot():
    move = 24000 * 0.15 * (5 / 365) ** 0.5                                                    # about 421 points
    assert strikes(24000, 15.0, 5, "PE", Params()) == (23550, 23350) and 24000 - move > 23550
    assert strikes(24000, 15.0, 5, "CE", Params()) == (24450, 24650)
    assert strikes(24000, 15.0, 5, "PE", Params(distance=0.5, width=100)) == (23750, 23650)


def test_manage_takes_the_first_of_target_stop_or_the_last_candle():
    assert manage(np.array([20, 15, 9, 50.0]), 20, Params()) == (2, "target")
    assert manage(np.array([20, 25, 41, 5.0]), 20, Params()) == (2, "stop")
    assert manage(np.array([20, 15, 30, 12.0]), 20, Params()) == (3, "expiry")
    assert manage(np.array([20, 45, 5.0]), 20, Params(stop_multiple=0)) == (2, "target")


class Quotes:
    def __init__(self, series):
        self.series, self.requests = series, 0

    def closes(self, first_day, expiry, strike, right):
        return self.series.get((strike, right), pd.Series(dtype=float))


def fake_market(short_path, long_path, spot=24000.0, vix=15.0, nifty_trend=1.0):
    """Entry Fri 7-Jun-2024 10:00, expiry Thu 13-Jun; option paths given candle by candle from the entry candle."""
    entry, expiry = date(2024, 6, 7), date(2024, 6, 13)
    times = [pd.Timestamp(datetime.combine(entry, datetime.min.time())) + pd.Timedelta(hours=9, minutes=55) + pd.Timedelta(minutes=5 * i)
             for i in range(len(short_path))]
    last = pd.Timestamp(datetime.combine(expiry, datetime.min.time())) + pd.Timedelta(hours=15, minutes=5)
    m = Market.__new__(Market)
    m.close = pd.Series(spot, index=pd.DatetimeIndex(times + [last]))
    m.days = [entry, expiry]
    history = weekdays("2024-01-01", "2024-06-06")
    m.daily = pd.Series(np.linspace(100, 100 * nifty_trend + 100, len(history)), index=history)
    m.vix = pd.Series(vix, index=history)
    short_k, long_k = strikes(spot, vix, 6.2, "PE", Params())
    m.quotes = Quotes({(short_k, "PE"): pd.Series(short_path, index=times), (long_k, "PE"): pd.Series(long_path, index=times)})
    m.cycles = [(entry, expiry)]
    return m, entry, expiry


def test_trade_takes_half_the_credit_at_the_target():
    m, entry, expiry = fake_market([30, 28, 14.0], [10, 9, 4.0])                              # credit 20 -> worth 10
    t = trade(m, entry, expiry, Params(), NO_SLIPPAGE)
    assert t["right"] == "PE" and t["credit"] == 20 and t["reason"] == "target" and t["points"] == pytest.approx(10)
    assert t["gross"] == pytest.approx(10 * LOT) and t["net"] == pytest.approx(t["gross"] - t["charges"]) and t["charges"] > 4 * 20
    assert t["max_loss"] == (200 - 20) * LOT


def test_trade_stops_at_twice_the_credit_and_a_gap_can_cost_more_but_never_the_whole_width():
    m, entry, expiry = fake_market([30, 60, 80.0], [10, 18, 20.0])                            # 42 >= 2 x 20 on the second candle
    t = trade(m, entry, expiry, Params(), NO_SLIPPAGE)
    assert t["reason"] == "stop" and t["points"] == pytest.approx(20 - 42)
    m, entry, expiry = fake_market([30, 400.0], [10, 150.0])                                  # gap through the stop
    t = trade(m, entry, expiry, Params(), NO_SLIPPAGE)
    assert t["reason"] == "stop" and t["exit_value"] == 200 and t["points"] == pytest.approx(20 - 200)   # capped at the width


def test_trade_holds_to_the_expiry_candle_and_skips_what_it_cannot_price():
    m, entry, expiry = fake_market([30, 26, 24.0], [10, 9, 8.0])
    t = trade(m, entry, expiry, Params(), NO_SLIPPAGE)
    assert t["reason"] == "expiry" and t["exit"].startswith("2024-06-13T15:05") and t["days_held"] == 6
    m.quotes = Quotes({})
    assert trade(m, entry, expiry, Params(), NO_SLIPPAGE)["skipped"] == "no option quote at entry"
    m, entry, expiry = fake_market([10, 9.0], [10, 9.0])
    assert trade(m, entry, expiry, Params(), NO_SLIPPAGE)["skipped"] == "credit too small"
    m, entry, expiry = fake_market([30, 14.0], [10, 4.0], vix=11.0)
    assert trade(m, entry, expiry, Params(vix_min=13), NO_SLIPPAGE)["skipped"] == "vix"


def test_metrics_count_skipped_weeks_and_the_drawdown():
    week = lambda i, net=None: {"date": (date(2024, 1, 5) + timedelta(weeks=i)).isoformat(), "expiry": (date(2024, 1, 11) + timedelta(weeks=i)).isoformat(),
                                **({"net": net, "credit": 20, "charges": 100} if net is not None else {"skipped": "vix"})}
    m = metrics([week(0, 500), week(1, -1500), week(2), week(3, -500), week(4, 500)], capital=10_000)
    assert m["weeks"] == 5 and m["trades"] == 4 and m["skipped"] == 1 and m["net"] == -1000
    assert m["max_dd"] == 2000 and m["win_rate"] == 50.0 and m["profit_factor"] == 0.5 and m["losing_streak"] == 2
