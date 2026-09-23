"""Backtest Engine for Strategy Testing"""

import os
import time
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
import threading

from core.models import (
    BacktestResult, BacktestStatus, Trade, TradeStatus, Order, OrderSide,
    OrderType, OrderStatus, OrderProductType, Candle, Signal, generate_trade_id
)
from strategies import StrategyBase, StrategyRegistry
from persistence.journal import StateStore
from market_data.normalize import ensure_normalized_candles
from utils import Logger, get_timestamp
from market_data.normalize import normalize_timeframe


def _format_indian_currency(value: int) -> str:
    """Format a whole-rupee amount using the Indian comma grouping."""
    text = str(int(value))
    if len(text) <= 3:
        return text
    last = text[-3:]
    prefix = text[:-3]
    groups = []
    while prefix:
        groups.insert(0, prefix[-2:])
        prefix = prefix[:-2]
    return ",".join(groups + [last])


@dataclass
class BacktestConfig:
    """Backtest configuration"""
    strategy_id: str
    strategy_name: str
    strategy_params: Dict[str, Any] = field(default_factory=dict)
    instruments: List[str] = field(default_factory=list)
    start_date: datetime = field(default_factory=lambda: __import__('utils.timezone', fromlist=['now_ist']).now_ist() - timedelta(days=30))
    end_date: datetime = field(default_factory=lambda: __import__('utils.timezone', fromlist=['now_ist']).now_ist())
    initial_capital: float = 100000.0
    timeframe: str = "1d"
    fill_model: str = "INSTANT"  # INSTANT, TICK, CANDLE_CLOSE
    slippage_percent: float = 0.05
    commission_percent: float = 0.05  # Broker commission
    exchange_fee_percent: float = 0.001  # Exchange transaction fee

    def __post_init__(self) -> None:
        self.timeframe = normalize_timeframe(self.timeframe)


