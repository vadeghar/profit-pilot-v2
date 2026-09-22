"""Broker adapters for Trading Platform"""

import asyncio
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field

from datetime import timedelta
from core.models import (
    Instrument, Order, OrderSide, OrderType, OrderStatus, OrderProductType,
    Quote, Tick, Candle, generate_order_id
)
from utils import Logger, get_timestamp


class BrokerBase(ABC):
    """Abstract base class for broker adapters"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = Logger(f"broker.{self.__class__.__name__}")
        self._connected = False
        self._ws = None
        self._tick_callbacks: List[Callable[[Tick], None]] = []
        self._order_callbacks: List[Callable[[Order], None]] = []
        self._ws_thread: Optional[threading.Thread] = None
        self._running = False
    
    @property
    def is_connected(self) -> bool:
        return self._connected
    
    @abstractmethod
    def connect(self) -> bool:
        """Establish connection to broker"""
        pass
    
    @abstractmethod
    def disconnect(self) -> bool:
        """Disconnect from broker"""
        pass
    
    @abstractmethod
    def authenticate(self) -> bool:
        """Authenticate with broker"""
        pass
    
    @abstractmethod
    def place_order(self, instrument: str, side: OrderSide, quantity: int,
                   order_type: OrderType, price: float = 0.0,
                   product_type: OrderProductType = OrderProductType.MIS) -> Order:
        """Place an order"""
        pass
    
    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        pass
    
    @abstractmethod
    def modify_order(self, order_id: str, quantity: Optional[int] = None,
                    price: Optional[float] = None) -> Order:
        """Modify an order"""
        pass
    
    @abstractmethod
    def get_order_status(self, order_id: str) -> Order:
        """Get order status"""
        pass
    
    @abstractmethod
    def get_positions(self) -> List[Dict[str, Any]]:
        """Get all positions"""
        pass
    
    @abstractmethod
    def get_quote(self, instrument: str) -> Quote:
        """Get quote for instrument"""
        pass
    
    @abstractmethod
    def getHistoricalData(self, instrument: str, timeframe: str,
                         from_date: datetime, to_date: datetime) -> List[Dict[str, Any]]:
        """Get historical data"""
        pass
    
    def subscribe_ticks(self, instruments: List[str]) -> None:
        """Subscribe to tick data"""
        pass
    
    def unsubscribe_ticks(self, instruments: List[str]) -> None:
        """Unsubscribe from tick data"""
        pass
    
    def on_tick(self, callback: Callable[[Tick], None]) -> None:
        """Register tick callback"""
        self._tick_callbacks.append(callback)
    
    def on_order_update(self, callback: Callable[[Order], None]) -> None:
        """Register order update callback"""
        self._order_callbacks.append(callback)
    
    def _notify_tick(self, tick: Tick) -> None:
        """Notify tick subscribers"""
        for callback in self._tick_callbacks:
            try:
                callback(tick)
            except Exception as e:
                self.logger.error(f"Error in tick callback: {e}")
    
    def _notify_order(self, order: Order) -> None:
        """Notify order subscribers"""
        for callback in self._order_callbacks:
            try:
                callback(order)
            except Exception as e:
                self.logger.error(f"Error in order callback: {e}")
    
    def start_heartbeat(self) -> None:
        """Start heartbeat thread"""
        self._running = True
        self._ws_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._ws_thread.start()
    
    def stop_heartbeat(self) -> None:
        """Stop heartbeat thread"""
        self._running = False
        if self._ws_thread:
            self._ws_thread.join(timeout=5)
    
    def _heartbeat_loop(self) -> None:
        """Heartbeat loop"""
        while self._running:
            try:
                self._send_heartbeat()
            except Exception as e:
                self.logger.error(f"Heartbeat error: {e}")
            time.sleep(30)
    
    def _send_heartbeat(self) -> None:
        """Send heartbeat to broker"""
        pass


class MockBroker(BrokerBase):
    """Mock broker for testing and backtesting"""
    
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config or {})
        self.orders: Dict[str, Order] = {}
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.quotes: Dict[str, Quote] = {}
        self._order_id_counter = 1000
    
    def connect(self) -> bool:
        self._connected = True
        self.logger.info("Mock broker connected")
        return True
    
    def disconnect(self) -> bool:
        self._connected = False
        self.logger.info("Mock broker disconnected")
        return True
    
    def authenticate(self) -> bool:
        self.logger.info("Mock broker authenticated")
        return True
    
    def place_order(self, instrument: str, side: OrderSide, quantity: int,
                   order_type: OrderType, price: float = 0.0,
                   product_type: OrderProductType = OrderProductType.MIS) -> Order:
        
        order_id = f"MOCK-{self._order_id_counter}"
        self._order_id_counter += 1
        
        # Simulate market order fill at current price
        fill_price = price
        if order_type == OrderType.MARKET:
            quote = self.quotes.get(instrument)
            if quote:
                fill_price = quote.last_price
        
        order = Order(
            order_id=order_id,
            instrument=instrument,
            side=side,
            quantity=quantity,
            price=price,
            order_type=order_type,
            product_type=product_type,
            status=OrderStatus.FILLED,
            filled_quantity=quantity,
            average_price=fill_price,
            broker_order_id=order_id
        )
        
        self.orders[order_id] = order
        self.logger.info(f"Mock order placed: {order_id} {side.value} {quantity}@{fill_price}")
        
        return order
    
    def cancel_order(self, order_id: str) -> bool:
        if order_id in self.orders:
            order = self.orders[order_id]
            order.status = OrderStatus.CANCELLED
            order.updated_at = __import__('utils.timezone', fromlist=['now_ist']).now_ist()
            self.logger.info(f"Mock order cancelled: {order_id}")
            return True
        return False
    
    def modify_order(self, order_id: str, quantity: Optional[int] = None,
                   price: Optional[float] = None) -> Order:
        if order_id in self.orders:
            order = self.orders[order_id]
            if quantity:
                order.quantity = quantity
            if price:
                order.price = price
            order.updated_at = __import__('utils.timezone', fromlist=['now_ist']).now_ist()
            self.logger.info(f"Mock order modified: {order_id}")
            return order
        raise ValueError(f"Order not found: {order_id}")
    
    def get_order_status(self, order_id: str) -> Order:
        if order_id in self.orders:
            return self.orders[order_id]
        raise ValueError(f"Order not found: {order_id}")
    
    def get_positions(self) -> List[Dict[str, Any]]:
        return list(self.positions.values())
    
    def get_quote(self, instrument: str) -> Quote:
        if instrument in self.quotes:
            return self.quotes[instrument]
        
        # Return mock quote
        return Quote(
            instrument=instrument,
            last_price=100.0,
            bid_price=99.90,
            ask_price=100.10,
            volume=1000
        )
    
    def get_historical_candles(self, instrument: str, timeframe: str,
                              from_date: datetime, to_date: datetime) -> List[Candle]:
        """Get historical candles for backtesting and warm-up"""
        import random
        candles: List[Candle] = []
        current = from_date
        # Base realistic prices based on instrument
        if "NIFTY" in instrument:
            price = 24500.0
            volatility = 45.0
        elif "BANKNIFTY" in instrument:
            price = 51200.0
            volatility = 120.0
        elif "CRUDEOIL" in instrument:
            price = 6200.0
            volatility = 25.0
        else:
            price = 1500.0
            volatility = 10.0
            
        tf_delta = {
            "1m": timedelta(minutes=1),
            "5m": timedelta(minutes=5),
            "15m": timedelta(minutes=15),
            "1h": timedelta(hours=1),
            "1d": timedelta(days=1),
        }.get(timeframe, timedelta(days=1))
        
        while current <= to_date:
            # Skip weekends
            if current.weekday() < 5:
                # Up / down drift with mean reversion
                change = random.gauss(0.1, volatility)
                open_p = price
                close_p = max(1.0, round(open_p + change, 2))
                high_p = round(max(open_p, close_p) + abs(random.uniform(2.0, volatility * 0.8)), 2)
                low_p = round(min(open_p, close_p) - abs(random.uniform(2.0, volatility * 0.8)), 2)
                vol = random.randint(5000, 75000)
                
                candles.append(Candle(
                    instrument=instrument,
                    timeframe=timeframe,
                    open=open_p,
                    high=high_p,
                    low=low_p,
                    close=close_p,
                    volume=vol,
                    timestamp=current
                ))
                price = close_p
            current += tf_delta
        return candles

    def getHistoricalData(self, instrument: str, timeframe: str,
                         from_date: datetime, to_date: datetime) -> List[Dict[str, Any]]:
        candles = self.get_historical_candles(instrument, timeframe, from_date, to_date)
        return [{
            "timestamp": c.timestamp.isoformat(),
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume
        } for c in candles]
    
    def set_quote(self, instrument: str, price: float) -> None:
        """Set mock quote for testing"""
        self.quotes[instrument] = Quote(
            instrument=instrument,
            last_price=price,
            bid_price=price - 0.10,
            ask_price=price + 0.10,
            volume=10000
        )


class BrokerFactory:
    """Factory for creating broker instances"""
    
    _brokers = {
        'mock': MockBroker,
    }
    
    @classmethod
    def register(cls, name: str, broker_class: type) -> None:
        """Register a new broker"""
        cls._brokers[name.lower()] = broker_class
    
    @classmethod
    def create(cls, name: str, config: Dict[str, Any]) -> BrokerBase:
        """Create a broker instance"""
        name = name.lower()
        if name not in cls._brokers:
            raise ValueError(f"Unknown broker: {name}. Available: {list(cls._brokers.keys())}")
        return cls._brokers[name](config)
    
    @classmethod
    def available(cls) -> List[str]:
        """List available brokers"""
        return list(cls._brokers.keys())


# Auto-register built-in brokers
from .angel_one import AngelOneBroker
from .icici import ICICIBroker

BrokerFactory.register('angelone', AngelOneBroker)
BrokerFactory.register('icici', ICICIBroker)
