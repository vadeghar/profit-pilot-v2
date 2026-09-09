from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from profit_pilot.strategy.signal import SignalAction


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Order:
    symbol: str
    side: OrderSide
    quantity: int
    created_at: datetime

    @classmethod
    def from_signal(cls, signal: "Signal", created_at: datetime) -> "Order | None":
        if signal.action.value == "HOLD" or signal.quantity == 0:
            return None
        return cls(signal.symbol, OrderSide(signal.action.value), signal.quantity, created_at)

    def __post_init__(self) -> None:
        if not self.symbol or self.quantity <= 0:
            raise ValueError("orders require a symbol and positive quantity")
