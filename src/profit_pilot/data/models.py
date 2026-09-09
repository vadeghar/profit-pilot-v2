from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping


@dataclass(frozen=True)
class MarketState:
    timestamp: datetime
    symbol: str
    price: float
    fields: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must not be empty")
        if self.price < 0:
            raise ValueError("price must be non-negative")
