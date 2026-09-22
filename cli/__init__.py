"""Command-line interface for Trading Platform"""

import argparse
import asyncio
import json
import os
import sys
import signal
import socket
import threading
from datetime import datetime, timedelta
from utils.timezone import IST as _CLI_IST, ensure_ist as _cli_ensure_ist
from pathlib import Path
from typing import Any, Dict, List, Optional
import time

import platform_config as config
from brokers import BrokerFactory, MockBroker
from market_data import MarketDataManager, CandleBuilder
from execution import ExecutionEngine, RiskManager, ExecutionPolicy
from strategies import StrategyRegistry, StrategyBase
from persistence.journal import StateStore
from core.models import (
    StrategyConfig, StrategyState, StrategyStatus, ExecutionMode,
    OrderSide, OrderType, OrderProductType, BacktestResult, BacktestStatus
)
from backtest import BacktestEngine, BacktestConfig, BacktestManager
from utils import Logger, format_inr, format_pnl, get_timestamp, ensure_dir


class PlatformCLI:
    """Main CLI for Trading Platform"""
    
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.logger = Logger("cli")
        
        # Ensure data directories
        ensure_dir(f"{data_dir}/state")
        ensure_dir(f"{data_dir}/journals")
        ensure_dir(f"{data_dir}/backtests")
        ensure_dir(f"{data_dir}/logs")
        
        # State store
        self.state_store = StateStore(f"{data_dir}/state")
        
        # Components (initialized on start)
        self.broker = None
        self.market_data = None
        self.execution_engine = None
        self.risk_manager = None
        self.backtest_manager = None
        
        # Runtime state
        self._running_strategies: Dict[str, StrategyBase] = {}
        self._engine_thread: Optional[threading.Thread] = None
        self._socket_path = f"/tmp/trading_platform.sock"
        
        # Parse commands
        self._parse_args()
    
    def _parse_args(self) -> None:
        """Parse command line arguments"""
        parser = argparse.ArgumentParser(
            prog='trading-platform',
            description='Trading Strategy Execution Platform'
        )
        
        subparsers = parser.add_subparsers(dest='command', help='Commands')
        
        # Start command
        start_parser = subparsers.add_parser('start', help='Start the platform')
        start_parser.add_argument('--broker', default='mock', help='Broker to use')
        start_parser.add_argument('--config', help='Config file path')
        
        # Stop command
        subparsers.add_parser('stop', help='Stop the platform')
        
        # Strategy commands
        strategy_parser = subparsers.add_parser('strategy', help='Strategy management')
        strategy_sub = strategy_parser.add_subparsers(dest='strategy_action')
        
        # List strategies
        strategy_sub.add_parser('list', help='List available strategies')
        
        # Start strategy
        start_strat = strategy_sub.add_parser('start', help='Start a strategy')
        start_strat.add_argument('strategy_id', help='Strategy ID or name')
        start_strat.add_argument('--instrument', help='Trading instrument')
        start_strat.add_argument('--params', help='Strategy parameters (JSON)')
        
        # Stop strategy
        stop_strat = strategy_sub.add_parser('stop', help='Stop a strategy')
        stop_strat.add_argument('strategy_id', help='Strategy ID')
        
        # Status strategy
        status_strat = strategy_sub.add_parser('status', help='Strategy status')
        status_strat.add_argument('strategy_id', help='Strategy ID')
        
        # Backtest command
        backtest_parser = subparsers.add_parser('backtest', help='Run backtest')
        backtest_parser.add_argument('strategy_id', help='Strategy ID or name')
        backtest_parser.add_argument('--start-date', help='Start date (YYYY-MM-DD)')
        backtest_parser.add_argument('--end-date', help='End date (YYYY-MM-DD)')
        backtest_parser.add_argument('--capital', type=float, default=100000, help='Initial capital')
        backtest_parser.add_argument('--timeframe', default='1d', help='Timeframe')
        backtest_parser.add_argument('--instrument', default='NSE:NIFTY', help='Instrument')
        backtest_parser.add_argument('--params', help='Strategy parameters (JSON)')
        backtest_parser.add_argument('--provider', default='yfinance', choices=['breeze', 'angel', 'yfinance', 'csv'], help='Data provider')
        backtest_parser.add_argument('--stream', action='store_true', help='Stream backtest events in real-time')
        
        # Position commands
        position_parser = subparsers.add_parser('position', help='Position management')
        position_sub = position_parser.add_subparsers(dest='position_action')
        
        position_sub.add_parser('list', help='List all positions')
        position_sub.add_parser('close', help='Close all positions')
        
        # Order commands
        order_parser = subparsers.add_parser('order', help='Order management')
        order_sub = order_parser.add_subparsers(dest='order_action')
        
        order_sub.add_parser('list', help='List all orders')
        
        # Place order
        place_order = order_sub.add_parser('place', help='Place an order')
        place_order.add_argument('--instrument', required=True, help='Instrument')
        place_order.add_argument('--side', required=True, choices=['BUY', 'SELL'])
        place_order.add_argument('--quantity', type=int, required=True)
        place_order.add_argument('--price', type=float, default=0)
        place_order.add_argument('--type', default='MARKET', choices=['MARKET', 'LIMIT', 'SL'])
        
        # Status command
        subparsers.add_parser('status', help='Platform status')
        
        # Config command
        config_parser = subparsers.add_parser('config', help='Configuration')
        config_sub = config_parser.add_subparsers(dest='config_action')
        
        config_sub.add_parser('show', help='Show current config')
        
        self.args = parser.parse_args()
    
    def run(self) -> None:
        """Run CLI command"""
        if not self.args.command:
            print("No command specified. Use --help for usage.")
            return
        
        command = self.args.command
        
        if command == 'start':
            self._cmd_start()
        elif command == 'stop':
            self._cmd_stop()
        elif command == 'strategy':
            self._cmd_strategy()
        elif command == 'backtest':
            self._cmd_backtest()
        elif command == 'position':
            self._cmd_position()
        elif command == 'order':
            self._cmd_order()
        elif command == 'status':
            self._cmd_status()
        elif command == 'config':
            self._cmd_config()
        else:
            print(f"Unknown command: {command}")
    
    def _cmd_start(self) -> None:
        """Start the platform"""
        broker_name = self.args.broker
        
        print(f"Starting platform with broker: {broker_name}")
        
        # Create broker
        broker_config = {'name': broker_name}
        self.broker = BrokerFactory.create(broker_name, broker_config)
        
        # Connect and authenticate
        if not self.broker.connect():
            print("Failed to connect to broker")
            return
        
        if not self.broker.authenticate():
            print("Failed to authenticate with broker")
            return
        
        # Initialize components
        self.market_data = MarketDataManager(self.broker)
        self.execution_engine = ExecutionEngine(self.broker)
        self.risk_manager = RiskManager()
        self.backtest_manager = BacktestManager(f"{self.data_dir}/backtests")
        
        # Start market data
        self.market_data.start()
        
        print(f"Platform started successfully")
        print(f"Broker: {broker_name}")
        print(f"Data directory: {self.data_dir}")
        
        # Save state
        self._save_state()
    
    def _cmd_stop(self) -> None:
        """Stop the platform"""
        print("Stopping platform...")
        
        if self.market_data:
            self.market_data.stop()
        
        if self.broker:
            self.broker.disconnect()
        
        if self.state_store:
            self.state_store.close()
        
        print("Platform stopped")
    
    def _cmd_strategy(self) -> None:
        """Strategy management"""
        action = getattr(self.args, 'strategy_action', None)
        
        if action == 'list':
            self._strategy_list()
        elif action == 'start':
            self._strategy_start()
        elif action == 'stop':
            self._strategy_stop()
        elif action == 'status':
            self._strategy_status()
        else:
            print("Use 'strategy list' to see available strategies")
    
    def _strategy_list(self) -> None:
        """List available strategies"""
        strategies = StrategyRegistry.list_strategies()
        
        print("\n=== Available Strategies ===\n")
        for name in strategies:
            print(f"  - {name}")
        print()
    
    def _strategy_start(self) -> None:
        """Start a strategy"""
        strategy_id = self.args.strategy_id
        instrument = self.args.instrument or 'NSE:NIFTY'
        params = json.loads(self.args.params or '{}')
        
        # Ensure platform is started
        if not self.broker:
            print("Platform not started. Use 'start' command first.")
            return
        
        # Parse strategy - could be name or ID
        strategy_name = strategy_id
        params['instrument'] = instrument
        
        # Create strategy
        try:
            strategy = StrategyRegistry.create(strategy_name, strategy_id, params)
            strategy.initialize()
            strategy.start()
            
            self._running_strategies[strategy_id] = strategy
            
            print(f"Strategy '{strategy_name}' started with ID: {strategy_id}")
            print(f"Instrument: {instrument}")
            print(f"Parameters: {params}")
            
            # Subscribe to market data
            self.market_data.subscribe(instrument, strategy.on_tick)
            
            # Subscribe to candles
            candle_builder = self.market_data.get_candle_builder('1m')
            candle_builder.on_candle(strategy.on_candle)
            
        except Exception as e:
            print(f"Failed to start strategy: {e}")
    
    def _strategy_stop(self) -> None:
        """Stop a strategy"""
        strategy_id = self.args.strategy_id
        
        if strategy_id in self._running_strategies:
            strategy = self._running_strategies[strategy_id]
            strategy.stop()
            del self._running_strategies[strategy_id]
            print(f"Strategy '{strategy_id}' stopped")
        else:
            print(f"Strategy '{strategy_id}' not running")
    
    def _strategy_status(self) -> None:
        """Get strategy status"""
        strategy_id = self.args.strategy_id
        
        if strategy_id in self._running_strategies:
            strategy = self._running_strategies[strategy_id]
            print(f"\n=== Strategy: {strategy_id} ===")
            print(f"Name: {strategy.name}")
            print(f"Running: {strategy.is_running}")
            print(f"Signals generated: {len(strategy.get_signals())}")
            
            indicators = strategy._indicators
            if indicators:
                print("\nIndicators:")
                for name, value in indicators.items():
                    print(f"  {name}: {value}")
            print()
        else:
            print(f"Strategy '{strategy_id}' not found")
    
    def _cmd_backtest(self) -> None:
        """Run backtest"""
        strategy_id = self.args.strategy_id
        instrument = self.args.instrument
        capital = self.args.capital
        timeframe = self.args.timeframe
        
        # Parse dates
        start_date = self.args.start_date
        end_date = self.args.end_date
        
        if start_date:
            start = _cli_ensure_ist(datetime.strptime(start_date, '%Y-%m-%d'))
        else:
            start = __import__('utils.timezone', fromlist=['now_ist']).now_ist() - timedelta(days=90)
        
        if end_date:
            end = _cli_ensure_ist(datetime.strptime(end_date, '%Y-%m-%d'))
        else:
            end = __import__('utils.timezone', fromlist=['now_ist']).now_ist()
        
        # Parse params
        params = json.loads(self.args.params or '{}')
        
        # Ensure broker is initialized
        if not self.broker:
            self.broker = MockBroker()
        
        # Resolve universe or multi-symbol input
        from market_data.universe import UniverseManager
        resolved_instruments = UniverseManager.resolve_instruments(instrument)
        if not resolved_instruments:
            resolved_instruments = [instrument]

        # Use provider factory to get data provider
        data_provider = None
        provider_name = getattr(self.args, 'provider', 'yfinance')
        try:
            from market_data.factory import ProviderFactory
            data_provider = ProviderFactory.get(provider_name)
            print(f"Using data provider: {provider_name}")
        except Exception as e:
            print(f"Warning: Could not initialize {provider_name} provider: {e}")
            print("Falling back to mock data")
            data_provider = None

        # Default wider window for daily MCX / trend systems
        if strategy_id == 'mcx_trend_rider' and not self.args.start_date:
            start = datetime(2024, 1, 1, tzinfo=_CLI_IST)

        # Create backtest config
        bt_config = BacktestConfig(
            strategy_id=strategy_id,
            strategy_name=strategy_id,  # Use ID as name (could be name or class)
            strategy_params=params,
            instruments=resolved_instruments,
            start_date=start,
            end_date=end,
            initial_capital=capital,
            timeframe=timeframe
        )
        
        print(f"\n=== Running Backtest ===")
        print(f"Strategy: {strategy_id}")
        print(f"Instruments: {resolved_instruments}")
        print(f"Period: {start.date()} to {end.date()}")
        print(f"Capital: {format_inr(capital)}")
        print(f"Timeframe: {timeframe}")
        print()
        
        # Create and run backtest
        engine = BacktestEngine(bt_config, data_provider=data_provider)
        
        # Check if streaming mode is enabled
        use_streaming = getattr(self.args, 'stream', False)
        
        if use_streaming:
            # Streaming mode: print events as they happen
            trades_entered = 0
            trades_exited = 0
            
            def event_handler(event_type: str, payload: dict):
                nonlocal trades_entered, trades_exited
                
                if event_type == "backtest_started":
                    print(f"\n[START] Backtest initiated")
                elif event_type == "candles_loaded":
                    print(f"[DATA] Candles loaded for {payload.get('instruments', [])}")
                elif event_type == "progress":
                    progress = payload.get('progress', 0)
                    print(f"\r[PROGRESS] {progress:.1f}%", end='', flush=True)
                elif event_type == "trade_entry":
                    trades_entered += 1
                    inst = payload.get('instrument', '')
                    side = payload.get('side', '')
                    price = payload.get('entry_price', 0)
                    qty = payload.get('quantity', 0)
                    print(f"\n[ENTRY #{trades_entered}] {side} {qty} {inst} @ {price:.2f}")
                elif event_type == "trade_exit":
                    trades_exited += 1
                    inst = payload.get('instrument', '')
                    side = payload.get('side', '')
                    exit_price = payload.get('exit_price', 0)
                    pnl = payload.get('pnl', 0)
                    duration = payload.get('duration', 0)
                    print(f"[EXIT #{trades_exited}] {side} {inst} @ {exit_price:.2f} | PnL: {pnl:+.2f} | Duration: {duration/60:.1f}min")
                elif event_type == "metrics_update":
                    pass  # Skip intermediate metric updates in CLI
                elif event_type == "backtest_completed":
                    print(f"\n[COMPLETE] Backtest finished successfully")
                elif event_type == "backtest_failed":
                    error = payload.get('error', 'Unknown error')
                    print(f"\n[ERROR] Backtest failed: {error}")
            
            engine.set_event_callback(event_handler)
            result = engine.run()
        else:
            # Legacy mode: progress bar only
            def on_progress(p):
                print(f"\rProgress: {p:.1f}%", end='', flush=True)
            
            # Note: set_progress_callback doesn't exist, but we have event_callback
            def event_handler(event_type: str, payload: dict):
                if event_type == "progress":
                    on_progress(payload.get('progress', 0))
            
            engine.set_event_callback(event_handler)
            result = engine.run()
        
        print("\n\n=== Backtest Results ===")
        print(f"Status: {result.status.value}")
        print(f"Total Trades: {result.total_trades}")
        print(f"Winning: {result.winning_trades}")
        print(f"Losing: {result.losing_trades}")
        print(f"Win Rate: {result.win_rate:.2f}%")
        print(f"\nInitial Capital: {format_inr(result.initial_capital)}")
        print(f"Final Capital: {format_inr(result.final_capital)}")
        print(f"Total Return: {format_pnl(result.total_return)} ({result.total_return_pct:.2f}%)")
        print(f"Max Drawdown: {result.max_drawdown:.2f}%")
        print(f"Sharpe Ratio: {result.sharpe_ratio:.2f}")
        
        if result.avg_win > 0:
            print(f"Avg Win: {format_inr(result.avg_win)}")
        if result.avg_loss < 0:
            print(f"Avg Loss: {format_inr(result.avg_loss)}")
        print(f"Profit Factor: {result.profit_factor:.2f}")
        
        # Save result
        self._save_backtest_result(result)
    
    def _save_backtest_result(self, result: BacktestResult) -> None:
        """Save backtest result"""
        import pickle
        
        filename = f"{self.data_dir}/backtests/{result.strategy_id}_{__import__('utils.timezone', fromlist=['now_ist']).now_ist().strftime('%Y%m%d_%H%M%S')}.pkl"
        
        with open(filename, 'wb') as f:
            pickle.dump(result, f)
        
        print(f"\nResult saved to: {filename}")
    
    def _cmd_position(self) -> None:
        """Position management"""
        action = getattr(self.args, 'position_action', None)
        
        if action == 'list':
            self._position_list()
        elif action == 'close':
            self._position_close()
        else:
            print("Use 'position list' or 'position close'")
    
    def _position_list(self) -> None:
        """List positions"""
        if not self.execution_engine:
            print("Execution engine not initialized. Start platform first.")
            return
        
        positions = self.execution_engine.get_all_positions()
        
        print("\n=== Open Positions ===\n")
        if not positions:
            print("No open positions")
        else:
            for pos in positions:
                print(f"{pos.instrument}: {pos.quantity} @ {format_inr(pos.average_price)}")
        print()
    
    def _position_close(self) -> None:
        """Close all positions"""
        if not self.execution_engine:
            print("Execution engine not initialized. Start platform first.")
            return
        
        if self.execution_engine.close_all_positions():
            print("All positions closed")
        else:
            print("Failed to close some positions")
    
    def _cmd_order(self) -> None:
        """Order management"""
        action = getattr(self.args, 'order_action', None)
        
        if action == 'list':
            self._order_list()
        elif action == 'place':
            self._order_place()
        else:
            print("Use 'order list' or 'order place'")
    
    def _order_list(self) -> None:
        """List orders"""
        if not self.execution_engine:
            print("Execution engine not initialized. Start platform first.")
            return
        
        orders = self.execution_engine.get_all_orders()
        
        print("\n=== Orders ===\n")
        if not orders:
            print("No orders")
        else:
            for order in orders:
                print(f"{order.order_id}: {order.side.value} {order.quantity} @ {order.average_price} - {order.status.value}")
        print()
    
    def _order_place(self) -> None:
        """Place an order"""
        if not self.execution_engine:
            print("Execution engine not initialized. Start platform first.")
            return
        
        from core.models import Signal
        
        signal = Signal(
            strategy_id='manual',
            instrument=self.args.instrument,
            action=OrderSide.BUY if self.args.side == 'BUY' else OrderSide.SELL,
            quantity=self.args.quantity,
            order_type=OrderType[self.args.type],
            price=self.args.price
        )
        
        result = self.execution_engine.execute_signal(signal)
        
        if result.success:
            print(f"Order placed: {result.order.order_id}")
        else:
            print(f"Order failed: {result.error}")
    
    def _cmd_status(self) -> None:
        """Show platform status"""
        print("\n=== Platform Status ===\n")
        
        if self.broker:
            print(f"Broker: {self.broker.__class__.__name__}")
            print(f"Connected: {self.broker.is_connected}")
        else:
            print("Broker: Not initialized")
        
        print(f"\nRunning Strategies: {len(self._running_strategies)}")
        for sid, strat in self._running_strategies.items():
            print(f"  - {sid}: {strat.name} ({'running' if strat.is_running else 'stopped'})")
        
        if self.execution_engine:
            positions = self.execution_engine.get_all_positions()
            orders = self.execution_engine.get_all_orders()
            print(f"\nPositions: {len(positions)}")
            print(f"Orders: {len(orders)}")
        
        if self.risk_manager:
            stats = self.risk_manager.get_daily_stats()
            print(f"\nDaily P&L: {format_pnl(stats['daily_pnl'])}")
            print(f"Daily Trades: {stats['daily_trades']}")
        
        print()
    
    def _cmd_config(self) -> None:
        """Config management"""
        action = getattr(self.args, 'config_action', None)
        
        if action == 'show':
            cfg = config.get_config()
            print("\n=== Configuration ===\n")
            print(json.dumps(cfg.config, indent=2))
            print()
    
    def _save_state(self) -> None:
        """Save platform state"""
        state = {
            'running_strategies': list(self._running_strategies.keys()),
            'timestamp': get_timestamp()
        }
        self.state_store.save_state('platform', state)


def main():
    """Main entry point"""
    cli = PlatformCLI()
    cli.run()


if __name__ == '__main__':
    main()
