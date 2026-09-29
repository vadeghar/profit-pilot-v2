"""Backtest-runner tests for the Four Indicator System, with a fake Breeze
provider (no network, no cache) - mirrors tests/test_nifty_no_brainer_runner.py.
"""
from datetime import date, datetime, time, timedelta

import pytest

from backtest.charges import ChargeConfig
from backtest.four_indicator_backtest import (
    find_strike_for_target_premium, resolve_weekly_expiry, run_four_indicator_backtest,
    weekly_expiry_weekday,
)
from market_data.normalize import NormalizedCandle
from market_data.trading_days import TradingCalendar, TradingDayFetcher
from utils.timezone import IST

HOLIDAYS = ["2026-01-26"]  # Monday


def cal():
    return TradingCalendar(HOLIDAYS, holiday_file=None)


def test_weekly_expiry_weekday_switches_in_sep_2025():
    assert weekly_expiry_weekday(date(2025, 8, 1)) == 3   # Thursday, pre-switch
    assert weekly_expiry_weekday(date(2025, 9, 2)) == 1   # Tuesday, post-switch
    assert weekly_expiry_weekday(date(2026, 1, 7)) == 1


def test_resolve_weekly_expiry_finds_next_tuesday_on_or_after():
    # 2026-01-07 is a Wednesday; next Tuesday is 2026-01-13.
    expiry = resolve_weekly_expiry(date(2026, 1, 7), cal())
    assert expiry == date(2026, 1, 13)


def test_resolve_weekly_expiry_rolls_back_on_holiday():
    # 2026-01-27 is a Tuesday; if it were a holiday the expiry should roll to Monday.
    holiday_cal = TradingCalendar(["2026-01-27"], holiday_file=None)
    expiry = resolve_weekly_expiry(date(2026, 1, 26), holiday_cal)
    assert expiry == date(2026, 1, 26)  # 26th (Monday) is itself the rolled-back day


class _StubFetcher:
    """Just enough of TradingDayFetcher's interface for the strike probe."""

    def __init__(self, premium_fn):
        self.premium_fn = premium_fn
        self.calls = []

    def fetch(self, instrument, timeframe, start, end):
        self.calls.append(instrument)
        strike = int(instrument["strike_price"])
        premium = self.premium_fn(strike)
        if premium is None:
            return []
        return [NormalizedCandle(start, premium, premium, premium, premium, 1, str(instrument), timeframe)]


def test_find_strike_for_target_premium_converges_toward_target():
    # premium decreases linearly as the strike rises; true answer is 24,750.
    def premium_of(strike):
        return max(1.0, 25000.0 - strike)
    fetcher = _StubFetcher(premium_of)
    found = find_strike_for_target_premium(fetcher, date(2026, 1, 13), spot=25000.0,
                                           at=datetime(2026, 1, 7, 10, 0, tzinfo=IST), target_pct=0.01)
    assert found is not None
    strike, premium = found
    assert abs(strike - 24750) <= 150
    assert len(fetcher.calls) <= 8


def test_find_strike_returns_none_when_no_option_data():
    fetcher = _StubFetcher(lambda strike: None)
    found = find_strike_for_target_premium(fetcher, date(2026, 1, 13), spot=25000.0,
                                           at=datetime(2026, 1, 7, 10, 0, tzinfo=IST))
    assert found is None


def test_find_strike_for_target_premium_put_direction_is_mirrored():
    # a put's premium RISES as the strike rises (more ITM); true answer is 25,250.
    def premium_of(strike):
        return max(1.0, strike - 25000.0)
    fetcher = _StubFetcher(premium_of)
    found = find_strike_for_target_premium(fetcher, date(2026, 1, 13), spot=25000.0,
                                           at=datetime(2026, 1, 7, 10, 0, tzinfo=IST),
                                           option_type="PE", target_pct=0.01)
    assert found is not None
    strike, premium = found
    assert abs(strike - 25250) <= 150
    assert len(fetcher.calls) <= 8


