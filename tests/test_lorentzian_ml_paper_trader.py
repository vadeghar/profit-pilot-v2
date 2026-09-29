"""Tests for the Lorentzian ML paper-trading runner (no network), against an
injected fake strategy - the real KNN pipeline isn't practical to trigger
deterministically in a unit test, so this focuses entirely on the trader's
own bookkeeping: entry/exit application (price straight from signal.price,
unlike MCX), the reversal-without-explicit-exit case, compounding-free
balance tracking, persistence, and idempotency.
"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.models import Candle, OrderSide, OrderType, Signal
from execution.lorentzian_ml_paper_trader import LorentzianMLPaperTrader
from utils.timezone import IST

TICKER = "NSE:TESTSTOCK"


class FakeProvider:
    def __init__(self, series: dict):
        self.series = series
        self.calls = []

    def get_historical_candles(self, instrument, timeframe, start, end):
        self.calls.append(instrument)
        return list(self.series.get(instrument, []))


def flat_series(n=40, start=datetime(2024, 1, 1, tzinfo=IST), price=100.0):
    out, ts = [], start
    for _ in range(n):
        out.append(Candle(timestamp=ts, open=price, high=price + 1, low=price - 1,
                          close=price, volume=1000, instrument=TICKER))
        ts += timedelta(days=1)
    return out


class ScriptedStrategy:
    """Emits: long entry (day1) -> short reversal with NO explicit exit
    signal (day2, mirroring the real strategy's flip behavior) -> short
    exit (day3)."""

    _d1 = datetime(2024, 1, 5, tzinfo=IST).date()
    _d2 = datetime(2024, 1, 10, tzinfo=IST).date()
    _d3 = datetime(2024, 1, 15, tzinfo=IST).date()

    def __init__(self, params):
        self.params = params
        self._fired = set()

    def on_candle(self, candle):
        d = candle.timestamp.date()
        if d == self._d1 and "d1" not in self._fired:
            self._fired.add("d1")
            return Signal(strategy_id="t", instrument=TICKER, action=OrderSide.BUY, quantity=5,
                         order_type=OrderType.MARKET, price=100.0,
                         metadata={"reason": "lorentzian_new_long"})
        if d == self._d2 and "d2" not in self._fired:
            self._fired.add("d2")
            return Signal(strategy_id="t", instrument=TICKER, action=OrderSide.SELL, quantity=5,
                         order_type=OrderType.MARKET, price=110.0,
                         metadata={"reason": "lorentzian_new_short"})
        if d == self._d3 and "d3" not in self._fired:
            self._fired.add("d3")
            return Signal(strategy_id="t", instrument=TICKER, action=OrderSide.BUY, quantity=5,
                         order_type=OrderType.MARKET, price=95.0,
                         metadata={"reason": "lorentzian_exit_short"})
        return None


def make_trader(tmp_path: Path, provider, **kw):
    return LorentzianMLPaperTrader(
        provider, tickers=[TICKER], capital=100_000.0,
        state_path=tmp_path / "state.json",
        strategy_factory=lambda params: ScriptedStrategy(params), **kw,
    )


def test_poll_applies_entry_reversal_and_exit(tmp_path):
    provider = FakeProvider({TICKER: flat_series(n=25, start=datetime(2024, 1, 1, tzinfo=IST))})
    trader = make_trader(tmp_path, provider)
    status = trader.poll(now=datetime(2024, 1, 20, tzinfo=IST))
    assert status["positions"] == {}
    assert len(status["trades"]) == 2  # the reversal-close + the final exit
    reversal, final = status["trades"]
    assert reversal["side"] == "LONG" and reversal["reason"] == "reversed"
    assert reversal["pnl"] == pytest.approx((110.0 - 100.0) * 5)
    assert final["side"] == "SHORT" and final["reason"] == "lorentzian_exit_short"
    assert final["pnl"] == pytest.approx((110.0 - 95.0) * 5)  # short: entry - exit profit
    assert status["balance"] == pytest.approx(100_000.0 + reversal["pnl"] + final["pnl"])


def test_poll_mid_trade_shows_open_long_position(tmp_path):
    provider = FakeProvider({TICKER: flat_series(n=25, start=datetime(2024, 1, 1, tzinfo=IST))})
    trader = make_trader(tmp_path, provider)
    status = trader.poll(now=datetime(2024, 1, 8, tzinfo=IST))
    assert TICKER in status["positions"]
    assert status["positions"][TICKER]["side"] == "LONG"
    assert status["trades"] == []


def test_poll_is_idempotent_no_duplicate_trades(tmp_path):
    provider = FakeProvider({TICKER: flat_series(n=25, start=datetime(2024, 1, 1, tzinfo=IST))})
    trader = make_trader(tmp_path, provider)
    trader.poll(now=datetime(2024, 1, 20, tzinfo=IST))
    status2 = trader.poll(now=datetime(2024, 1, 21, tzinfo=IST))
    assert len(status2["trades"]) == 2


def test_state_persists_and_reloads_from_disk(tmp_path):
    provider = FakeProvider({TICKER: flat_series(n=25, start=datetime(2024, 1, 1, tzinfo=IST))})
    trader1 = make_trader(tmp_path, provider)
    trader1.poll(now=datetime(2024, 1, 8, tzinfo=IST))
    assert TICKER in trader1.state["positions"]

    trader2 = LorentzianMLPaperTrader(
        provider, tickers=[TICKER], capital=100_000.0, state_path=tmp_path / "state.json",
        strategy_factory=lambda params: ScriptedStrategy(params))
    assert TICKER in trader2.state["positions"]
    status = trader2.poll(now=datetime(2024, 1, 20, tzinfo=IST))
    assert TICKER not in status["positions"]
    assert len(status["trades"]) == 2


def test_sizing_is_fixed_quantity_not_capital_based(tmp_path):
    """This strategy has no capital-sizing concept: balance changes never
    feed back into position size (unlike the other three retrofits)."""
    provider = FakeProvider({TICKER: flat_series(n=25, start=datetime(2024, 1, 1, tzinfo=IST))})
    trader = make_trader(tmp_path, provider)
    trader.state["balance"] = 500_000.0  # much larger than initial capital
    status = trader.poll(now=datetime(2024, 1, 8, tzinfo=IST))
    assert status["positions"][TICKER]["quantity"] == 5  # unchanged: from the scripted signal, not balance
