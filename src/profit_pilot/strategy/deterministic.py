from profit_pilot.strategy.base import Strategy
from profit_pilot.strategy.context import SignalContext
from profit_pilot.strategy.signal import Signal, SignalAction
from profit_pilot.data.models import MarketState

# Register simple deterministic signal emitters as strategies
# This is necessary for Step 10 testing (we don't introduce real trading strategies)

class HoldStrategy(Strategy):
    """Strategy that always returns HOLD signals."""
    name = "HoldStrategy"
    symbol = "NIFTY"
    
    def on_signal_context(self, ctx: SignalContext) -> Signal:
        return Signal(SignalAction.HOLD, self.symbol, 0)

class OneBarBuy(Strategy):
    """Simple strategy that buys at the first bar, holds thereafter."""
    name = "OneBarBuy"
    symbol = "NIFTY"
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.has_bought = False
    
    def on_signal_context(self, ctx: SignalContext) -> Signal:
        if not self.has_bought:
            self.has_bought = True
            return Signal(SignalAction.BUY, self.symbol, 5)
        return Signal(SignalAction.HOLD, self.symbol, 0)

class BuySellPattern(Strategy):
    """Strategy that buys on first bar and sells on second bar."""
    name = "BuySellPattern"
    symbol = "NIFTY"
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.buy_signaled = False
        self.sell_signaled = False
    
    def on_signal_context(self, ctx: SignalContext) -> Signal:
        if not self.buy_signaled:
            self.buy_signaled = True
            return Signal(SignalAction.BUY, self.symbol, 10)
        elif not self.sell_signaled:
            self.sell_signaled = True
            return Signal(SignalAction.SELL, self.symbol, 10)
        return Signal(SignalAction.HOLD, self.symbol, 0)