# --------------------------------------------------------------------------- full runner
WARMUP_DATES = {date(2026, 1, 5), date(2026, 1, 6)}
RALLY_DATE = date(2026, 1, 7)


def _spot_ohlc(ts: datetime):
    day = ts.date()
    bar_index = int((ts - datetime.combine(day, time(9, 15), IST)).total_seconds() // 300)
    if day in WARMUP_DATES:
        base = 25000.0
        return (base, base + 1, base - 1, base)
    if day == RALLY_DATE:
        if bar_index < 30:
            price = 25000.0 + 25.0 * (bar_index + 1)
            return (price - 25, price + 5, price - 30, price)
        if bar_index < 60:
            k = bar_index - 30
            start_price = 25000.0 + 25.0 * 30
            price = start_price - 60.0 * (k + 1)
            return (price + 60, price + 65, price - 5, price)
        start_price = 25000.0 + 25.0 * 30 - 60.0 * 30
        return (start_price, start_price + 1, start_price - 1, start_price)
    return None


class FakeBreezeFourIndicator:
    """Serves the rally/reversal spot path plus a simple decreasing CE premium
    curve centered so the strike probe resolves at (or near) ATM immediately."""

    name = "breeze"

    def __init__(self):
        self.requested_days: list[date] = []

    def get_historical_candles(self, instrument, timeframe, start, end):
        self.requested_days.append(start.date())
        rows, ts = [], start
        while ts <= end:
            tod = ts.timetz().replace(tzinfo=None)
            if time(9, 15) <= tod <= time(15, 29):
                if isinstance(instrument, dict):
                    ohlc_now = _spot_ohlc(ts)
                    if ohlc_now:
                        spot_close = ohlc_now[3]
                        strike = int(instrument["strike_price"])
                        atm = round(spot_close / 50) * 50
                        premium = max(0.5, spot_close * 0.01 - abs(strike - atm) * 0.02)
                        rows.append(NormalizedCandle(ts, premium, premium, premium, premium, 10,
                                                     str(instrument), timeframe))
                else:
                    ohlc = _spot_ohlc(ts)
                    if ohlc:
                        o, h, l, c = ohlc
                        rows.append(NormalizedCandle(ts, o, h, l, c, 1000, "NSE:NIFTY", timeframe))
            ts += timedelta(minutes=5)
        if not rows:
            raise RuntimeError("No Breeze data returned")
        return rows


def test_runner_produces_a_closed_trade_with_consistent_pnl():
    fake = FakeBreezeFourIndicator()
    report = run_four_indicator_backtest(
        fake, RALLY_DATE, RALLY_DATE, timeframe="5m", capital=100_000.0,
        capital_per_lot=50_000.0, warmup_days=2, calendar=cal(),
        fetcher=TradingDayFetcher(fake, cal(), throttle_seconds=0, retries=0, sleep=lambda s: None),
        charges=ChargeConfig.zero(),
    )
    closed = [t for t in report["trades"] if t["status"] == "CLOSED"]
    assert len(closed) == 1
    trade = closed[0]
    assert trade["entry_date"] == RALLY_DATE.isoformat()
    assert trade["exit_reason"] in ("supertrend_flip", "intraday_square_off")
    assert trade["gross_pnl"] == pytest.approx((trade["exit_premium"] - trade["entry_premium"]) * trade["quantity"])
    assert trade["pnl"] == pytest.approx(trade["gross_pnl"] - trade["charges_total"])
    assert report["summary"]["total_trades"] == 1
    assert report["summary"]["final_balance"] == pytest.approx(100_000.0 + trade["pnl"])
    assert all(d.weekday() < 5 for d in fake.requested_days)


SELLOFF_DATE = date(2026, 1, 7)


def _spot_ohlc_selloff(ts: datetime):
    day = ts.date()
    bar_index = int((ts - datetime.combine(day, time(9, 15), IST)).total_seconds() // 300)
    if day in WARMUP_DATES:
        base = 25000.0
        return (base, base + 1, base - 1, base)
    if day == SELLOFF_DATE:
        if bar_index < 30:
            price = 25000.0 - 25.0 * (bar_index + 1)
            return (price + 25, price + 30, price - 5, price)
        if bar_index < 60:
            k = bar_index - 30
            start_price = 25000.0 - 25.0 * 30
            price = start_price + 60.0 * (k + 1)
            return (price - 60, price + 5, price - 65, price)
        start_price = 25000.0 - 25.0 * 30 + 60.0 * 30
        return (start_price, start_price + 1, start_price - 1, start_price)
    return None


class FakeBreezeFourIndicatorPut:
    """Same shape as FakeBreezeFourIndicator, but a selloff spot path plus a
    PE premium curve centered so the strike probe resolves at/near ATM."""

    name = "breeze"

    def __init__(self):
        self.requested_days: list[date] = []

    def get_historical_candles(self, instrument, timeframe, start, end):
        self.requested_days.append(start.date())
        rows, ts = [], start
        while ts <= end:
            tod = ts.timetz().replace(tzinfo=None)
            if time(9, 15) <= tod <= time(15, 29):
                if isinstance(instrument, dict):
                    ohlc_now = _spot_ohlc_selloff(ts)
                    if ohlc_now:
                        spot_close = ohlc_now[3]
                        strike = int(instrument["strike_price"])
                        atm = round(spot_close / 50) * 50
                        premium = max(0.5, spot_close * 0.01 - abs(strike - atm) * 0.02)
                        rows.append(NormalizedCandle(ts, premium, premium, premium, premium, 10,
                                                     str(instrument), timeframe))
                else:
                    ohlc = _spot_ohlc_selloff(ts)
                    if ohlc:
                        o, h, l, c = ohlc
                        rows.append(NormalizedCandle(ts, o, h, l, c, 1000, "NSE:NIFTY", timeframe))
            ts += timedelta(minutes=5)
        if not rows:
            raise RuntimeError("No Breeze data returned")
        return rows


def test_runner_produces_a_closed_put_trade():
    fake = FakeBreezeFourIndicatorPut()
    report = run_four_indicator_backtest(
        fake, SELLOFF_DATE, SELLOFF_DATE, timeframe="5m", capital=100_000.0,
        capital_per_lot=50_000.0, warmup_days=2, calendar=cal(),
        fetcher=TradingDayFetcher(fake, cal(), throttle_seconds=0, retries=0, sleep=lambda s: None),
        charges=ChargeConfig.zero(),
    )
    closed = [t for t in report["trades"] if t["status"] == "CLOSED"]
    assert len(closed) == 1
    trade = closed[0]
    assert trade["side"] == "PE"
    assert trade["entry_date"] == SELLOFF_DATE.isoformat()
    assert trade["exit_reason"] in ("supertrend_flip", "intraday_square_off")
    assert trade["pnl"] == pytest.approx(trade["gross_pnl"] - trade["charges_total"])


def test_runner_charges_reduce_net_pnl():
    fake_gross = FakeBreezeFourIndicator()
    gross = run_four_indicator_backtest(
        fake_gross, RALLY_DATE, RALLY_DATE, warmup_days=2, calendar=cal(),
        fetcher=TradingDayFetcher(fake_gross, cal(), throttle_seconds=0, retries=0, sleep=lambda s: None),
        charges=ChargeConfig.zero())
    fake_net = FakeBreezeFourIndicator()
    net = run_four_indicator_backtest(
        fake_net, RALLY_DATE, RALLY_DATE, warmup_days=2, calendar=cal(),
        fetcher=TradingDayFetcher(fake_net, cal(), throttle_seconds=0, retries=0, sleep=lambda s: None),
        charges=ChargeConfig())
    g = gross["trades"][0]
    n = net["trades"][0]
    assert n["charges_total"] > 0
    assert n["pnl"] == pytest.approx(n["gross_pnl"] - n["charges_total"])
    assert n["gross_pnl"] == pytest.approx(g["gross_pnl"])
