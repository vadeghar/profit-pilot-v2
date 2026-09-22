"""Regression tests for the SSE backtest stream payload.

Bug being guarded against:
  The ``backtest_completed`` event used to send only
  ``BacktestResult.__dict__()`` (a summary WITHOUT ``trades`` /
  ``equity_curve`` / ``candles_evaluated`` / ``period``), so the UI rendered
  "undefined candles (undefined)", an empty equity curve / portfolio
  progression and wiped the streamed trade fills.

Run: python -m pytest tests/test_backtest_stream.py -q
"""
import json
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import Candle, OrderSide, Signal, BacktestStatus
from backtest import BacktestConfig, BacktestEngine
from strategies import StrategyBase, StrategyRegistry

INSTRUMENT = "TEST-INST"
N_CANDLES = 20


class StubProvider:
    """Minimal data provider returning a fixed candle series."""

    def __init__(self, candles):
        self._candles = candles

    def get_historical_candles(self, instrument, timeframe, start_date, end_date):
        return list(self._candles)


def make_candles(n=N_CANDLES):
    """Deterministic OHLC series with an up-leg and a down-leg."""
    candles = []
    price = 100.0
    for i in range(n):
        drift = 1.0 if i < 10 else -1.0
        close = price + drift
        candles.append(Candle(
            timestamp=datetime(2024, 1, 1 + i // 2, 9 + i % 12),
            open=price,
            high=close + 0.5,
            low=price - 0.5,
            close=close,
            volume=1000 + i,
            instrument=INSTRUMENT,
            timeframe="1d",
        ))
        price = close
    return candles


class BuySellStubStrategy(StrategyBase):
    """BUY on candle 5, SELL on candle 15 -> exactly one closed trade."""

    def _init_indicators(self):
        self._count = 0

    def on_tick(self, tick):
        return None

    def on_candle(self, candle):
        self._count += 1
        if self._count == 5:
            return Signal(
                strategy_id=self.strategy_id,
                instrument=INSTRUMENT,
                action=OrderSide.BUY,
                quantity=1,
                price=0.0,  # fill at candle close
                reason="stub_entry",
            )
        if self._count == 15:
            return Signal(
                strategy_id=self.strategy_id,
                instrument=INSTRUMENT,
                action=OrderSide.SELL,
                quantity=1,
                price=0.0,
                reason="stub_exit",
            )
        return None


class ExplodingStrategy(StrategyBase):
    """Fails during initialize() to exercise the failure event path."""

    def on_tick(self, tick):
        return None

    def on_candle(self, candle):
        return None

    def initialize(self):
        raise RuntimeError("stub initialization failure")


@pytest.fixture
def stub_strategy_registered():
    StrategyRegistry.register("stream_stub", BuySellStubStrategy)
    StrategyRegistry.register("stream_exploding", ExplodingStrategy)
    yield
    StrategyRegistry._strategies.pop("stream_stub", None)
    StrategyRegistry._strategies.pop("stream_exploding", None)


def make_config():
    return BacktestConfig(
        strategy_id="bt-stream-test",
        strategy_name="stream_stub",
        strategy_params={},
        instruments=[INSTRUMENT],
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 31),
        initial_capital=100000.0,
        timeframe="1d",
    )


def make_engine(candles=None):
    provider = StubProvider(candles if candles is not None else make_candles())
    engine = BacktestEngine(make_config(), data_provider=provider)
    events = []
    engine.set_event_callback(lambda etype, payload: events.append((etype, payload)))
    return engine, events


