from profit_pilot.backtest.run import BacktestRunConfig
from profit_pilot.backtest.engine import BacktestEngine
from profit_pilot.data.market_data import MarketDataProvider
from profit_pilot.backtest.commission import CommissionModel, FlatCommission, BpsCommission
from profit_pilot.backtest.slippage import SlippageModel, BpsSlippage
from typing import Sequence, Tuple, Callable
from datetime import date, datetime


class BacktestRunner:
    def __init__(self, config: BacktestRunConfig):
        self.config = config

    def run(self) -> 'BacktestResult':
        # Build data provider from config (HTTP-backed)
        provider = _build_provider(self.config)
        engine = BacktestEngine(data_provider=provider)
        # Build a simple strategy from config params
        strategy = _build_strategy(self.config.strategy_params)
        return engine.run(self.config, strategy)


def _build_provider(config: BacktestRunConfig) -> MarketDataProvider:
    # Use the HTTP provider we created
    from profit_pilot.data.fastapi_client import HttpMarketDataProvider
    return HttpMarketDataProvider()


def _build_strategy(params: dict) -> 'Strategy':
    # Import a simple strategy or use defaults
    from profit_pilot.strategy.deterministic import OneBarBuy
    return OneBarBuy()