from dataclasses import dataclass, field
from typing import Sequence

from profit_pilot.execution.fill import Fill


@dataclass(frozen=True)
class BacktestResult:
    initial_cash: float
    final_cash: float
    fills: Sequence[Fill] = field(default_factory=tuple)

    @property
    def pnl(self) -> float:
        return self.final_cash - self.initial_cash
