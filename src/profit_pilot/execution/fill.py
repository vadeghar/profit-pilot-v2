from dataclasses import dataclass
from datetime import datetime

from .order import Order


@dataclass(frozen=True)
class Fill:
    order: Order
    price: float
    quantity: int
    filled_at: datetime

    def __post_init__(self) -> None:
        if self.price < 0 or self.quantity <= 0 or self.quantity > self.order.quantity:
            raise ValueError("fill price must be non-negative and quantity must be valid")
