from collections.abc import Iterable
from datetime import date
from typing import Protocol

from .models import MarketState


class MarketDataProvider(Protocol):
    def states(self, start: date, end: date) -> Iterable[MarketState]: ...
