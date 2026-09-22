"""Market Data module with WebSocket support and Candle Builder"""

import asyncio
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field

from core.models import Candle, Quote, Tick
from utils import Logger, get_timestamp


class CandleBuilder:
    """Build OHLCV candles from tick data in real-time"""
    
    def __init__(self, timeframe: str = "1m"):
        self.timeframe = timeframe
        self._timeframe_seconds = self._parse_timeframe(timeframe)
        self._ticks: Dict[str, List[Tick]] = defaultdict(list)
        self._current_candles: Dict[str, Candle] = {}
        self._callbacks: List[Callable[[Candle], None]] = []
    
    def _parse_timeframe(self, tf: str) -> int:
        """Parse timeframe string to seconds"""
        mapping = {
            "1m": 60, "3m": 180, "5m": 300, "10m": 600, "15m": 900,
            "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400
        }
        return mapping.get(tf, 60)
    
    def _get_candle_open_time(self, timestamp: datetime) -> datetime:
        """Get candle open timestamp (IST bucket)."""
        from utils.timezone import IST, ensure_ist
        ist = ensure_ist(timestamp)
        # bucket on IST wall-clock so 09:15/15:30 session edges align
        base = ist.replace(hour=0, minute=0, second=0, microsecond=0)
        secs = int((ist - base).total_seconds()) // self._timeframe_seconds * self._timeframe_seconds
        from datetime import timedelta as _td
        return base + _td(seconds=secs)
    
    def on_tick(self, tick: Tick) -> None:
        """Process incoming tick"""
        instrument = tick.instrument
        candle_time = self._get_candle_open_time(tick.timestamp)
        
        # Get or create current candle
        if instrument not in self._current_candles:
            self._current_candles[instrument] = Candle(
                instrument=instrument,
                timeframe=self.timeframe,
                open=tick.last_price,
                high=tick.last_price,
                low=tick.last_price,
                close=tick.last_price,
                volume=tick.volume,
                timestamp=candle_time
            )
        
        candle = self._current_candles[instrument]
        
        # Check if we need a new candle
        if candle.timestamp < candle_time:
            # Emit old candle
            self._emit_candle(candle)
            
            # Start new candle
            self._current_candles[instrument] = Candle(
                instrument=instrument,
                timeframe=self.timeframe,
                open=tick.last_price,
                high=tick.last_price,
                low=tick.last_price,
                close=tick.last_price,
                volume=tick.volume,
                timestamp=candle_time
            )
            return
        
        # Update existing candle
        candle.high = max(candle.high, tick.last_price)
        candle.low = min(candle.low, tick.last_price)
        candle.close = tick.last_price
        candle.volume = tick.volume
    
    def _emit_candle(self, candle: Candle) -> None:
        """Emit completed candle to callbacks"""
        for callback in self._callbacks:
            try:
                callback(candle)
            except Exception as e:
                pass  # Don't let callback errors break the builder
    
    def on_candle(self, callback: Callable[[Candle], None]) -> None:
        """Register callback for completed candles"""
        self._callbacks.append(callback)
    
    def get_current_candle(self, instrument: str) -> Optional[Candle]:
        """Get current (incomplete) candle"""
        return self._current_candles.get(instrument)
    
    def get_historical_candles(self, broker: Any, instrument: str, 
                               from_date: datetime, to_date: datetime) -> List[Candle]:
        """Get historical candles from broker"""
        data = broker.getHistoricalData(instrument, self.timeframe, from_date, to_date)
        candles = []
        for d in data:
            candles.append(Candle(
                instrument=instrument,
                timeframe=self.timeframe,
                open=d['open'],
                high=d['high'],
                low=d['low'],
                close=d['close'],
                volume=d['volume'],
                timestamp=datetime.fromisoformat(d['timestamp'])
            ))
        return candles


class TickAggregator:
    """Aggregate ticks into time-based windows"""
    
    def __init__(self, window_seconds: int = 60):
        self.window_seconds = window_seconds
        self._ticks: Dict[str, List[Tick]] = defaultdict(list)
        self._callbacks: List[Callable[[List[Tick]], None]] = []
        self._last_emit: Dict[str, datetime] = {}
    
    def on_tick(self, tick: Tick) -> None:
        """Process tick"""
        instrument = tick.instrument
        self._ticks[instrument].append(tick)
        
        # Check if we should emit
        now = __import__('utils.timezone', fromlist=['now_ist']).now_ist()
        last_emit = self._last_emit.get(instrument)
        
        if last_emit is None or (now - last_emit).total_seconds() >= self.window_seconds:
            self._emit(instrument)
    
    def _emit(self, instrument: str) -> None:
        """Emit aggregated ticks"""
        if instrument not in self._ticks:
            return
        
        ticks = self._ticks[instrument]
        if ticks:
            for callback in self._callbacks:
                try:
                    callback(ticks)
                except Exception:
                    pass
            
            self._ticks[instrument] = []
            self._last_emit[instrument] = __import__('utils.timezone', fromlist=['now_ist']).now_ist()
    
    def on_aggregate(self, callback: Callable[[List[Tick]], None]) -> None:
        """Register callback for aggregated ticks"""
        self._callbacks.append(callback)


