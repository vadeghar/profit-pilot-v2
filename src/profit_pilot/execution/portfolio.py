from dataclasses import dataclass, field

from .fill import Fill
from .position import Position


@dataclass
class Portfolio:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)

    def apply_fill(self, fill: Fill) -> None:
        position = self.positions.setdefault(fill.order.symbol, Position(fill.order.symbol))
        if fill.order.side.value == "BUY":
            self.cash -= fill.price * fill.quantity
        else:
            self.cash += fill.price * fill.quantity
        position.apply(fill)

    def market_value(self, prices: dict[str, float]) -> float:
        return self.cash + sum(p.quantity * prices.get(p.symbol, p.average_price) for p in self.positions.values())
