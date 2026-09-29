"""Tests for the Equity Swing VCP paper-trading runner (no network).

Two layers, matching the module's own design split:
  1. The trader's own bookkeeping (entry/exit application, compounding
     balance, persistence, idempotency) is tested against an injected FAKE
     strategy so it doesn't depend on the real VCP math triggering.
  2. One end-to-end test drives the REAL EquitySwingVCPStrategy through a
     hand-verified synthetic price series (uptrend -> contraction ->
     volume breakout -> pullback) to prove the wiring (benchmark feed,
     candle routing, replay-from-scratch cutoff) works against the actual
     strategy, not just the fake.
"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.models import Candle, OrderSide, OrderType, Signal
from execution.equity_swing_vcp_paper_trader import EquitySwingVCPPaperTrader
from strategies.equity_swing_vcp import EquitySwingVCPStrategy
from utils.timezone import IST

SYMBOL = "NSE:TESTSTOCK"
BENCH = "NSE:NIFTY"


class FakeEquityProvider:
    """Serves a fixed candle list per instrument, ignoring the requested window
    (the trader always asks for [now - lookback, now], which comfortably
    covers everything the tests seed)."""

    def __init__(self, series: dict):
        self.series = series  # {instrument: [Candle, ...]}
        self.calls = []

    def get_historical_candles(self, instrument, timeframe, start, end):
        self.calls.append(instrument)
        return list(self.series.get(instrument, []))


def bench_candles(n=60, start=datetime(2024, 1, 1, tzinfo=IST)):
    out, price, ts = [], 20000.0, start
    for _ in range(n):
        price *= 1.0005
        out.append(Candle(timestamp=ts, open=price * 0.999, high=price * 1.002,
                          low=price * 0.998, close=price, volume=1_000_000, instrument=BENCH))
        ts += timedelta(days=1)
    return out


class ScriptedStrategy:
    """A minimal StrategyBase-shaped fake: emits a scripted BUY then a
    scripted SELL on specific candle dates, regardless of price content."""

    def __init__(self, params):
        self.params = params
        self.capital = params.get("capital")
        self._fired_entry = False
        self._fired_exit = False

    def initialize(self):
        pass

    def on_candle(self, candle):
        if candle.instrument == BENCH:
            return None
        d = candle.timestamp.date()
        if not self._fired_entry and d == self._entry_date:
            self._fired_entry = True
            return Signal(strategy_id="t", instrument=SYMBOL, action=OrderSide.BUY,
                         quantity=10, order_type=OrderType.MARKET, price=100.0, stop_loss=90.0)
        if self._fired_entry and not self._fired_exit and d == self._exit_date:
            self._fired_exit = True
            return Signal(strategy_id="t", instrument=SYMBOL, action=OrderSide.SELL,
                         quantity=10, order_type=OrderType.MARKET, price=120.0,
                         metadata={"reason": "trailing_exit"})
        return None

    _entry_date = datetime(2024, 3, 1, tzinfo=IST).date()
    _exit_date = datetime(2024, 3, 5, tzinfo=IST).date()


def symbol_series_for_script(start=datetime(2024, 1, 1, tzinfo=IST), n=80):
    out, ts = [], start
    for _ in range(n):
        out.append(Candle(timestamp=ts, open=100, high=101, low=99, close=100, volume=50000, instrument=SYMBOL))
        ts += timedelta(days=1)
    return out


def make_trader(tmp_path: Path, provider, **kw):
    return EquitySwingVCPPaperTrader(
        provider, symbols=[SYMBOL], capital=100_000.0,
        state_path=tmp_path / "state.json",
        strategy_factory=lambda params: ScriptedStrategy(params), **kw,
    )


def test_poll_applies_scripted_entry_and_exit(tmp_path):
    provider = FakeEquityProvider({BENCH: bench_candles(), SYMBOL: symbol_series_for_script()})
    trader = make_trader(tmp_path, provider)
    status = trader.poll(now=datetime(2024, 3, 10, tzinfo=IST))
    assert status["positions"] == {}
    assert len(status["trades"]) == 1
    trade = status["trades"][0]
    assert trade["symbol"] == SYMBOL and trade["shares"] == 10
    assert trade["pnl"] == pytest.approx((120.0 - 100.0) * 10)
    assert status["balance"] == pytest.approx(100_000.0 + trade["pnl"])


def test_poll_mid_trade_shows_open_position(tmp_path):
    provider = FakeEquityProvider({BENCH: bench_candles(), SYMBOL: symbol_series_for_script()})
    trader = make_trader(tmp_path, provider)
    status = trader.poll(now=datetime(2024, 3, 3, tzinfo=IST))
    assert SYMBOL in status["positions"]
    assert status["positions"][SYMBOL]["shares"] == 10
    assert status["trades"] == []


def test_poll_is_idempotent_no_duplicate_trades(tmp_path):
    provider = FakeEquityProvider({BENCH: bench_candles(), SYMBOL: symbol_series_for_script()})
    trader = make_trader(tmp_path, provider)
    trader.poll(now=datetime(2024, 3, 10, tzinfo=IST))
    status2 = trader.poll(now=datetime(2024, 3, 11, tzinfo=IST))
    assert len(status2["trades"]) == 1  # not re-applied on a later poll


def test_lots_scale_with_compounded_balance(tmp_path):
    provider = FakeEquityProvider({BENCH: bench_candles(), SYMBOL: symbol_series_for_script()})
    trader = make_trader(tmp_path, provider)
    trader.state["balance"] = 250_000.0
    captured = {}
    original_factory = trader.strategy_factory
    def spy_factory(params):
        captured["capital"] = params.get("capital")
        return original_factory(params)
    trader.strategy_factory = spy_factory
    trader.poll(now=datetime(2024, 3, 3, tzinfo=IST))
    assert captured["capital"] == 250_000.0


def test_state_persists_and_reloads_from_disk(tmp_path):
    provider = FakeEquityProvider({BENCH: bench_candles(), SYMBOL: symbol_series_for_script()})
    trader1 = make_trader(tmp_path, provider)
    trader1.poll(now=datetime(2024, 3, 3, tzinfo=IST))
    assert SYMBOL in trader1.state["positions"]

    trader2 = EquitySwingVCPPaperTrader(
        provider, symbols=[SYMBOL], capital=100_000.0, state_path=tmp_path / "state.json",
        strategy_factory=lambda params: ScriptedStrategy(params))
    assert SYMBOL in trader2.state["positions"]
    status = trader2.poll(now=datetime(2024, 3, 10, tzinfo=IST))
    assert SYMBOL not in status["positions"]
    assert len(status["trades"]) == 1


# --------------------------------------------------------------------------- real strategy end-to-end
def _real_vcp_series():
    """Hand-verified: uptrend (220d) -> contracting base (34d) -> volume
    breakout -> quiet drift -> decline triggering a full exit (stop or
    trailing, whichever the real strategy reaches first)."""
    candles = []
    price = 100.0
    ts = datetime(2024, 1, 1, tzinfo=IST)
    for i in range(220):
        price *= 1.0035
        candles.append((ts, price * 0.995, price * 1.01, price * 0.985, price, 100000 + (i % 5) * 1000))
        ts += timedelta(days=1)
    base_peak = price
    for i in range(34):
        frac = i / 34
        amplitude = 0.12 * (1 - 0.7 * frac)
        wave = amplitude * (1 if i % 4 < 2 else -1) * (1 - frac * 0.5)
        close = base_peak * (1 + wave * 0.3)
        vol = max(90000 - i * 1500, 20000)
        candles.append((ts, close * 0.995, close * (1 + abs(wave) * 0.4 + 0.005),
                        close * (1 - abs(wave) * 0.4 - 0.005), close, vol))
        ts += timedelta(days=1)
    pivot_est = max(h for (_, _, h, _, _, _) in candles[-15:])
    breakout_close = pivot_est * 1.03
    candles.append((ts, pivot_est * 0.99, breakout_close * 1.01, pivot_est * 0.98, breakout_close, 200000))
    ts += timedelta(days=1)
    price = breakout_close
    for _ in range(5):
        price *= 1.003
        candles.append((ts, price * 0.998, price * 1.005, price * 0.995, price, 80000))
        ts += timedelta(days=1)
    for _ in range(6):
        price *= 0.97
        candles.append((ts, price * 1.01, price * 1.02, price * 0.99, price, 70000))
        ts += timedelta(days=1)
    return [Candle(timestamp=t, open=o, high=h, low=l, close=c, volume=v, instrument=SYMBOL)
           for (t, o, h, l, c, v) in candles]


def test_real_strategy_end_to_end_entry_and_exit(tmp_path):
    symbol_rows = _real_vcp_series()
    bench_rows = bench_candles(n=len(symbol_rows) + 10, start=symbol_rows[0].timestamp)
    provider = FakeEquityProvider({BENCH: bench_rows, SYMBOL: symbol_rows})
    trader = EquitySwingVCPPaperTrader(
        provider, symbols=[SYMBOL], capital=100_000.0, state_path=tmp_path / "state.json",
        strategy_factory=lambda params: EquitySwingVCPStrategy("equity_swing_vcp_paper", "t", params))
    status = trader.poll(now=symbol_rows[-1].timestamp + timedelta(days=1))
    assert status["positions"] == {}
    assert len(status["trades"]) == 1
    trade = status["trades"][0]
    assert trade["symbol"] == SYMBOL
    assert trade["pnl"] == pytest.approx(status["balance"] - 100_000.0)
