"""Execution Engine with Multi-Leg Orchestrator"""

import asyncio
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from core.models import (
    Order, OrderSide, OrderType, OrderStatus, OrderProductType,
    Signal, Trade, Position, generate_order_id, generate_trade_id, generate_position_id
)
from utils import Logger, get_timestamp


class ExecutionPolicy(Enum):
    """Execution policy for multi-leg orders"""
    ALL_OR_NONE = "ALL_OR_NONE"     # All legs must fill or none
    PARTIAL_OK = "PARTIAL_OK"       # Partial fills allowed
    ROLLBACK_ON_FAILURE = "ROLLBACK_ON_FAILURE"  # Rollback filled legs on failure


class ExecutionResult:
    """Result of order execution"""
    
    def __init__(self, success: bool, order: Optional[Order] = None, 
                 error: Optional[str] = None):
        self.success = success
        self.order = order
        self.error = error


@dataclass
class MultiLegOrder:
    """Multi-leg order definition"""
    legs: List[Signal]  # Each leg is a Signal
    policy: ExecutionPolicy = ExecutionPolicy.ROLLBACK_ON_FAILURE
    timeout_seconds: float = 30.0
    
    @property
    def is_complete(self) -> bool:
        return all(leg.metadata.get('_filled', False) for leg in self.legs)


