from dataclasses import dataclass

from .fill import Fill


@dataclass
class Position:
    symbol: str
    quantity: int = 0
    average_price: float = 0.0

    def apply(self, fill: Fill) -> None:
        if fill.order.symbol != self.symbol:
            raise ValueError("fill symbol does not match position")
        signed = fill.quantity if fill.order.side.value == "BUY" else -fill.quantity
        if signed > 0:
            total = self.quantity * self.average_price + signed * fill.price
            self.quantity += signed
            self.average_price = total / self.quantity
        else:
            self.quantity += signed
            if self.quantity == 0:
                self.average_price = 0.0
