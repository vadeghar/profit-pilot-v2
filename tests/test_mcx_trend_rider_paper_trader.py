"""Tests for the MCX Trend Rider paper-trading runner (no network).

Two layers, matching the module's own design split:
  1. The trader's own bookkeeping (entry/exit disambiguation via reason +
     existing-position lookup, point_value PnL scaling, compounding
     balance, persistence, idempotency) is tested against an injected FAKE
     strategy so it doesn't depend on the real Donchian/ADX math triggering.
  2. One end-to-end test drives the REAL MCXTrendRiderStrategy through a
     hand-verified synthetic price series (uptrend -> breakout -> stop-out)
     to prove the wiring works against the actual strategy.
"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.models import Candle, OrderSide, OrderType, Signal
from execution.mcx_trend_rider_paper_trader import MCXTrendRiderPaperTrader
from strategies.mcx_trend_rider import MCXTrendRiderStrategy
from utils.timezone import IST

INSTRUMENT = "MCX_CRUDEOIL"


class FakeMCXProvider:
    def __init__(self, series: dict):
        self.series = series
        self.calls = []

    def get_historical_candles(self, instrument, timeframe, start, end):
        self.calls.append(instrument)
        return list(self.series.get(instrument, []))


def flat_series(n=80, start=datetime(2024, 1, 1, tzinfo=IST), price=1000.0):
    out, ts = [], start
    for _ in range(n):
        out.append(Candle(timestamp=ts, open=price, high=price + 1, low=price - 1,
                          close=price, volume=10000, instrument=INSTRUMENT))
        ts += timedelta(days=1)
    return out


class ScriptedStrategy:
    """Scripted long entry then a stop-loss exit, mirroring the real
    strategy's Signal shape (price carried in metadata, not sig.price)."""

    _entry_date = datetime(2024, 3, 1, tzinfo=IST).date()
    _exit_date = datetime(2024, 3, 5, tzinfo=IST).date()

    def __init__(self, params):
        self.params = params
        self.capital = params.get("capital")
        self._fired_entry = False
        self._fired_exit = False

    def initialize(self):
        pass

    def on_candle(self, candle):
        d = candle.timestamp.date()
        if not self._fired_entry and d == self._entry_date:
            self._fired_entry = True
            return Signal(strategy_id="t", instrument=INSTRUMENT, action=OrderSide.BUY,
                         quantity=3, order_type=OrderType.MARKET,
                         metadata={"reason": "long_breakout_20d", "entry_price": 1000.0, "stop_loss": 950.0})
        if self._fired_entry and not self._fired_exit and d == self._exit_date:
            self._fired_exit = True
            return Signal(strategy_id="t", instrument=INSTRUMENT, action=OrderSide.SELL,
                         quantity=3, order_type=OrderType.MARKET,
                         metadata={"reason": "long_stop_hit", "exit_price": 950.0})
        return None


def make_trader(tmp_path: Path, provider, **kw):
    return MCXTrendRiderPaperTrader(
        provider, instruments=[INSTRUMENT], capital=100_000.0,
        state_path=tmp_path / "state.json",
        strategy_factory=lambda params: ScriptedStrategy(params), **kw,
    )


def test_poll_applies_scripted_entry_and_exit(tmp_path):
    provider = FakeMCXProvider({INSTRUMENT: flat_series()})
    trader = make_trader(tmp_path, provider)
    status = trader.poll(now=datetime(2024, 3, 10, tzinfo=IST))
    assert status["positions"] == {}
    assert len(status["trades"]) == 1
    trade = status["trades"][0]
    assert trade["instrument"] == INSTRUMENT and trade["side"] == "LONG" and trade["lots"] == 3
    # MCX_CRUDEOIL point_value = 100 (see strategies/mcx_trend_rider.py COMMODITY_SPECS)
    assert trade["pnl"] == pytest.approx((950.0 - 1000.0) * 3 * 100)
    assert status["balance"] == pytest.approx(100_000.0 + trade["pnl"])