class ExecutionEngine:
    """Main execution engine for the platform"""
    
    def __init__(self, broker: Any, config: Dict[str, Any] = None):
        self.broker = broker
        self.config = config or {}
        self.logger = Logger("execution-engine")
        
        # Order tracking
        self._orders: Dict[str, Order] = {}
        self._order_lock = threading.Lock()
        
        # Position tracking
        self._positions: Dict[str, Position] = {}
        
        # Multi-leg orchestrator
        self._multi_leg_orders: Dict[str, MultiLegOrder] = {}
        
        # Callbacks
        self._on_order_update: Optional[Callable[[Order], None]] = None
        self._on_trade_update: Optional[Callable[[Trade], None]] = None
        self._on_position_update: Optional[Callable[[Position], None]] = None
        
        # Retry config
        self.max_retries = self.config.get('orderRetryAttempts', 3)
        self.retry_delay = self.config.get('orderRetryDelaySeconds', 2)
    
    def execute_signal(self, signal: Signal, product_type: OrderProductType = OrderProductType.MIS) -> ExecutionResult:
        """Execute a single signal"""
        try:
            # Place order via broker
            order = self.broker.place_order(
                instrument=signal.instrument,
                side=signal.action,
                quantity=signal.quantity,
                order_type=signal.order_type,
                price=signal.price,
                product_type=product_type
            )
            
            # Track order
            self._track_order(order)
            
            self.logger.info(f"Order executed: {order.order_id} {signal.action.value} {signal.quantity}@{order.average_price}")
            
            return ExecutionResult(success=True, order=order)
            
        except Exception as e:
            self.logger.error(f"Order execution failed: {e}")
            return ExecutionResult(success=False, error=str(e))
    
    def execute_multi_leg(self, multi_leg: MultiLegOrder) -> Dict[str, ExecutionResult]:
        """Execute a multi-leg order with orchestration"""
        results = {}
        
        # Sort legs: LONG first, then SHORT (to secure margin)
        sorted_legs = sorted(
            multi_leg.legs, 
            key=lambda s: 0 if s.action == OrderSide.BUY else 1
        )
        
        filled_legs = []
        
        for i, signal in enumerate(sorted_legs):
            # Execute leg
            result = self.execute_signal(signal)
            results[f"leg_{i}"] = result
            
            if result.success:
                filled_legs.append(signal)
                signal.metadata['_filled'] = True
                signal.metadata['_order_id'] = result.order.order_id
            else:
                # Handle failure based on policy
                if multi_leg.policy == ExecutionPolicy.ALL_OR_NONE:
                    # Cancel all filled legs
                    self._rollback_legs(filled_legs)
                    for leg in filled_legs:
                        leg.metadata['_filled'] = False
                    break
                    
                elif multi_leg.policy == ExecutionPolicy.ROLLBACK_ON_FAILURE:
                    # Rollback filled legs
                    self._rollback_legs(filled_legs)
                    for leg in filled_legs:
                        leg.metadata['_filled'] = False
                    break
        
        return results
    
    def _rollback_legs(self, legs: List[Signal]) -> None:
        """Rollback filled legs (market exit)"""
        for leg in legs:
            try:
                exit_side = OrderSide.SELL if leg.action == OrderSide.BUY else OrderSide.BUY
                exit_order = self.broker.place_order(
                    instrument=leg.instrument,
                    side=exit_side,
                    quantity=leg.quantity,
                    order_type=OrderType.MARKET,
                    product_type=leg.metadata.get('product_type', OrderProductType.MIS)
                )
                self.logger.warning(f"Rolled back leg: {exit_order.order_id}")
            except Exception as e:
                self.logger.error(f"Rollback failed: {e}")
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        try:
            success = self.broker.cancel_order(order_id)
            if success and order_id in self._orders:
                with self._order_lock:
                    order = self._orders[order_id]
                    order.status = OrderStatus.CANCELLED
                    order.updated_at = __import__('utils.timezone', fromlist=['now_ist']).now_ist()
            return success
        except Exception as e:
            self.logger.error(f"Cancel order failed: {e}")
            return False
    
    def modify_order(self, order_id: str, quantity: int = None, price: float = None) -> bool:
        """Modify an order"""
        try:
            order = self.broker.modify_order(order_id, quantity, price)
            if order and order_id in self._orders:
                self._track_order(order)
            return True
        except Exception as e:
            self.logger.error(f"Modify order failed: {e}")
            return False
    
    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID"""
        return self._orders.get(order_id)
    
    def get_all_orders(self) -> List[Order]:
        """Get all orders"""
        with self._order_lock:
            return list(self._orders.values())
    
    def _track_order(self, order: Order) -> None:
        """Track an order"""
        with self._order_lock:
            self._orders[order.order_id] = order
        
        # Update position
        self._update_position(order)
        
        # Notify callback
        if self._on_order_update:
            self._on_order_update(order)
    
    def _update_position(self, order: Order) -> None:
        """Update position based on order"""
        if order.status not in [OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED]:
            return
        
        instrument = order.instrument
        
        # Get or create position
        if instrument not in self._positions:
            self._positions[instrument] = Position(
                position_id=generate_position_id(),
                strategy_id=order.metadata.get('strategy_id', ''),
                instrument=instrument,
                quantity=0,
                average_price=0.0,
                side=OrderSide.BUY if order.side == OrderSide.SELL else OrderSide.SELL
            )
        
        position = self._positions[instrument]
        
        if order.side == OrderSide.BUY:
            # Adding to position
            total_qty = position.quantity + order.filled_quantity
            if position.quantity == 0:
                position.average_price = order.average_price
            else:
                position.average_price = (
                    (position.average_price * position.quantity + 
                     order.average_price * order.filled_quantity) / total_qty
                )
            position.quantity = total_qty
        else:
            # Reducing position
            position.quantity -= order.filled_quantity
            if position.quantity == 0:
                position.average_price = 0.0
        
        position.updated_at = __import__('utils.timezone', fromlist=['now_ist']).now_ist()
        
        # Close position if no quantity
        if position.quantity == 0:
            position.status = Position.CLOSED
        
        # Notify callback
        if self._on_position_update:
            self._on_position_update(position)
    
    def get_position(self, instrument: str) -> Optional[Position]:
        """Get position for instrument"""
        return self._positions.get(instrument)
    
    def get_all_positions(self) -> List[Position]:
        """Get all positions"""
        return list(self._positions.values())
    
    def close_position(self, instrument: str) -> bool:
        """Close position for instrument"""
        position = self._positions.get(instrument)
        if not position or position.quantity == 0:
            return True
        
        try:
            exit_side = OrderSide.SELL if position.side == OrderSide.BUY else OrderSide.BUY
            self.broker.place_order(
                instrument=instrument,
                side=exit_side,
                quantity=position.quantity,
                order_type=OrderType.MARKET,
                product_type=OrderProductType.MIS
            )
            return True
        except Exception as e:
            self.logger.error(f"Close position failed: {e}")
            return False
    
    def close_all_positions(self) -> bool:
        """Close all positions"""
        success = True
        for instrument in list(self._positions.keys()):
            if not self.close_position(instrument):
                success = False
        return success
    
    def on_order_update(self, callback: Callable[[Order], None]) -> None:
        """Register order update callback"""
        self._on_order_update = callback
    
    def on_trade_update(self, callback: Callable[[Trade], None]) -> None:
        """Register trade update callback"""
        self._on_trade_update = callback
    
    def on_position_update(self, callback: Callable[[Position], None]) -> None:
        """Register position update callback"""
        self._on_position_update = callback


class RiskManager:
    """Risk management for execution"""
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.logger = Logger("risk-manager")
        
        # Limits
        self.max_position_size = self.config.get('maxPositionSize', 100)
        self.max_loss_per_trade = self.config.get('maxLossPerTrade', 10000)
        self.max_daily_loss = self.config.get('maxDailyLoss', 50000)
        
        # Daily tracking
        self._daily_pnl = 0.0
        self._daily_trades = 0
        self._last_reset = __import__('utils.timezone', fromlist=['now_ist']).now_ist().date()
    
    def check_signal(self, signal: Signal) -> bool:
        """Check if signal passes risk checks"""
        # Reset daily counters if new day
        today = __import__('utils.timezone', fromlist=['now_ist']).now_ist().date()
        if today > self._last_reset:
            self._daily_pnl = 0.0
            self._daily_trades = 0
            self._last_reset = today
        
        # Check position size
        if signal.quantity > self.max_position_size:
            self.logger.warning(f"Signal rejected: quantity {signal.quantity} exceeds max {self.max_position_size}")
            return False
        
        # Check daily loss limit
        if self._daily_pnl < -self.max_daily_loss:
            self.logger.warning("Signal rejected: daily loss limit exceeded")
            return False
        
        return True
    
    def update_pnl(self, pnl: float) -> None:
        """Update daily P&L"""
        self._daily_pnl += pnl
        self._daily_trades += 1
    
    def get_daily_stats(self) -> Dict[str, Any]:
        """Get daily statistics"""
        return {
            'daily_pnl': self._daily_pnl,
            'daily_trades': self._daily_trades,
            'max_daily_loss': self.max_daily_loss,
            'remaining_loss_capacity': self.max_daily_loss + self._daily_pnl
        }
