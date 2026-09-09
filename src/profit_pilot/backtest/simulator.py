from profit_pilot.data.models import MarketState
from profit_pilot.execution.fill import Fill
from profit_pilot.execution.order import Order


class ExecutionSimulator:
    def fill(self, order: Order, state: MarketState) -> Fill:
        if order.symbol != state.symbol:
            raise ValueError("order symbol does not match market state")
        return Fill(order, state.price, order.quantity, state.timestamp)