def test_poll_mid_trade_shows_open_position(tmp_path):
    provider = FakeMCXProvider({INSTRUMENT: flat_series()})
    trader = make_trader(tmp_path, provider)
    status = trader.poll(now=datetime(2024, 3, 3, tzinfo=IST))
    assert INSTRUMENT in status["positions"]
    assert status["positions"][INSTRUMENT]["side"] == "LONG"
    assert status["trades"] == []


def test_poll_is_idempotent_no_duplicate_trades(tmp_path):
    provider = FakeMCXProvider({INSTRUMENT: flat_series()})
    trader = make_trader(tmp_path, provider)
    trader.poll(now=datetime(2024, 3, 10, tzinfo=IST))
    status2 = trader.poll(now=datetime(2024, 3, 11, tzinfo=IST))
    assert len(status2["trades"]) == 1


def test_lots_scale_with_compounded_balance(tmp_path):
    provider = FakeMCXProvider({INSTRUMENT: flat_series()})
    trader = make_trader(tmp_path, provider)
    trader.state["balance"] = 300_000.0
    captured = {}
    original_factory = trader.strategy_factory
    def spy_factory(params):
        captured["capital"] = params.get("capital")
        return original_factory(params)
    trader.strategy_factory = spy_factory
    trader.poll(now=datetime(2024, 3, 3, tzinfo=IST))
    assert captured["capital"] == 300_000.0


def test_state_persists_and_reloads_from_disk(tmp_path):
    provider = FakeMCXProvider({INSTRUMENT: flat_series()})
    trader1 = make_trader(tmp_path, provider)
    trader1.poll(now=datetime(2024, 3, 3, tzinfo=IST))
    assert INSTRUMENT in trader1.state["positions"]

    trader2 = MCXTrendRiderPaperTrader(
        provider, instruments=[INSTRUMENT], capital=100_000.0, state_path=tmp_path / "state.json",
        strategy_factory=lambda params: ScriptedStrategy(params))
    assert INSTRUMENT in trader2.state["positions"]
    status = trader2.poll(now=datetime(2024, 3, 10, tzinfo=IST))
    assert INSTRUMENT not in status["positions"]
    assert len(status["trades"]) == 1


# --------------------------------------------------------------------------- real strategy end-to-end
def _real_mcx_series():
    """Hand-verified: uptrend (230d, builds ADX/SMA200) -> a 15d base ->
    20-day Donchian breakout -> decline triggering the initial stop."""
    candles = []
    price = 1000.0
    ts = datetime(2024, 1, 1, tzinfo=IST)
    for _ in range(230):
        price *= 1.0025
        candles.append((ts, price * 0.995, price * 1.008, price * 0.99, price, 10000))
        ts += timedelta(days=1)
    for _ in range(15):
        price *= 1.0002
        candles.append((ts, price * 0.998, price * 1.005, price * 0.995, price, 8000))
        ts += timedelta(days=1)
    price *= 1.03
    candles.append((ts, price * 0.99, price * 1.02, price * 0.985, price, 15000))
    ts += timedelta(days=1)
    for _ in range(15):
        price *= 0.97
        candles.append((ts, price * 1.01, price * 1.02, price * 0.985, price, 12000))
        ts += timedelta(days=1)
    return [Candle(timestamp=t, open=o, high=h, low=l, close=c, volume=v, instrument=INSTRUMENT)
           for (t, o, h, l, c, v) in candles]


def test_real_strategy_end_to_end_entry_and_exit(tmp_path):
    rows = _real_mcx_series()
    provider = FakeMCXProvider({INSTRUMENT: rows})
    trader = MCXTrendRiderPaperTrader(
        provider, instruments=[INSTRUMENT], capital=100_000.0, state_path=tmp_path / "state.json",
        strategy_factory=lambda params: MCXTrendRiderStrategy("mcx_trend_rider_paper", "t", params))
    status = trader.poll(now=rows[-1].timestamp + timedelta(days=1))
    assert len(status["trades"]) >= 1
    first_trade = status["trades"][0]
    assert first_trade["instrument"] == INSTRUMENT
    assert first_trade["reason"].startswith("long_stop_hit")
    assert first_trade["pnl"] < 0  # the stop-out is a loss by construction
