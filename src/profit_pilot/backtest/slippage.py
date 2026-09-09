from profit_pilot.execution.order import Order
from typing import Protocol

class SlippageModel(Protocol):
    def adjust_price(self, side: str, price: float) -> float:
        """Apply slippage to a price based on side."""
        ...

class BpsSlippage:
    def __init__(self, bps: float):
        self.bps = float(bps) / 10000.0  # bps to fraction
    def adjust_price(self, side: str, price: float) -> float:
        if side == "BUY":
            return price * (1.0 + self.bps)
        else:
            return price * (1.0 - self.bps)

# Convenience zero-slippage stub
zero_slippage = BpsSlippage(0.0)