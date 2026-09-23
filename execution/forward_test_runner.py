"""
Execution Engine: Forward Test Runner
Paper trading runner using live market quotes from Angel One SmartAPI.
Supports commodities (MCX) and equities (NSE).
Dispatches signals to Telegram channel.
"""

import os
import json
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

from core.models import Candle, OrderSide
from strategies import StrategyRegistry
from brokers.angel_one import AngelOneBroker
from market_data.resolver import SymbolResolver
from utils.telegram import TelegramNotifier
from utils import Logger
from utils.timezone import now_ist
import platform_config


def load_env(env_path: Optional[str] = None) -> Dict[str, str]:
    env_path = env_path or str(platform_config.ENV_FILE)
    env = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip()
    return env


class ForwardTestRunner:
    """Forward Test Runner: Paper trading with real market data feed"""

    def __init__(self, strategy_id: str, instruments: List[str], capital: float = 100000.0, params: Dict[str, Any] = None):
        self.strategy_id = strategy_id
        self.strategy_name = strategy_id
        self.instruments = instruments
        self.capital = capital
        self.params = params or {}
        self.logger = Logger(f"forward_test.{strategy_id}")

        # In-memory paper portfolio state
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.paper_trades: List[Dict[str, Any]] = []
        self.running = False

        # Broker connection & Env
        self.env = load_env()
        self.broker = None
        self._init_broker()

        # Telegram notifier
        bot_token = self.env.get('TELEGRAM_BOT_TOKEN', '')
        chat_id = self.env.get('TELEGRAM_HOME_CHANNEL', '')
        self.notifier = TelegramNotifier(bot_token, chat_id) if bot_token and chat_id else None

        # Resolver
        self.resolver = SymbolResolver()

        # Strategy
        self.strategy = StrategyRegistry.create(self.strategy_name, self.strategy_id, {
            'capital': self.capital,
            **(self.params)
        })
        self.strategy.initialize()

        # Load existing state if exists
        self._load_state()

    def _init_broker(self):
        config = {
            'api_key': self.env.get('ANGEL_API_KEY'),
            'client_id': self.env.get('ANGEL_CLIENT_CODE'),
            'password': self.env.get('ANGEL_PASSWORD_OR_MPIN'),
            'totp_secret': self.env.get('ANGEL_TOTP_SECRET')
        }
        self.broker = AngelOneBroker(config)
        auth_ok = self.broker.authenticate()
        if not auth_ok:
            self.logger.error("Failed to authenticate with Angel One for forward test!")
        else:
            self.logger.info("Authenticated successfully with Angel One for forward testing.")

    def run_once(self):
        """Poll current market quotes for instruments, feed to strategy, and evaluate"""
        self.logger.info(f"Running forward test step for {self.instruments}...")

        # Fixed token mapping for MCX and common symbols
        token_map = {
            'MCX_CRUDEOIL': ('MCX', '565899', 'CRUDEOIL21SEP26FUT'),
            'MCX_GOLDM': ('MCX', '569003', 'GOLDM05OCT26FUT'),
            'MCX_SILVERM': ('MCX', '483080', 'SILVERM30NOV26FUT'),
            'MCX_GOLD': ('MCX', '483079', 'GOLD05OCT26FUT'),
            'MCX_SILVER': ('MCX', '495214', 'SILVER04DEC26FUT'),
            'NSE_RELIANCE': ('NSE', '2885', 'RELIANCE-EQ'),
            'NSE_HDFCBANK': ('NSE', '1333', 'HDFCBANK-EQ'),
            'NSE_ICICIBANK': ('NSE', '4963', 'ICICIBANK-EQ'),
            'NSE_SBIN': ('NSE', '3045', 'SBIN-EQ'),
            'NSE_TCS': ('NSE', '11536', 'TCS-EQ'),
            'NSE_ITC': ('NSE', '1660', 'ITC-EQ'),
            'NSE_TATASTEEL': ('NSE', '3499', 'TATASTEEL-EQ'),
            'NSE_TATAMOTORS': ('NSE', '3456', 'TMPV-EQ'),
            'NSE_INFY': ('NSE', '1594', 'INFY-EQ'),
            'NSE_NIFTY': ('NSE', '99926000', 'Nifty 50'),
        }

        for inst in self.instruments:
            clean = inst.replace(':', '_').upper()
            info = token_map.get(clean)
            if not info:
                # Try resolver
                token, sym = self.resolver.resolve_angel_token(inst)
                if token and token != '0':
                    info = ('NSE', token, sym)
                else:
                    self.logger.warning(f"No token mapping for instrument {inst}")
                    continue

            exchange, token, sym = info
            try:
                # Rate-limit safety pause
                time.sleep(0.5)
                quote_res = self.broker.client.ltpData(exchange, sym, token)
                if quote_res and quote_res.get('status') and quote_res.get('data'):
                    data = quote_res['data']
                    ltp = float(data.get('ltp', 0.0))
                    close = float(data.get('close', ltp))
                    high = float(data.get('high', ltp))
                    low = float(data.get('low', ltp))
                    opn = float(data.get('open', ltp))

                    candle = Candle(
                        instrument=inst,
                        timeframe="1d",
                        open=opn,
                        high=high,
                        low=low,
                        close=ltp,
                        volume=1000,
                        timestamp=now_ist()  # IST
                    )

                    # Evaluate signal
                    signal = self.strategy.on_candle(candle)
                    if signal:
                        self.logger.info(f"🎯 FORWARD TEST SIGNAL: {signal.action.value} {signal.instrument} @ ₹{ltp:.2f}")
                        if self.notifier:
                            msg = (
                                f"🔔 *Forward Test Signal Alert*\n"
                                f"Strategy: `{self.strategy_id}`\n"
                                f"Instrument: `{signal.instrument}`\n"
                                f"Action: *{signal.action.value}*\n"
                                f"Price: *₹{ltp:,.2f}*\n"
                                f"Quantity: `{signal.quantity}`\n"
                                f"Timestamp: `{now_ist().strftime('%Y-%m-%d %H:%M:%S IST')}`"
                            )
                            self.notifier.send_alert(msg)
                    else:
                        self.logger.info(f"Market Quote {clean} ({sym}): LTP ₹{ltp:,.2f} | Strategy holding position or waiting for trigger.")
            except Exception as e:
                self.logger.error(f"Error fetching quote for {inst}: {e}")

        # Save forward testing state
        self._save_state()

    def _load_state(self):
        state_file = str(platform_config.FORWARD_TEST_DIR / f"{self.strategy_id}.json")
        if os.path.exists(state_file):
            try:
                with open(state_file) as f:
                    d = json.load(f)
                    self.positions = d.get('positions', {})
                    self.paper_trades = d.get('paper_trades', [])
            except Exception as e:
                self.logger.warning(f"Error loading state: {e}")

    def _save_state(self):
        os.makedirs(str(platform_config.FORWARD_TEST_DIR), exist_ok=True)
        state_file = str(platform_config.FORWARD_TEST_DIR / f"{self.strategy_id}.json")
        with open(state_file, 'w') as f:
            json.dump({
                'strategy_id': self.strategy_id,
                'strategy_name': self.strategy_name,
                'instruments': self.instruments,
                'capital': self.capital,
                'status': 'ACTIVE',
                'mode': 'PAPER_FORWARD_TEST',
                'positions': self.positions,
                'paper_trades_count': len(self.paper_trades),
                'paper_trades': self.paper_trades,
                'last_updated': now_ist().isoformat()
            }, f, indent=2)


if __name__ == '__main__':
    runner = ForwardTestRunner(
        strategy_id='mcx_trend_rider',
        instruments=['MCX_GOLDM', 'MCX_SILVERM', 'MCX_CRUDEOIL'],
        capital=100000.0
    )
    runner.run_once()