class BacktestEngine:
    """Backtest engine for strategy testing"""
    
    def __init__(self, config: BacktestConfig, data_provider: Any = None):
        self.config = config
        self.data_provider = data_provider
        self.logger = Logger(f"backtest.{config.strategy_id}")
        
        # State
        self._running = False
        self._paused = False
        self._cancelled = False
        
        # Results
        self.result = BacktestResult(
            strategy_id=config.strategy_id,
            start_time=config.start_date,
            end_time=config.end_date,
            initial_capital=config.initial_capital,
            final_capital=config.initial_capital,
            total_return=0,
            total_return_pct=0,
            max_drawdown=0,
            sharpe_ratio=0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0,
            avg_win=0,
            avg_loss=0,
            profit_factor=0,
            status=BacktestStatus.PENDING
        )
        
        # Runtime state
        self._capital = config.initial_capital
        self._positions: Dict[str, Dict[str, Any]] = {}
        self._trades: List[Trade] = []
        self._equity_curve: List[Dict[str, Any]] = []
        
        # Strategy instance
        self._strategy: Optional[StrategyBase] = None
        
        # Event callback for streaming
        self._event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None
        
        # Data cache
        self._candle_cache: Dict[str, List[Candle]] = {}
        
        # Candle builder for tick simulation
        self._tick_accumulator: Dict[str, List[Dict]] = {}
    
    def set_event_callback(self, callback: Callable[[str, Dict[str, Any]], None]) -> None:
        """Set event callback for streaming results"""
        self._event_callback = callback
    
    def _emit(self, event_type: str, payload: Dict[str, Any]):
        """Helper to send events to the callback"""
        if self._event_callback:
            self._event_callback(event_type, payload)

    def run(self) -> BacktestResult:
        """Run backtest"""
        self._running = True
        self.result.status = BacktestStatus.RUNNING
        
        try:
            self.logger.info(f"Starting backtest for {self.config.strategy_name}")
            self._emit("backtest_started", {"strategy_id": self.config.strategy_id, "strategy_name": self.config.strategy_name})
            
            # Create strategy
            self._strategy = StrategyRegistry.create(
                self.config.strategy_name,
                self.config.strategy_id,
                self.config.strategy_params
            )
            self._strategy.initialize()
            
            # Load historical data
            self._load_historical_data()
            self._emit("candles_loaded", {"instruments": self.config.instruments})
            
            # Run through each instrument
            for instrument in self.config.instruments:
                if self._cancelled:
                    break
                
                candles = self._candle_cache.get(instrument, [])
                if not candles:
                    continue
                
                # Process each candle
                for i, candle in enumerate(candles):
                    if self._cancelled:
                        break
                    
                    while self._paused:
                        time.sleep(0.1)
                    
                    # Generate signal
                    signal = self._strategy.on_candle(candle)
                    
                    if signal:
                        # Execute signal
                        self._execute_signal(signal, candle)
                    
                    # Update equity curve
                    self._update_equity(candle)
                    self._emit("equity_update", {
                        "point": self._equity_curve[-1],
                    })
                    
                    # Report progress
                    if self._event_callback and len(candles) > 0:
                        progress = (i + 1) / len(candles)
                        self._emit("progress", {"progress": progress * 100, "instrument": instrument})
            
            # Close remaining positions at end
            self._close_all_positions()
            
            # Calculate final metrics
            self._calculate_metrics()
            self._emit("metrics_update", {"metrics": self.result.__dict__()})
            
            self.result.status = BacktestStatus.COMPLETED
            # Full completion payload: BacktestResult.__dict__() is a summary
            # (no trades/equity_curve), so attach everything the UI needs to
            # render the equity curve, portfolio progression and trade fills
            # — mirroring the one-shot /api/backtest response shape.
            completion_result = self.result.__dict__()
            completion_result.update({
                "trades": [t.to_dict() for t in self._trades],
                "equity_curve": list(self._equity_curve),
                "candles_evaluated": sum(len(c) for c in self._candle_cache.values()),
                "period": (f"{self.config.start_date.strftime('%Y-%m-%d')} to "
                           f"{self.config.end_date.strftime('%Y-%m-%d')}"),
            })
            self._emit("backtest_completed", {"result": completion_result})
            self.logger.info(f"Backtest completed: {self.result.total_trades} trades, "
                           f"Return: {self.result.total_return_pct:.2f}%")
            
        except Exception as e:
            self.result.status = BacktestStatus.FAILED
            self.result.error_message = str(e)
            self._emit("backtest_failed", {"error": str(e)})
            self.logger.error(f"Backtest failed: {e}")
        
        self._running = False
        return self.result
    
    def run_async(self) -> threading.Thread:
        """Run backtest in background thread"""
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        return thread
    
    def pause(self) -> None:
        """Pause backtest"""
        self._paused = True
    
    def resume(self) -> None:
        """Resume backtest"""
        self._paused = False
    
    def cancel(self) -> None:
        """Cancel backtest"""
        self._cancelled = True
    
    def _load_historical_data(self) -> None:
        """Load historical data for backtest (normalized candles only).

        Provider responses are never consumed raw — every provider must
        return `market_data.normalize.NormalizedCandle` records; this gate
        re-validates/provenances them (and coerces legacy Candle/DataFrame
        responses defensively) before anything enters the backtest loop.
        """
        self.logger.info("Loading historical data...")

        # Try data provider first
        if self.data_provider:
            # Broker-backed providers must prove a live session BEFORE any
            # data touches the engine — even a cache hit. Auth failure ==
            # loud abort, never a backtest on stale/untrusted data.
            gate = getattr(self.data_provider, "ensure_authenticated", None)
            if callable(gate):
                gate()
            for instrument in self.config.instruments:
                response = self.data_provider.get_historical_candles(
                    instrument,
                    self.config.timeframe,
                    self.config.start_date,
                    self.config.end_date
                )
                candles = ensure_normalized_candles(
                    response,
                    context=f"instrument={instrument}, "
                            f"provider={getattr(self.data_provider, 'name', 'unknown')}",
                )
                if not candles:
                    raise RuntimeError(
                        f"Data provider "
                        f"'{getattr(self.data_provider, 'name', 'unknown')}' returned "
                        f"no usable candles for {instrument!r} "
                        f"({self.config.start_date}..{self.config.end_date} @ "
                        f"{self.config.timeframe}). Execution aborted — "
                        f"backtesting on fabricated data is not allowed."
                    )
                self._candle_cache[instrument] = [c.to_engine_candle() for c in candles]
                self.logger.info(
                    f"Loaded {len(candles)} normalized candles for {instrument}"
                )
            return
        # No provider: never fall back to synthetic prices — a backtest on
        # fabricated data is worse than a failed one (silently produced
        # meaningless trades/returns). Fail loudly instead.
        raise RuntimeError(
            "No data provider configured for backtest. Configure a valid "
            "data source (breeze / angel / yfinance) and retry."
        )

    def _execute_signal(self, signal, candle: Candle) -> None:
        """Execute trading signal with Long and Short derivative support"""
        from strategies.mcx_trend_rider import COMMODITY_SPECS
        from platform_config import get_index_lot_size, get_instrument

        # Get point value multiplier
        spec = COMMODITY_SPECS.get(signal.instrument, {'point_value': 1, 'lot_size': 1})
        point_multiplier = spec.get('point_value', 1)
        instrument_config = get_instrument(signal.instrument)
        is_index = bool(instrument_config and instrument_config.get("type") == "index")
        index_lot_size = get_index_lot_size(signal.instrument) if is_index else 1

        # Simulated fill price
        if signal.price > 0:
            fill_price = signal.price
        else:
            fill_price = self._get_fill_price(candle)
        if signal.metadata and 'exit_price' in signal.metadata:
            fill_price = float(signal.metadata['exit_price'])

        trade_val = fill_price * signal.quantity * point_multiplier
        commission = trade_val * (self.config.commission_percent / 100)
        exchange_fee = trade_val * (self.config.exchange_fee_percent / 100)
        slippage = trade_val * (self.config.slippage_percent / 100)
        total_cost = commission + exchange_fee + slippage

        existing_pos = self._positions.get(signal.instrument)

        if existing_pos and signal.action != existing_pos['side']:
            reason = (signal.metadata or {}).get('reason', '')
            if reason != 'partial_profit_take':
                signal.quantity = existing_pos['quantity']
            trade_val = fill_price * signal.quantity * point_multiplier
            commission = trade_val * (self.config.commission_percent / 100)
            exchange_fee = trade_val * (self.config.exchange_fee_percent / 100)
            slippage = trade_val * (self.config.slippage_percent / 100)
            total_cost = commission + exchange_fee + slippage

        # Strategy-specific risk sizing is useful for live execution, but a
        # backtest entry must never spend more than the current net cash. For
        # cash equities, round down to the exchange-friendly nearest five
        # shares (e.g. 666 becomes 665).
        if not existing_pos:
            unit_cost = fill_price * point_multiplier
            unit_cost += unit_cost * (
                (self.config.commission_percent +
                 self.config.exchange_fee_percent +
                 self.config.slippage_percent) / 100
            )
            affordable = int(self._capital // unit_cost) if unit_cost > 0 else 0
            if is_index:
                affordable = (affordable // index_lot_size) * index_lot_size
            elif point_multiplier == 1:
                affordable = (affordable // 5) * 5
            signal.quantity = affordable
            if signal.quantity <= 0:
                required_capital = unit_cost * index_lot_size if is_index else unit_cost
                rounded_required = math.ceil(required_capital / 5000) * 5000
                if is_index:
                    raise RuntimeError(
                        f"Minimum capital for {signal.instrument} required is "
                        f"{_format_indian_currency(rounded_required)} "
                        f"for one lot ({index_lot_size} units)"
                    )
                self.logger.warning(
                    f"Skipping {signal.action.value} {signal.instrument}: "
                    f"insufficient available capital for one trade unit"
                )
                return
            if self._strategy is not None:
                self._strategy.on_entry_fill(signal.instrument, signal.quantity, fill_price)
            trade_val = fill_price * signal.quantity * point_multiplier
            commission = trade_val * (self.config.commission_percent / 100)
            exchange_fee = trade_val * (self.config.exchange_fee_percent / 100)
            slippage = trade_val * (self.config.slippage_percent / 100)
            total_cost = commission + exchange_fee + slippage

        # 1. Closing an existing Long position
        if existing_pos and existing_pos['side'] == OrderSide.BUY and signal.action == OrderSide.SELL:
            close_qty = min(signal.quantity, existing_pos['quantity'])
            pnl = (fill_price - existing_pos['entry_price']) * close_qty * point_multiplier
            pnl -= total_cost
            
            # Emit trade exit event
            self._emit("trade_exit", {
                "instrument": signal.instrument,
                "side": "BUY",
                "entry_price": existing_pos['entry_price'],
                "exit_price": fill_price,
                "pnl": pnl,
                "duration": (candle.timestamp - existing_pos['entry_time']).total_seconds(),
                "quantity": close_qty,
                "reason": "signal_exit",
                "timestamp": candle.timestamp.isoformat(),
            })

            trade = Trade(
                trade_id=generate_trade_id(),
                strategy_id=self.config.strategy_id,
                instrument=signal.instrument,
                status=TradeStatus.CLOSED,
                entry_time=existing_pos['entry_time'],
                exit_time=candle.timestamp,
                quantity=close_qty,
                entry_price=existing_pos['entry_price'],
                exit_price=fill_price,
                pnl=pnl,
                side=existing_pos['side'],
            )
            self._trades.append(trade)
            self._capital += pnl
            if close_qty >= existing_pos['quantity']:
                del self._positions[signal.instrument]
            else:
                existing_pos['quantity'] -= close_qty
            return

        # 2. Closing an existing Short position
        if existing_pos and existing_pos['side'] == OrderSide.SELL and signal.action == OrderSide.BUY:
            pnl = (existing_pos['entry_price'] - fill_price) * existing_pos['quantity'] * point_multiplier
            pnl -= total_cost
            
            # Emit trade exit event
            self._emit("trade_exit", {
                "instrument": signal.instrument,
                "side": "SELL",
                "entry_price": existing_pos['entry_price'],
                "exit_price": fill_price,
                "pnl": pnl,
                "duration": (candle.timestamp - existing_pos['entry_time']).total_seconds(),
                "quantity": existing_pos['quantity'],
                "reason": "signal_exit",
                "timestamp": candle.timestamp.isoformat(),
            })

            trade = Trade(
                trade_id=generate_trade_id(),
                strategy_id=self.config.strategy_id,
                instrument=signal.instrument,
                status=TradeStatus.CLOSED,
                entry_time=existing_pos['entry_time'],
                exit_time=candle.timestamp,
                quantity=existing_pos['quantity'],
                entry_price=existing_pos['entry_price'],
                exit_price=fill_price,
                pnl=pnl,
                side=existing_pos['side'],
            )
            self._trades.append(trade)
            self._capital += pnl
            del self._positions[signal.instrument]
            return

        # 3. Opening new Long position
        if signal.action == OrderSide.BUY and not existing_pos:
            # Emit trade entry event
            self._emit("trade_entry", {
                "instrument": signal.instrument,
                "side": "BUY",
                "entry_price": fill_price,
                "quantity": signal.quantity,
                "reason": "strategy_signal",
                "timestamp": candle.timestamp.isoformat()
            })
            self._positions[signal.instrument] = {
                'quantity': signal.quantity,
                'entry_price': fill_price,
                'entry_time': candle.timestamp,
                'side': OrderSide.BUY,
                'sl': signal.stop_loss,
                'target': signal.target_price,
                'point_multiplier': point_multiplier
            }
            self._capital -= total_cost
            return

        # 4. Opening new Short position
        if signal.action == OrderSide.SELL and not existing_pos:
            # Emit trade entry event
            self._emit("trade_entry", {
                "instrument": signal.instrument,
                "side": "SELL",
                "entry_price": fill_price,
                "quantity": signal.quantity,
                "reason": "strategy_signal",
                "timestamp": candle.timestamp.isoformat()
            })
            self._positions[signal.instrument] = {
                'quantity': signal.quantity,
                'entry_price': fill_price,
                'entry_time': candle.timestamp,
                'side': OrderSide.SELL,
                'sl': signal.stop_loss,
                'target': signal.target_price,
                'point_multiplier': point_multiplier
            }
            self._capital -= total_cost
            return
    
    def _get_fill_price(self, candle: Candle) -> float:
        """Get fill price based on fill model"""
        if self.config.fill_model == "INSTANT":
            return candle.close
        elif self.config.fill_model == "CANDLE_CLOSE":
            return candle.close
        elif self.config.fill_model == "TICK":
            # Use candle open with some slippage
            return candle.open * (1 + self.config.slippage_percent / 100)
        return candle.close
    
    def _close_all_positions(self) -> None:
        """Close all positions at end of backtest"""
        for instrument, pos in list(self._positions.items()):
            # Use last candle close as exit price
            candles = self._candle_cache.get(instrument, [])
            if candles:
                exit_price = candles[-1].close
                action = (OrderSide.SELL if pos['side'] == OrderSide.BUY
                          else OrderSide.BUY)
                self._execute_signal(
                    Signal(
                        strategy_id=self.config.strategy_id,
                        instrument=instrument,
                        action=action,
                        quantity=pos['quantity'],
                        order_type=OrderType.MARKET,
                        price=exit_price,
                        metadata={'reason': 'backtest_end', 'exit_price': exit_price},
                    ),
                    candles[-1],
                )
        
        self._positions = {}
    
    def _update_equity(self, candle: Candle) -> None:
        """Update equity curve"""
        # Calculate unrealized P&L
        unrealized = 0
        for instrument, pos in self._positions.items():
            if instrument == candle.instrument:
                unrealized += (candle.close - pos['entry_price']) * pos['quantity']
        
        total_equity = self._capital + unrealized
        
        self._equity_curve.append({
            'timestamp': candle.timestamp.isoformat(),
            'capital': self._capital,
            'unrealized_pnl': unrealized,
            'total_equity': total_equity
        })
    
    def _calculate_metrics(self) -> None:
        """Calculate backtest metrics"""
        self.result.trades = self._trades
        self.result.equity_curve = self._equity_curve
        
        # Final capital
        self.result.final_capital = self._capital
        self.result.total_return = self._capital - self.config.initial_capital
        self.result.total_return_pct = (self.result.total_return / self.config.initial_capital) * 100
        
        # Trade statistics
        self.result.total_trades = len(self._trades)
        
        if self._trades:
            winning = [t for t in self._trades if t.pnl > 0]
            losing = [t for t in self._trades if t.pnl <= 0]
            
            self.result.winning_trades = len(winning)
            self.result.losing_trades = len(losing)
            self.result.win_rate = (len(winning) / len(self._trades)) * 100 if self._trades else 0
            
            if winning:
                self.result.avg_win = sum(t.pnl for t in winning) / len(winning)
            if losing:
                self.result.avg_loss = sum(t.pnl for t in losing) / len(losing)
            
            # Profit factor
            gross_profit = sum(t.pnl for t in winning) if winning else 0
            gross_loss = abs(sum(t.pnl for t in losing)) if losing else 1
            self.result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        # Max drawdown
        if self._equity_curve:
            peak = self._equity_curve[0]['total_equity']
            max_dd = 0
            
            for point in self._equity_curve:
                equity = point['total_equity']
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak * 100 if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd
            
            self.result.max_drawdown = max_dd
        
        # Sharpe ratio (simplified)
        if len(self._equity_curve) > 1:
            returns = []
            for i in range(1, len(self._equity_curve)):
                ret = (self._equity_curve[i]['total_equity'] - 
                       self._equity_curve[i-1]['total_equity']) / self._equity_curve[i-1]['total_equity']
                returns.append(ret)
            
            if returns:
                import statistics
                avg_return = statistics.mean(returns)
                std_return = statistics.stdev(returns) if len(returns) > 1 else 0.01
                self.result.sharpe_ratio = (avg_return / std_return * (252 ** 0.5)) if std_return > 0 else 0


class BacktestManager:
    """Manages multiple backtests"""
    
    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir or "data/backtests"
        self.logger = Logger("backtest-manager")
        
        # Active backtests
        self._backtests: Dict[str, BacktestEngine] = {}
        
        # Completed results
        self._results: Dict[str, BacktestResult] = {}
    
    def run_backtest(self, config: BacktestConfig, 
                    data_provider: Any = None) -> BacktestEngine:
        """Run a new backtest"""
        engine = BacktestEngine(config, data_provider)
        self._backtests[config.strategy_id] = engine
        
        # Run and store result
        result = engine.run()
        self._results[config.strategy_id] = result
        
        return engine
    
    def run_backtest_async(self, config: BacktestConfig,
                           data_provider: Any = None) -> str:
        """Run backtest asynchronously, returns ID"""
        engine = BacktestEngine(config, data_provider)
        self._backtests[config.strategy_id] = engine
        engine.run_async()
        
        return config.strategy_id
    
    def get_result(self, strategy_id: str) -> Optional[BacktestResult]:
        """Get backtest result"""
        return self._results.get(strategy_id)
    
    def get_running(self, strategy_id: str) -> Optional[BacktestEngine]:
        """Get running backtest"""
        return self._backtests.get(strategy_id)
    
    def cancel(self, strategy_id: str) -> bool:
        """Cancel a backtest"""
        if strategy_id in self._backtests:
            self._backtests[strategy_id].cancel()
            return True
        return False
    
    def list_results(self) -> List[BacktestResult]:
        """List all backtest results"""
        return list(self._results.values())
