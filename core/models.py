"""Core data models for the trading platform"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
import uuid


# Enums
class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL_M"


class OrderStatus(Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class OrderProductType(Enum):
    MIS = "MIS"  # Intraday
    CNC = "CNC"  # Delivery
    NRML = "NRML"  # Normal/Carryforward


class PositionStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class TradeStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class StrategyStatus(Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class BacktestStatus(Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ExecutionMode(Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"
    BACKTEST = "BACKTEST"


# Data Models
@dataclass
class Instrument:
    """Trading instrument"""
    symbol: str
    exchange: str
    token: Optional[str] = None
    name: Optional[str] = None
    lot_size: int = 1
    tick_size: float = 0.01
    

@dataclass
class Quote:
    """Market quote"""
    instrument: str
    timestamp: datetime
    bid: float
    ask: float
    bid_qty: int = 0
    ask_qty: int = 0
    last: Optional[float] = None
    volume: int = 0


@dataclass
class Tick:
    """Market tick data"""
    instrument: str
    timestamp: datetime
    ltp: float
    volume: int = 0
    bid: Optional[float] = None
    ask: Optional[float] = None
    oi: Optional[int] = None
    oi_day_high: Optional[int] = None
    oi_day_low: Optional[int] = None


@dataclass
class Candle:
    """OHLCV candle data"""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    instrument: Optional[str] = None
    timeframe: Optional[str] = None
    open_interest: Optional[float] = None
    vwap: Optional[float] = None
    trades_count: Optional[int] = None
    provider: Optional[str] = None

    def to_dict(self):
        return {
            "timestamp": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "instrument": self.instrument,
            "timeframe": self.timeframe,
            "open_interest": self.open_interest,
            "vwap": self.vwap,
            "trades_count": self.trades_count,
            "provider": self.provider,
        }


@dataclass
class Order:
    """Order model"""
    order_id: str
    strategy_id: str
    instrument: str
    side: OrderSide
    quantity: int
    order_type: OrderType
    status: OrderStatus
    product_type: OrderProductType = OrderProductType.MIS
    price: float = 0.0
    trigger_price: float = 0.0
    filled_quantity: int = 0
    average_price: float = 0.0
    placed_time: Optional[datetime] = None
    updated_time: Optional[datetime] = None
    exchange_order_id: Optional[str] = None
    rejection_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return {
            "order_id": self.order_id,
            "strategy_id": self.strategy_id,
            "instrument": self.instrument,
            "side": self.side.value,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "status": self.status.value,
            "product_type": self.product_type.value,
            "price": self.price,
            "trigger_price": self.trigger_price,
            "filled_quantity": self.filled_quantity,
            "average_price": self.average_price,
            "placed_time": self.placed_time.isoformat() if self.placed_time else None,
            "updated_time": self.updated_time.isoformat() if self.updated_time else None,
            "exchange_order_id": self.exchange_order_id,
            "rejection_reason": self.rejection_reason,
        }


@dataclass
class Position:
    """Position model"""
    position_id: str
    strategy_id: str
    instrument: str
    quantity: int
    average_price: float
    status: PositionStatus
    side: OrderSide
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    opened_time: Optional[datetime] = None
    closed_time: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return {
            "position_id": self.position_id,
            "strategy_id": self.strategy_id,
            "instrument": self.instrument,
            "quantity": self.quantity,
            "average_price": self.average_price,
            "status": self.status.value,
            "side": self.side.value,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "opened_time": self.opened_time.isoformat() if self.opened_time else None,
            "closed_time": self.closed_time.isoformat() if self.closed_time else None,
        }


@dataclass
class Trade:
    """Trade model (closed position)"""
    trade_id: str
    strategy_id: str
    instrument: str
    quantity: int
    entry_price: float
    exit_price: float
    status: TradeStatus
    pnl: float = 0.0
    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None
    side: Optional[OrderSide] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return {
            "trade_id": self.trade_id,
            "strategy_id": self.strategy_id,
            "instrument": self.instrument,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "status": self.status.value,
            "pnl": self.pnl,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "side": self.side.value if self.side else None,
        }


@dataclass
class Signal:
    """Trading signal"""
    strategy_id: str
    instrument: str
    action: OrderSide
    quantity: int
    order_type: OrderType = OrderType.MARKET
    price: float = 0.0
    trigger_price: float = 0.0
    confidence: float = 1.0
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: Optional[datetime] = None
    stop_loss: Optional[float] = None
    target_price: Optional[float] = None


@dataclass
class StrategyConfig:
    """Strategy configuration"""
    strategy_id: str
    strategy_name: str
    instruments: List[str]
    capital: float
    parameters: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class StrategyState:
    """Strategy runtime state"""
    strategy_id: str
    status: StrategyStatus
    capital: float
    pnl: float = 0.0
    positions_count: int = 0
    trades_count: int = 0
    last_signal_time: Optional[datetime] = None
    error_message: Optional[str] = None


@dataclass
class BacktestResult:
    """Backtest result"""
    strategy_id: str
    start_time: datetime
    end_time: datetime
    initial_capital: float
    final_capital: float
    total_return: float
    total_return_pct: float
    max_drawdown: float
    sharpe_ratio: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    status: BacktestStatus
    error_message: Optional[str] = None
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)

    def __dict__(self):
        """For backward compatibility"""
        return {
            "strategy_id": self.strategy_id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "initial_capital": self.initial_capital,
            "final_capital": self.final_capital,
            "total_return": self.total_return,
            "total_return_pct": self.total_return_pct,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.win_rate,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "profit_factor": self.profit_factor,
            "status": self.status.value,
            "error_message": self.error_message,
        }


# ID Generators
def generate_order_id() -> str:
    """Generate unique order ID"""
    return f"ORD_{uuid.uuid4().hex[:8].upper()}"


def generate_trade_id() -> str:
    """Generate unique trade ID"""
    return f"TRD_{uuid.uuid4().hex[:8].upper()}"


def generate_position_id() -> str:
    """Generate unique position ID"""
    return f"POS_{uuid.uuid4().hex[:8].upper()}"
