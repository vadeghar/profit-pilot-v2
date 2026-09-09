from profit_pilot.execution.order import Order
from typing import Protocol, List

class CommissionModel(Protocol):
    def compute(self, order: Order, price: float) -> float:
        """Compute commission for the given order and price."""
        ...

class FlatCommission:
    def __init__(self, flat: float):
        self.flat = float(flat)
    def compute(self, order: Order, price: float) -> float:
        return self.flat

class BpsCommission:
    def __init__(self, bps: float):
        # bps is basis points (1 bps = 0.0001)
        self.bps = float(bps) / 10000.0
    def compute(self, order: Order, price: float) -> float:
        return self.bps * order.quantity * price

class CompositeCommission:
    def __init__(self, *models: CommissionModel):
        self.models = list(models)
    def compute(self, order: Order, price: float) -> float:
        return sum(m.compute(order, price) for m in self.models)