"""Options & Derivatives Base Strategy with Built-in Adjustments (NFO / MCX)

Designed for multi-leg derivative strategies (Straddles, Strangles, Iron Condors, Spreads)
where strike selection, rolling legs, delta-hedging, and target adjustments are defined
ENTIRELY within the strategy class implementation rather than simple fixed rules.
"""

from typing import Dict, Any, Optional, List
from core.models import Signal, OrderSide, OrderType, Candle, Tick
from strategies import StrategyBase, StrategyRegistry
from platform_config import get_index_lot_size


class NFOOptionsStrategyBase(StrategyBase):
    """
    Self-contained Options Strategy Base.
    Encapsulates all adjustment logic, dynamic leg rebalancing, delta/gamma hedge triggers,
    and multi-leg tracking internally within the strategy instance.
    """

    def __init__(self, strategy_id: str, name: str, params: Dict[str, Any] = None):
        super().__init__(strategy_id, name, params)
        self.underlying = self.params.get("underlying", "NSE:NIFTY")
        self.strike_step = self.params.get("strike_step", 50)
        self.lot_size = self.params.get(
            "lot_size",
            get_index_lot_size(self.underlying, 1),
        )
        
        # Internal state for tracking active derivative legs
        self.active_legs: Dict[str, Dict[str, Any]] = {}
        self.adjustment_history: List[Dict[str, Any]] = []

    def select_atm_strike(self, spot_price: float) -> int:
        """Calculate closest ATM strike price"""
        return int(round(spot_price / self.strike_step) * self.strike_step)

    def on_tick(self, tick: Tick) -> Optional[Signal]:
        """Override to implement real-time adjustment triggers (e.g. 30% SL on individual leg)"""
        return None

    def on_candle(self, candle: Candle) -> Optional[Signal]:
        """Process candle and perform internal multi-leg evaluation or adjustment"""
        return None

    def adjust_leg(self, leg_id: str, reason: str, action: str, new_strike: Optional[int] = None) -> Dict[str, Any]:
        """Record and execute internal option adjustment"""
        adjustment_record = {
            "leg_id": leg_id,
            "reason": reason,
            "action": action,
            "new_strike": new_strike,
            "timestamp": self._data.get("last_timestamp")
        }
        self.adjustment_history.append(adjustment_record)
        self.logger.info(f"Options Adjustment triggered on {leg_id}: {reason} -> {action}")
        return adjustment_record
