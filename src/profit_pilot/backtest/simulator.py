from dataclasses import dataclass
from datetime import datetime

from profit_pilot.data.models import MarketState
from profit_pilot.execution.fill import Fill
from profit_pilot.execution.order import Order


@dataclass(frozen=True)
class ExitDecision:
    reason: str
    price: float
    timestamp: datetime


def evaluate_long_exit(
    entry_price: float,
    stop_loss: float,
    target: float,
    bar: MarketState,
    bars_held: int,
    max_holding_bars: int,
) -> ExitDecision | None:
    """Evaluate a long exit using completed OHLC data only.

    Conservative rule when both stop and target occur in one candle: stop wins.
    A gap below stop exits at the open, otherwise stop/target execute at their
    levels. A time stop exits at the close after the holding limit.
    """
    o = float(bar.fields.get("open", bar.price))
    h = float(bar.fields.get("high", bar.price))
    l = float(bar.fields.get("low", bar.price))
    if o <= stop_loss:
        return ExitDecision("stop", o, bar.timestamp)
    if l <= stop_loss:
        return ExitDecision("stop", stop_loss, bar.timestamp)
    if h >= target:
        return ExitDecision("target", target, bar.timestamp)
    if bars_held >= max_holding_bars:
        return ExitDecision("time_stop", bar.price, bar.timestamp)
    return None


class ExecutionSimulator:
    def fill(self, order: Order, state: MarketState) -> Fill:
        if order.symbol != state.symbol:
            raise ValueError("order symbol does not match market state")
        return Fill(order, state.price, order.quantity, state.timestamp)
