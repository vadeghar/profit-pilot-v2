"""Strategy framework: the base class every candle/tick strategy extends, and the registry
the backtest engine, CLI and dashboard create strategies from.

Strategies live in the strategy packages (``trading_strategies``, ``investment_strategies``), each of
which registers its classes on import; the registry imports those packages the first time it is asked
for a strategy, so callers never depend on import order. The tick scalpers in ``scalp_strategies``
use their own engine and registry (``SCALP_STRATEGIES``).
"""

import importlib
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from core.models import Candle, Signal, Tick
from utils import Logger

# Packages whose import registers strategies with StrategyRegistry.
STRATEGY_PACKAGES = ("trading_strategies", "investment_strategies")


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

    def on_entry_fill(self, instrument: str, quantity: int, price: float) -> None:
        """Synchronize strategy bookkeeping with a backtest's filled quantity."""
        return None
    
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
    _loaded = False

    @classmethod
    def _load_packages(cls) -> None:
        """Import the strategy packages once so their strategies are registered."""
        if cls._loaded:
            return
        cls._loaded = True
        for package in STRATEGY_PACKAGES:
            importlib.import_module(package)

    @classmethod
    def register(cls, name: str, strategy_class: type) -> None:
        """Register a strategy"""
        cls._strategies[name.lower()] = strategy_class
    
    @classmethod
    def get(cls, name: str) -> type:
        """Get strategy class by name"""
        cls._load_packages()
        name = name.lower()
        if name not in cls._strategies:
            raise ValueError(f"Unknown strategy: {name}. Available: {list(cls._strategies.keys())}")
        return cls._strategies[name]
    
    @classmethod
    def list_strategies(cls) -> List[str]:
        """List all available strategies"""
        cls._load_packages()
        return list(cls._strategies.keys())
    
    @classmethod
    def create(cls, name: str, strategy_id: str, params: Dict[str, Any] = None) -> StrategyBase:
        """Create a strategy instance"""
        strategy_class = cls.get(name)
        return strategy_class(strategy_id, name, params)
