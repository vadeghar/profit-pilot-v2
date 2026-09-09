from __future__ import annotations

from dataclasses import dataclass

from profit_pilot.strategy.base import Strategy
from profit_pilot.strategy.context import SignalContext
from profit_pilot.strategy.indicators import crossover
from profit_pilot.strategy.registry import register
from profit_pilot.strategy.signal import Signal, SignalAction


@register
@dataclass
class MovingAverageCrossStrategy(Strategy):
    """Long/flat Nifty MA-crossover strategy built on the frozen spot lake.

    Trades ``NIFTY`` positionally. Emits a BUY when a fast SMA crosses above a
    slow SMA, a SELL when it crosses below; otherwise HOLD. Quantity is read
    from ``params["quantity"]`` (default 1) so backtests can size per-lot.
    """

    fast_window: int = 20
    slow_window: int = 60
    quantity: int = 1

    @property
    def symbol(self) -> str:
        return "NIFTY"

    def on_signal_context(self, ctx: SignalContext) -> Signal:
        direction = crossover(ctx.close, self.fast_window, self.slow_window)
        if direction > 0:
            return Signal(SignalAction.BUY, ctx.symbol, self.quantity)
        if direction < 0:
            return Signal(SignalAction.SELL, ctx.symbol, self.quantity)
        return Signal(SignalAction.HOLD, ctx.symbol, 0)