from datetime import datetime, timezone

from profit_pilot.backtest.engine import BacktestEngine
from profit_pilot.data.models import MarketState
from profit_pilot.execution.fill import Fill
from profit_pilot.execution.order import Order, OrderSide
from profit_pilot.execution.portfolio import Portfolio
from profit_pilot.execution.position import Position
from profit_pilot.strategy.signal import Signal, SignalAction


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_contracts_and_portfolio_lifecycle() -> None:
    state = MarketState(NOW, "TEST", 100.0)
    signal = Signal(SignalAction.BUY, "TEST", 2)
    order = Order.from_signal(signal, NOW)
    assert order is not None and order.side is OrderSide.BUY
    fill = Fill(order, state.price, 2, NOW)
    portfolio = Portfolio(1_000.0)
    portfolio.apply_fill(fill)
    assert portfolio.cash == 800.0
    assert portfolio.positions["TEST"].quantity == 2


def test_strategy_to_fill_framework_validation() -> None:
    class ValidationStrategy:
        def on_market_state(self, state: MarketState) -> Signal:
            return Signal(SignalAction.BUY, state.symbol, 1)

    result = BacktestEngine().run([MarketState(NOW, "TEST", 25.0)], ValidationStrategy(), 100.0)
    assert result.final_cash == 75.0
    assert len(result.fills) == 1


def test_position_average_price() -> None:
    order = Order("TEST", OrderSide.BUY, 2, NOW)
    position = Position("TEST")
    position.apply(Fill(order, 10.0, 2, NOW))
    assert position.quantity == 2 and position.average_price == 10.0