class TestNoSilentMockData:
    """Regression for the NIFTY ₹70-100 bug.

    The engine used to silently fall back to ``_generate_mock_data`` (a random
    walk starting at ₹100) whenever ``data_provider`` was None, producing
    meaningless trades at fabricated prices (NSE:NIFTY shown at ₹70-100
    instead of ~₹20,000+). A backtest must fail loudly instead.
    """

    def test_missing_provider_raises(self):
        engine = BacktestEngine(make_config(), data_provider=None)
        with pytest.raises(RuntimeError, match="No data provider configured"):
            engine._load_historical_data()

    def test_auth_failure_blocks_execution(self):
        """Broker auth failure must abort the run (never backtest on stale
        cache / unauthenticated data). The gate runs both at stream
        construction (web_app) and inside _load_historical_data (defense in
        depth for direct engine users)."""

        class FailingAuthProvider(StubProvider):
            name = "breeze"

            def ensure_authenticated(self):
                raise RuntimeError(
                    "Breeze authentication failed: Missing Breeze credentials "
                    "in .env: BREEZE_SESSION_TOKEN."
                )

        engine = BacktestEngine(
            make_config(),
            data_provider=FailingAuthProvider(make_candles()),
        )
        with pytest.raises(RuntimeError, match="authentication failed"):
            engine._load_historical_data()

    def test_empty_provider_response_aborts(self):
        """Empty provider output must abort, not run on fabricated data."""

        class EmptyProvider(StubProvider):
            name = "breeze"

            def ensure_authenticated(self):
                return None

            def get_historical_candles(self, instrument, timeframe,
                                       start_date, end_date):
                return []

        engine = BacktestEngine(
            make_config(), data_provider=EmptyProvider(make_candles()))
        with pytest.raises(RuntimeError, match="no usable candles"):
            engine._load_historical_data()

    def test_provider_data_is_used_verbatim(self):
        """Provider data is normalized into NormalizedCandle (not stored raw)
        and the backtest engine must never consume an un-normalized broker
        payload. The *values* (o/h/l/c/v) are preserved through normalization.
        The engine cache holds core Candle objects converted from normalized
        candles (strategies consume core Candle)."""
        from core.models import Candle as EngineCandle
        from market_data.normalize import NormalizedCandle, ensure_normalized_candles
        candles = make_candles()
        engine, _ = make_engine(candles)
        engine._load_historical_data()
        cached = engine._candle_cache[INSTRUMENT]
        assert len(cached) == len(candles)
        assert all(isinstance(c, EngineCandle) for c in cached)
        # values preserved (envelope-clamped to be physically valid)
        normalized = ensure_normalized_candles(candles)
        assert all(isinstance(c, NormalizedCandle) for c in normalized)
        for cc, nc, c in zip(cached, normalized, candles):
            assert cc.open == nc.open == c.open
            assert cc.close == nc.close == c.close
            assert cc.high == nc.high == max(c.high, c.open, c.close)
            assert cc.low == nc.low == min(c.low, c.open, c.close)
            assert cc.volume == nc.volume == c.volume
            assert cc.instrument == nc.instrument == INSTRUMENT
        assert all(c.close > 10 for c in cached)




class TestCompletedPayload:
    def test_completed_event_contains_full_ui_payload(self, stub_strategy_registered):
        """The completion event must carry everything renderModalResults needs."""
        engine, events = make_engine()
        result = engine.run()

        assert result.status == BacktestStatus.COMPLETED
        completed = [p for t, p in events if t == "backtest_completed"]
        assert len(completed) == 1
        payload = completed[0]["result"]

        # Previously-missing fields (UI showed "undefined candles (undefined)")
        assert payload["candles_evaluated"] == N_CANDLES
        assert payload["period"] == "2024-01-01 to 2024-01-31"

        # Equity curve for the Equity Curve / Portfolio Progression charts
        equity = payload["equity_curve"]
        assert len(equity) == N_CANDLES
        for row in equity:
            assert set(row) >= {"timestamp", "capital", "unrealized_pnl", "total_equity"}
            assert isinstance(row["timestamp"], str)  # ISO string, JSON-safe

        # Trades for "Simulated Trade Fills & Executions"
        trades = payload["trades"]
        assert len(trades) == 1
        trade = trades[0]
        assert trade["instrument"] == INSTRUMENT
        assert trade["status"] == "CLOSED"
        assert trade["entry_price"] > 0 and trade["exit_price"] > 0
        assert isinstance(trade["entry_time"], str)

        # Summary metrics the modal already displayed must remain present
        assert "total_return_pct" in payload
        assert "win_rate" in payload

    def test_completed_payload_is_json_serializable(self, stub_strategy_registered):
        """The SSE endpoint json-ifies the payload; any non-serializable value
        would kill the stream."""
        engine, events = make_engine()
        engine.run()

        for etype, payload in events:
            text = json.dumps({"event": etype, **payload})
            assert isinstance(text, str)

    def test_trade_row_keys_match_streamed_events(self, stub_strategy_registered):
        """Final trades render must keep the same keys the streamed
        trade_entry/trade_exit rows used (no wiping of streamed rows)."""
        engine, events = make_engine()
        engine.run()

        completed = [p for t, p in events if t == "backtest_completed"][0]
        trade = completed["result"]["trades"][0]
        # Keys the modal table renders per row
        for key in ("instrument", "entry_price", "exit_price", "pnl",
                    "entry_time", "exit_time", "quantity"):
            assert key in trade


class TestFailurePath:
    def test_engine_emits_backtest_failed_on_error(self, stub_strategy_registered):
        """Internal errors must emit backtest_failed so SSE clients see why
        the stream ended (no silent close -> "Stream connection error")."""
        config = make_config()
        config.strategy_name = "stream_exploding"
        provider = StubProvider(make_candles())
        engine = BacktestEngine(config, data_provider=provider)
        events = []
        engine.set_event_callback(lambda etype, payload: events.append((etype, payload)))

        result = engine.run()

        assert result.status == BacktestStatus.FAILED
        assert "stub initialization failure" in result.error_message
        failed = [p for t, p in events if t == "backtest_failed"]
        assert len(failed) == 1
        assert "stub initialization failure" in failed[0]["error"]
        # Failure payload must also survive SSE serialization
        json.dumps({"event": "backtest_failed", **failed[0]})
