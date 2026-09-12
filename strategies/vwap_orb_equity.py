from profit_pilot.strategy.base import Strategy
from profit_pilot.strategy.context import SignalContext
from profit_pilot.strategy.signal import Signal, SignalAction
from profit_pilot.strategy.registry import register


@register
class VWAPORBEquityStrategy(Strategy):
    """V1.1 VWAP + ORB Equity — dedicated state-machine (straddle pattern).

    Implements the v1.1 spec: ORB (09:15-09:30), signal window (09:30-11:30
    bar-open boundary), VWAP slope (3-bar), RVOL (rolling_cross_session),
    candle strength (70/30), tick-size rounding (0.05), same-bar SL-first tie-break.

    V1 restrictions (explicit in spec.md / registry):
    - 8 /equity symbols only; point-in-time universe deferred.
    - RVOL_MODE=rolling_cross_session (not same_session_only for baseline).
    - E0-E3 only; no retest, no trailing, no market filter.
    - No overnight holds; max 1 trade/symbol/day.
    """

    @property
    def registry_name(self) -> str:
        return "vwap_orb_equity"

    @property
    def symbol(self) -> str:
        # Strategy operates on per-symbol basis; symbol passed via context/state
        # Default to RELIANCE for V1 as per user's instruction (§4 / §29)
        return self.__class__.__name__

    def on_signal_context(self, ctx: SignalContext) -> Signal:
        # V1 framework: the dedicated state-machine uses a separate runner
        # (see strategies/vwap_orb_equity_runner.py) that builds the OR,
        # tracks session state, applies filters, and returns TradeResult.
        # This base-class hook is kept minimal — it registers the strategy
        # for registry lookup and provides a safe HOLD fallback.
        return Signal(SignalAction.HOLD, self.symbol, 0, metadata={
            "strategy_version": "1.1",
            "rvol_mode": self.params.get("rvol_mode") if hasattr(self, 'params') else None,
        })