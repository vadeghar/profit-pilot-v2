"""Strategy framework for Trading Platform"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from core.models import (
    Signal, OrderSide, OrderType, Candle, Tick, Quote
)
from utils import Logger


class StrategyBase(ABC):
    """Abstract base class for trading strategies"""
    
    def __init__(self, strategy_id: str, name: str, params: Dict[str, Any] = None):
        self.strategy_id = strategy_id
        self.name = name
        self.params = params or {}
        self.logger = Logger(f"strategy.{strategy_id}")
        
        # State
        self._running = False
        self._signals: List[Signal] = []
        self._data: Dict[str, Any] = {}
        
        # Indicators storage
        self._indicators: Dict[str, Any] = {}
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @abstractmethod
    def on_tick(self, tick: Tick) -> Optional[Signal]:
        """Process tick and return signal if any"""
        pass
    
    @abstractmethod
    def on_candle(self, candle: Candle) -> Optional[Signal]:
        """Process candle and return signal if any"""
        pass
    
    def on_bar(self, candle: Candle) -> Optional[Signal]:
        """Alias for on_candle"""
        return self.on_candle(candle)
    
    def initialize(self) -> None:
        """Initialize strategy - called before starting"""
        self.logger.info(f"Initializing strategy: {self.name}")
        self._init_indicators()
    
    def _init_indicators(self) -> None:
        """Initialize indicators - override in subclass"""
        pass
    
    def start(self) -> None:
        """Start strategy"""
        self._running = True
        self.logger.info(f"Strategy started: {self.name}")
    
    def stop(self) -> None:
        """Stop strategy"""
        self._running = False
        self.logger.info(f"Strategy stopped: {self.name}")
    
    def reset(self) -> None:
        """Reset strategy state"""
        self._signals = []
        self._data = {}
        self._indicators = {}
    
    def add_signal(self, signal: Signal) -> None:
        """Add a trading signal"""
        self._signals.append(signal)
    
    def get_signals(self) -> List[Signal]:
        """Get all signals"""
        return self._signals.copy()
    
    def clear_signals(self) -> None:
        """Clear all signals"""
        self._signals = []
    
    # Indicator helpers
    def sma(self, period: int, data: List[float]) -> Optional[float]:
        """Simple Moving Average"""
        if len(data) < period:
            return None
        return sum(data[-period:]) / period
    
    def ema(self, period: int, data: List[float], prev_ema: float = None) -> float:
        """Exponential Moving Average"""
        if len(data) < period:
            return prev_ema or 0
        
        multiplier = 2 / (period + 1)
        
        if prev_ema is None:
            # First EMA is SMA
            return self.sma(period, data)
        
        ema = prev_ema
        for price in data:
            ema = (price - ema) * multiplier + ema
        return ema
    
    def rsi(self, period: int, data: List[float]) -> Optional[float]:
        """Relative Strength Index"""
        if len(data) < period + 1:
            return None
        
        gains = []
        losses = []
        
        for i in range(1, len(data)):
            change = data[i] - data[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        
        if len(gains) < period:
            return None
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def bb(self, period: int, std_dev: float, data: List[float]) -> Dict[str, float]:
        """Bollinger Bands"""
        if len(data) < period:
            return {'upper': 0, 'middle': 0, 'lower': 0}
        
        middle = self.sma(period, data)
        
        # Calculate standard deviation
        subset = data[-period:]
        variance = sum((x - middle) ** 2 for x in subset) / period
        std = variance ** 0.5
        
        return {
            'upper': middle + (std_dev * std),
            'middle': middle,
            'lower': middle - (std_dev * std)
        }
    
    def atr(self, period: int, candles: List[Candle]) -> Optional[float]:
        """Average True Range"""
        if len(candles) < period + 1:
            return None
        
        tr_values = []
        for i in range(1, len(candles)):
            high = candles[i].high
            low = candles[i].low
            prev_close = candles[i-1].close
            
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            tr_values.append(tr)
        
        if len(tr_values) < period:
            return None
        
        return sum(tr_values[-period:]) / period
    
    def store_indicator(self, name: str, value: Any) -> None:
        """Store indicator value"""
        self._indicators[name] = value
    
    def get_indicator(self, name: str) -> Any:
        """Get indicator value"""
        return self._indicators.get(name)


class StrategyRegistry:
    """Registry for available strategies"""
    
    _strategies: Dict[str, type] = {}
    
    @classmethod
    def register(cls, name: str, strategy_class: type) -> None:
        """Register a strategy"""
        cls._strategies[name.lower()] = strategy_class
    
    @classmethod
    def get(cls, name: str) -> type:
        """Get strategy class by name"""
        name = name.lower()
        if name not in cls._strategies:
            raise ValueError(f"Unknown strategy: {name}. Available: {list(cls._strategies.keys())}")
        return cls._strategies[name]
    
    @classmethod
    def list_strategies(cls) -> List[str]:
        """List all available strategies"""
        return list(cls._strategies.keys())
    
    @classmethod
    def create(cls, name: str, strategy_id: str, params: Dict[str, Any] = None) -> StrategyBase:
        """Create a strategy instance"""
        strategy_class = cls.get(name)
        return strategy_class(strategy_id, name, params)


# Sample strategies
class EMACrossover(StrategyBase):
    """EMA Crossover Strategy"""
    
    def _init_indicators(self) -> None:
        self.fast_period = self.params.get('fast_period', 9)
        self.slow_period = self.params.get('slow_period', 21)
        self._price_history: List[float] = []
        self._fast_ema: float = 0
        self._slow_ema: float = 0
    
    def on_tick(self, tick: Tick) -> Optional[Signal]:
        self._price_history.append(tick.last_price)
        return self._check_crossover()
    
    def on_candle(self, candle: Candle) -> Optional[Signal]:
        self._current_instrument = getattr(candle, 'instrument', self.params.get('instrument', _DEFAULT_NIFTY))
        self._price_history.append(candle.close)
        return self._check_crossover()
    
    def _check_crossover(self) -> Optional[Signal]:
        if len(self._price_history) < self.slow_period:
            return None
        
        self._fast_ema = self.ema(self.fast_period, self._price_history, self._fast_ema)
        self._slow_ema = self.ema(self.slow_period, self._price_history, self._slow_ema)
        
        if self._fast_ema and self._slow_ema:
            # Store for debugging
            self.store_indicator('fast_ema', self._fast_ema)
            self.store_indicator('slow_ema', self._slow_ema)
            
            # Check crossover
            prev_fast = self._price_history[-2] if len(self._price_history) >= 2 else self._fast_ema
            prev_slow = self._price_history[-2] if len(self._price_history) >= 2 else self._slow_ema
            
            inst = getattr(self, '_current_instrument', self.params.get('instrument', _DEFAULT_NIFTY))
            if prev_fast <= prev_slow and self._fast_ema > self._slow_ema:
                # Golden cross - BUY
                return Signal(
                    strategy_id=self.strategy_id,
                    instrument=inst,
                    action=OrderSide.BUY,
                    quantity=self.params.get('quantity', 1),
                    order_type=OrderType.MARKET,
                    metadata={'reason': 'golden_cross', 'fast': self._fast_ema, 'slow': self._slow_ema}
                )
            elif prev_fast >= prev_slow and self._fast_ema < self._slow_ema:
                # Death cross - SELL
                return Signal(
                    strategy_id=self.strategy_id,
                    instrument=inst,
                    action=OrderSide.SELL,
                    quantity=self.params.get('quantity', 1),
                    order_type=OrderType.MARKET,
                    metadata={'reason': 'death_cross', 'fast': self._fast_ema, 'slow': self._slow_ema}
                )
        
        return None


class RSIStrategy(StrategyBase):
    """RSI Reversal Strategy"""
    
    def _init_indicators(self) -> None:
        self.period = self.params.get('period', 14)
        self.oversold = self.params.get('oversold', 30)
        self.overbought = self.params.get('overbought', 70)
        self._price_history: List[float] = []
    
    def on_tick(self, tick: Tick) -> Optional[Signal]:
        self._price_history.append(tick.last_price)
        return self._check_rsi()
    
    def on_candle(self, candle: Candle) -> Optional[Signal]:
        self._current_instrument = getattr(candle, 'instrument', self.params.get('instrument', _DEFAULT_RSI))
        self._price_history.append(candle.close)
        return self._check_rsi()
    
    def _check_rsi(self) -> Optional[Signal]:
        rsi = self.rsi(self.period, self._price_history)
        if rsi is None:
            return None
        
        self.store_indicator('rsi', rsi)
        
        inst = getattr(self, '_current_instrument', self.params.get('instrument', _DEFAULT_RSI))
        if rsi < self.oversold:
            return Signal(
                strategy_id=self.strategy_id,
                instrument=inst,
                action=OrderSide.BUY,
                quantity=self.params.get('quantity', 1),
                order_type=OrderType.MARKET,
                metadata={'reason': 'rsi_oversold', 'rsi': rsi}
            )
        elif rsi > self.overbought:
            return Signal(
                strategy_id=self.strategy_id,
                instrument=inst,
                action=OrderSide.SELL,
                quantity=self.params.get('quantity', 1),
                order_type=OrderType.MARKET,
                metadata={'reason': 'rsi_overbought', 'rsi': rsi}
            )
        
        return None


class BreakoutStrategy(StrategyBase):
    """Price Breakout Strategy"""
    
    def _init_indicators(self) -> None:
        self.lookback = self.params.get('lookback', 20)
        self._highs: List[float] = []
        self._lows: List[float] = []
    
    def on_tick(self, tick: Tick) -> Optional[Signal]:
        return None  # Use candles for breakout
    
    def on_candle(self, candle: Candle) -> Optional[Signal]:
        self._highs.append(candle.high)
        self._lows.append(candle.low)
        
        if len(self._highs) < self.lookback:
            return None
        
        highest = max(self._highs[-self.lookback:-1])
        lowest = min(self._lows[-self.lookback:-1])
        
        # Store for debugging
        self.store_indicator('highest', highest)
        self.store_indicator('lowest', lowest)
        
        inst = getattr(candle, 'instrument', self.params.get('instrument', _DEFAULT_BREAKOUT))
        if candle.close > highest:
            return Signal(
                strategy_id=self.strategy_id,
                instrument=inst,
                action=OrderSide.BUY,
                quantity=self.params.get('quantity', 1),
                order_type=OrderType.MARKET,
                metadata={'reason': 'breakout_up', 'break_level': highest}
            )
        elif candle.close < lowest:
            return Signal(
                strategy_id=self.strategy_id,
                instrument=inst,
                action=OrderSide.SELL,
                quantity=self.params.get('quantity', 1),
                order_type=OrderType.MARKET,
                metadata={'reason': 'breakout_down', 'break_level': lowest}
            )
        
        return None


# Register built-in strategies
StrategyRegistry.register('ema_crossover', EMACrossover)
StrategyRegistry.register('rsi', RSIStrategy)
StrategyRegistry.register('breakout', BreakoutStrategy)

# Register MCX Trend Rider
try:
    from strategies.mcx_trend_rider import MCXTrendRiderStrategy
    StrategyRegistry.register('mcx_trend_rider', MCXTrendRiderStrategy)
except ImportError:
    pass

# Register Equity Swing VCP Strategy
try:
    from strategies.equity_swing_vcp import EquitySwingVCPStrategy
    StrategyRegistry.register('equity_swing_vcp', EquitySwingVCPStrategy)
except ImportError:
    pass

# Register Index OI Momentum Strategy
try:
    from strategies.index_oi_momentum import IndexOIMomentumStrategy
    StrategyRegistry.register('index_oi_momentum', IndexOIMomentumStrategy)
except ImportError:
    pass

# Register Lorentzian Classification ML Strategy (Breeze-fed OHLC pipeline)
try:
    from strategies.lorentzian_ml import LorentzianMLStrategy
    StrategyRegistry.register('lorentzian_ml', LorentzianMLStrategy)
    StrategyRegistry.register('lorentzian', LorentzianMLStrategy)
except ImportError:
    pass