class MarketDataManager:
    """Manages market data subscriptions and distribution"""
    
    def __init__(self, broker: Any):
        self.broker = broker
        self.logger = Logger("market-data")
        self._candle_builders: Dict[str, CandleBuilder] = {}
        self._subscribers: Dict[str, List[Callable[[Tick], None]]] = defaultdict(list)
        self._running = False
        self._thread: Optional[threading.Thread] = None
    
    def subscribe(self, instrument: str, callback: Callable[[Tick], None]) -> None:
        """Subscribe to tick data for an instrument"""
        self._subscribers[instrument].append(callback)
        self.broker.subscribe_ticks([instrument])
        self.logger.info(f"Subscribed to {instrument}")
    
    def unsubscribe(self, instrument: str, callback: Callable[[Tick], None]) -> None:
        """Unsubscribe from tick data"""
        if instrument in self._subscribers:
            self._subscribers[instrument].remove(callback)
            if not self._subscribers[instrument]:
                self.broker.unsubscribe_ticks([instrument])
                del self._subscribers[instrument]
    
    def get_candle_builder(self, timeframe: str = "1m") -> CandleBuilder:
        """Get or create candle builder for timeframe"""
        if timeframe not in self._candle_builders:
            self._candle_builders[timeframe] = CandleBuilder(timeframe)
        return self._candle_builders[timeframe]
    
    def start(self) -> None:
        """Start market data manager"""
        self._running = True
        
        # Register broker tick callback
        def on_tick(tick: Tick):
            # Distribute to subscribers
            for callback in self._subscribers.get(tick.instrument, []):
                try:
                    callback(tick)
                except Exception as e:
                    self.logger.error(f"Error in tick callback: {e}")
            
            # Update candle builders
            for builder in self._candle_builders.values():
                builder.on_tick(tick)
        
        self.broker.on_tick(on_tick)
        self.logger.info("Market data manager started")
    
    def stop(self) -> None:
        """Stop market data manager"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        self.logger.info("Market data manager stopped")
    
    def get_quote(self, instrument: str) -> Quote:
        """Get current quote"""
        return self.broker.get_quote(instrument)
    
    def get_historical_candles(self, instrument: str, timeframe: str,
                               from_date: datetime, to_date: datetime) -> List[Candle]:
        """Get historical candles"""
        builder = self.get_candle_builder(timeframe)
        return builder.get_historical_candles(self.broker, instrument, from_date, to_date)


class DataFeedHealthMonitor:
    """Monitor data feed health and detect stale feeds"""
    
    def __init__(self, stale_threshold_seconds: int = 10):
        self.stale_threshold_seconds = stale_threshold_seconds
        self._last_ticks: Dict[str, datetime] = {}
        self._callbacks: List[Callable[[str, bool], None]] = []  # instrument, is_stale
        self._running = False
        self._thread: Optional[threading.Thread] = None
    
    def on_tick(self, tick: Tick) -> None:
        """Record tick timestamp"""
        self._last_ticks[tick.instrument] = tick.timestamp
    
    def start(self) -> None:
        """Start health monitor"""
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
    
    def stop(self) -> None:
        """Stop health monitor"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
    
    def _monitor_loop(self) -> None:
        """Monitor loop"""
        while self._running:
            now = __import__('utils.timezone', fromlist=['now_ist']).now_ist()
            
            for instrument, last_tick in list(self._last_ticks.items()):
                is_stale = (now - last_tick).total_seconds() > self.stale_threshold_seconds
                for callback in self._callbacks:
                    try:
                        callback(instrument, is_stale)
                    except Exception:
                        pass
            
            time.sleep(1)
    
    def on_status_change(self, callback: Callable[[str, bool], None]) -> None:
        """Register status change callback"""
        self._callbacks.append(callback)
    
    def is_stale(self, instrument: str) -> bool:
        """Check if instrument feed is stale"""
        if instrument not in self._last_ticks:
            return True
        
        now = __import__('utils.timezone', fromlist=['now_ist']).now_ist()
        return (now - self._last_ticks[instrument]).total_seconds() > self.stale_threshold_seconds


# Export factory for convenient imports
from .factory import ProviderFactory

__all__ = [
    'CandleBuilder',
    'TickAggregator',
    'MarketDataManager',
    'ProviderFactory',
]
