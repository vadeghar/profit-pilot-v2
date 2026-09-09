from __future__ import annotations

from datetime import datetime, timezone

import pytest

from profit_pilot.data.models import MarketState
from profit_pilot.strategy.base import Strategy
from profit_pilot.strategy.context import SignalContext
from profit_pilot.strategy.registry import get_strategy, register, registered_names
from profit_pilot.strategy.signal import Signal, SignalAction

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_state(price: float, ts: datetime = NOW) -> MarketState:
    return MarketState(ts, "NIFTY", price)


def test_signal_action_mapping_buy_sell_hold() -> None:
    # Order.from_signal is exercised against the existing execution layer.
    from profit_pilot.execution.order import Order, OrderSide

    buy = Order.from_signal(Signal(SignalAction.BUY, "NIFTY", 2), NOW)
    sell = Order.from_signal(Signal(SignalAction.SELL, "NIFTY", 2), NOW)
    hold = Order.from_signal(Signal(SignalAction.HOLD, "NIFTY", 0), NOW)

    assert buy is not None and buy.side is OrderSide.BUY
    assert sell is not None and sell.side is OrderSide.SELL
    assert hold is None


def test_signal_validation_rules() -> None:
    with pytest.raises(ValueError):
        Signal(SignalAction.BUY, "", 1)
    with pytest.raises(ValueError):
        Signal(SignalAction.BUY, "NIFTY", -1)


def test_signal_context_close_window() -> None:
    history = [make_state(100.0), make_state(101.0)]
    ctx = SignalContext(state=make_state(102.0), history=history)
    assert ctx.close == [100.0, 101.0, 102.0]
    assert ctx.symbol == "NIFTY"


def test_dummy_strategy_emit_and_normalise() -> None:
    class Dummy(Strategy):
        def __init__(self) -> None:
            super().__init__(name="dummy")
            self.qty = 2

        @property
        def symbol(self) -> str:
            return "NIFTY"

        def on_signal_context(self, ctx: SignalContext) -> Signal:
            return Signal(SignalAction.BUY, ctx.symbol, self.qty)

    s = Dummy()
    sig = s.on_market_state(make_state(100.0))
    assert sig.action is SignalAction.BUY and sig.symbol == "NIFTY" and sig.quantity == 2


def test_normalise_hold_zeroes_quantity() -> None:
    class HoldStrategy(Strategy):
        @property
        def symbol(self) -> str:
            return "NIFTY"

        def on_signal_context(self, ctx: SignalContext) -> Signal:
            return Signal(SignalAction.HOLD, ctx.symbol, 99)

    s = HoldStrategy(name="hold")
    sig = s.on_market_state(make_state(50.0))
    assert sig.action is SignalAction.HOLD
    assert sig.quantity == 0


def test_normalise_rejects_negative_quantity() -> None:
    class Neg(Strategy):
        @property
        def symbol(self) -> str:
            return "NIFTY"

        def on_signal_context(self, ctx: SignalContext) -> Signal:
            return Signal(SignalAction.SELL, ctx.symbol, -3)

    with pytest.raises(ValueError):
        Neg(name="neg").on_market_state(make_state(50.0))


def test_registry_register_and_get() -> None:
    class Spec(Strategy):
        @property
        def symbol(self) -> str:
            return "NIFTY"

        def on_signal_context(self, ctx: SignalContext) -> Signal:
            return Signal(SignalAction.HOLD, ctx.symbol, 0)

    # register returns the class so it works as a class decorator
    assert register(Spec) is Spec
    assert get_strategy("Spec") is Spec
    assert "Spec" in registered_names()

    with pytest.raises(KeyError):
        get_strategy("DoesNotExist")


def test_registry_rejects_duplicate() -> None:
    from profit_pilot.strategy.registry import STRATEGY_REGISTRY

    key = "DupStrategy"
    # Avoid clobbering an existing registration in the shared registry.
    existing = STRATEGY_REGISTRY.get(key)

    class A(Strategy):
        registry_name = key  # class attr shadows base property

        @property
        def symbol(self) -> str:
            return "NIFTY"

        def on_signal_context(self, ctx: SignalContext) -> Signal:
            return Signal(SignalAction.HOLD, ctx.symbol, 0)

    class B(Strategy):
        registry_name = key

        @property
        def symbol(self) -> str:
            return "NIFTY"

        def on_signal_context(self, ctx: SignalContext) -> Signal:
            return Signal(SignalAction.HOLD, ctx.symbol, 0)

    try:
        register(A)
        with pytest.raises(ValueError):
            register(B)
    finally:
        STRATEGY_REGISTRY.pop(key, None)
        if existing is not None:
            STRATEGY_REGISTRY[key] = existing