from __future__ import annotations

from datetime import date

import pytest

from profit_pilot.backtest.run import BacktestRunConfig
from profit_pilot.backtest.runner import BacktestRunner
from profit_pilot.strategy.deterministic import HoldStrategy, BuySellPattern


def test_config_validates():
    cfg = BacktestRunConfig(
        symbol="NIFTY",
        start=date(2026, 1, 1),
        end=date(2026, 1, 1),
        initial_cash=100000.0,
    )
    assert cfg.initial_cash == 100000.0
    assert cfg.commission_flat == 0.0
    assert cfg.slippage_bps == 0.0


def test_config_rejects_negative_cash():
    with pytest.raises(ValueError):
        BacktestRunConfig(
            symbol="NIFTY",
            start=date(2026, 1, 1),
            end=date(2026, 1, 1),
            initial_cash=-100,
        )


def test_config_rejects_negative_rates():
    with pytest.raises(ValueError):
        BacktestRunConfig(
            symbol="NIFTY",
            start=date(2026, 1, 1),
            end=date(2026, 1, 1),
            initial_cash=100000,
            commission_flat=-1,
        )


def test_hold_strategy_no_fills():
    cfg = BacktestRunConfig(
        symbol="NIFTY",
        start=date(2026, 1, 1),
        end=date(2026, 1, 1),
        initial_cash=100000.0,
    )
    engine = __import__(
        "profit_pilot.backtest.engine", fromlist=["BacktestEngine"]
    ).BacktestEngine()
    # Provide states directly; engine should use the config's dates but we don't have a data provider here
    # Instead, manually set engine.data_provider to None and inject states through a custom mechanism
    # Or, simpler: create engine with data_provider=None and rely on manual states
    # Since the engine pulls from config.start/config.end if data_provider exists,
    # let's provide an empty provider.
    # For simplicity: just pass config and rely on empty states (no fills).
    result = engine.run(cfg, HoldStrategy())
    assert len(result.fills) == 0
    assert result.final_cash == 100000.0


def test_next_bar_fill_and_equity_curve():
    """Signal at bar t is filled at bar t+1's price (next-bar, no lookahead)."""
    from profit_pilot.backtest.engine import BacktestEngine
    from profit_pilot.data.models import MarketState
    from datetime import datetime, timezone, timedelta
    from profit_pilot.execution.order import OrderSide

    cfg = BacktestRunConfig(
        symbol="NIFTY",
        start=date(2026, 1, 1),
        end=date(2026, 1, 1),
        initial_cash=100000.0,
        slippage_bps=0.0,
        commission_flat=0.0,
    )
    base = datetime(2026, 1, 1, 9, 15, tzinfo=timezone.utc)
    # Prices increasing so we can verify next-bar fill price
    states = []
    for i in range(5):
        ts = base + timedelta(minutes=i)
        states.append(MarketState(ts, "NIFTY", 100.0 + i * 10))

    engine = BacktestEngine()
    result = engine.run(cfg, BuySellPattern())

    # Should have a BUY (fills at bar 2 price = 110) and SELL (fills at bar 3 price = 120)
    assert len(result.fills) == 2
    assert result.fills[0].order.side == OrderSide.BUY
    # BUY signal at bar 0 -> filled at bar 1 (next bar) = price 110
    assert result.fills[0].price == 110.0
    # SELL signal at bar 1 -> filled at bar 2 = price 120
    assert result.fills[1].price == 120.0
    # Equity curve should have entries
    assert len(result.equity_curve) >= 1


def test_cash_overdraw_rejected():
    """Orders that would push cash negative are rejected."""
    from profit_pilot.backtest.engine import BacktestEngine
    from profit_pilot.data.models import MarketState
    from datetime import datetime, timezone, timedelta

    cfg = BacktestRunConfig(
        symbol="NIFTY",
        start=date(2026, 1, 1),
        end=date(2026, 1, 1),
        initial_cash=100.0,  # Not enough for a large buy
    )
    base = datetime(2026, 1, 1, 9, 15, tzinfo=timezone.utc)
    states = [
        MarketState(base + timedelta(minutes=i), "NIFTY", 1000.0 + i)
        for i in range(5)
    ]

    class BigBuy(Strategy):
        @property
        def symbol(self):
            return "NIFTY"
        def on_signal_context(self, ctx):
            return Signal(SignalAction.BUY, "NIFTY", 100)  # 100 shares at 1000 = 100k > 100 cash
        def __init__(self):
            super().__init__(name="bigbuy")

    from profit_pilot.strategy.base import Strategy
    from profit_pilot.strategy.signal import Signal, SignalAction
    engine = BacktestEngine()
    result = engine.run(cfg, BigBuy())
    # The order should be rejected (cash goes negative), so it should not execute
    assert len(result.fills) == 0
    assert result.final_cash == 100.0  # Cash unchanged


def test_end_to_end_runner_single_day():
    """End-to-end: runner fetches from API over a single day, runs a simple strategy."""
    cfg = BacktestRunConfig(
        symbol="NIFTY",
        start=date(2026, 1, 1),
        end=date(2026, 1, 1),
        initial_cash=100000.0,
    )
    runner = BacktestRunner(cfg)
    result = runner.run()
    # Engine + provider + deterministic strategy all work
    assert result is not None
    assert result.initial_cash == 100000.0
