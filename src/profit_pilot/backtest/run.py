from dataclasses import dataclass, field
from datetime import date
from typing import Mapping

@dataclass(frozen=True)
class BacktestRunConfig:
    symbol: str
    start: date
    end: date
    initial_cash: float
    commission_flat: float = 0.0
    commission_bps: float = 0.0
    slippage_bps: float = 0.0
    strategy_params: Mapping[str, object] = field(default_factory=dict)
    bars_per_year: int = 101_790  # ~261 * (6.5 * 60)

    def __post_init__(self) -> None:
        if self.initial_cash < 0:
            raise ValueError("initial_cash must be non-negative")
        if self.commission_flat < 0 or self.commission_bps < 0 or self.slippage_bps < 0:
            raise ValueError("commission_flat, commission_bps, slippage_bps must be non-negative")