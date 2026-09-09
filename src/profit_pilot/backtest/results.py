from dataclasses import dataclass, field
from typing import Sequence
from datetime import datetime

from profit_pilot.execution.fill import Fill
from profit_pilot.execution.order import Order


@dataclass(frozen=True)
class Trade:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    side: str  # "BUY" or "SELL"
    qty: int
    entry_price: float
    exit_price: float
    pnl: float


@dataclass(frozen=True)
class BacktestResult:
    initial_cash: float
    final_cash: float
    fills: Sequence[Fill] = field(default_factory=tuple)
    equity_curve: Sequence[tuple[datetime, float]] = field(default_factory=tuple)
    rejected_orders: Sequence[object] = field(default_factory=tuple)  # Order objects
    trades: Sequence[Trade] = field(default_factory=tuple)
    return_pct: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    n_trades: int = 0
    win_rate: float = 0.0