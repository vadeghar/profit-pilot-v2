from profit_pilot.backtest.results import BacktestResult
from profit_pilot.backtest.simulator import ExecutionSimulator
from profit_pilot.data.market_data import MarketDataProvider
from profit_pilot.data.models import MarketState
from profit_pilot.execution.portfolio import Portfolio
from profit_pilot.execution.order import Order
from profit_pilot.strategy.base import Strategy


class BacktestEngine:
    def __init__(self, simulator: ExecutionSimulator | None = None) -> None:
        self.simulator = simulator or ExecutionSimulator()

    def run(self, states: list[MarketState], strategy: Strategy, initial_cash: float) -> BacktestResult:
        portfolio = Portfolio(initial_cash)
        fills = []
        for state in states:
            order = Order.from_signal(strategy.on_market_state(state), state.timestamp)
            if order is not None:
                fill = self.simulator.fill(order, state)
                portfolio.apply_fill(fill)
                fills.append(fill)
        return BacktestResult(initial_cash, portfolio.cash, tuple(fills))
