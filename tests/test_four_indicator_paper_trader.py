"""Tests for the dedicated Four Indicator System paper-trading runner
(no network - fake Breeze provider, same shape as tests/test_four_indicator_backtest.py).
"""
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from backtest.charges import ChargeConfig
from execution.four_indicator_paper_trader import FourIndicatorPaperTrader
from market_data.normalize import NormalizedCandle
from market_data.trading_days import TradingCalendar
from utils.timezone import IST

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


class FakeBreezeLive:
    """Same shape as tests/test_four_indicator_backtest.py's fake, but also
    tolerant of a request window that runs past the data it knows about
    (a live poll's ``now`` is usually mid-candle)."""

    def __init__(self):
        self.requested: list[tuple] = []

    def get_historical_candles(self, instrument, timeframe, start, end):
        self.requested.append((instrument if isinstance(instrument, str) else instrument["strike_price"], start, end))
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


def cal():
    return TradingCalendar(["2026-01-26"], holiday_file=None)


def make_trader(tmp_path: Path, provider=None, **kw):
    return FourIndicatorPaperTrader(
        provider or FakeBreezeLive(), capital=100_000.0, capital_per_lot=50_000.0,
        state_path=tmp_path / "state.json", calendar=cal(), lookback_days=2, **kw,
    )


def test_poll_before_rally_produces_no_trade(tmp_path):
    trader = make_trader(tmp_path)
    trader.state["started_at"] = datetime.combine(RALLY_DATE, time(9, 15), IST).isoformat()
    status = trader.poll(now=datetime.combine(WARMUP_DATES.__iter__().__next__(), time(10, 0), IST))
    assert status["open_trade"] is None
    assert status["trades"] == []


def test_poll_opens_and_squares_off_a_trade(tmp_path):
    trader = make_trader(tmp_path)
    trader.state["started_at"] = datetime.combine(RALLY_DATE, time(9, 0), IST).isoformat()
    # Poll mid-rally: a trade should be open but not yet closed.
    mid = trader.poll(now=datetime.combine(RALLY_DATE, time(11, 30), IST))
    assert mid["open_trade"] is not None
    assert mid["open_trade"]["side"] == "CE"
    # Poll after intraday square-off cutoff: it should be closed by 15:20.
    end_of_day = trader.poll(now=datetime.combine(RALLY_DATE, time(15, 25), IST))
    assert end_of_day["open_trade"] is None
    assert len(end_of_day["trades"]) == 1
    trade = end_of_day["trades"][0]
    assert trade["status"] == "CLOSED"
    assert trade["exit_reason"] in ("supertrend_flip", "intraday_square_off")
    assert trade["pnl"] == pytest.approx(trade["gross_pnl"] - trade["charges_total"])
    assert end_of_day["balance"] == pytest.approx(100_000.0 + trade["pnl"])


def test_poll_is_idempotent_no_duplicate_trades(tmp_path):
    trader = make_trader(tmp_path)
    trader.state["started_at"] = datetime.combine(RALLY_DATE, time(9, 0), IST).isoformat()
    trader.poll(now=datetime.combine(RALLY_DATE, time(11, 30), IST))
    trader.poll(now=datetime.combine(RALLY_DATE, time(11, 35), IST))
    status = trader.poll(now=datetime.combine(RALLY_DATE, time(15, 25), IST))
    # Re-polling after everything is already closed must not create a second trade.
    status2 = trader.poll(now=datetime.combine(RALLY_DATE, time(15, 26), IST))
    assert len(status2["trades"]) == len(status["trades"]) == 1


def test_lots_scale_with_compounded_balance_not_fixed_initial_capital(tmp_path):
    trader = make_trader(tmp_path)
    # Manually seed a large compounded balance, as if prior sessions had profited.
    trader.state["balance"] = 170_000.0
    trader.state["started_at"] = datetime.combine(RALLY_DATE, time(9, 0), IST).isoformat()
    trader.poll(now=datetime.combine(RALLY_DATE, time(11, 30), IST))
    assert trader.state["open_trade"]["lots"] == 3  # floor(170000 / 50000)


def test_state_persists_and_reloads_from_disk(tmp_path):
    provider = FakeBreezeLive()
    trader1 = make_trader(tmp_path, provider=provider)
    trader1.state["started_at"] = datetime.combine(RALLY_DATE, time(9, 0), IST).isoformat()
    trader1.poll(now=datetime.combine(RALLY_DATE, time(11, 30), IST))
    assert trader1.state["open_trade"] is not None

    # A fresh instance pointed at the same state file should pick up where it left off.
    trader2 = FourIndicatorPaperTrader(provider, capital=100_000.0, capital_per_lot=50_000.0,
                                       state_path=tmp_path / "state.json", calendar=cal(), lookback_days=2)
    assert trader2.state["open_trade"] is not None
    assert trader2.state["open_trade"]["strike"] == trader1.state["open_trade"]["strike"]
    status = trader2.poll(now=datetime.combine(RALLY_DATE, time(15, 25), IST))
    assert status["open_trade"] is None
    assert len(status["trades"]) == 1


def test_never_stacks_a_second_entry_while_one_is_open(tmp_path):
    trader = make_trader(tmp_path)
    trader.state["started_at"] = datetime.combine(RALLY_DATE, time(9, 0), IST).isoformat()
    trader.poll(now=datetime.combine(RALLY_DATE, time(11, 0), IST))
    first_strike = trader.state["open_trade"]["strike"]
    # Still within the same rally (SuperTrend hasn't flipped yet): re-polling
    # must not open a second position on top of the first.
    trader.poll(now=datetime.combine(RALLY_DATE, time(11, 20), IST))
    assert trader.state["open_trade"] is not None
    assert trader.state["open_trade"]["strike"] == first_strike
