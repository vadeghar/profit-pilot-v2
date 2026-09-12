from profit_pilot.backtest.run import BacktestRunConfig
from profit_pilot.backtest.engine import BacktestEngine
from profit_pilot.data.market_data import MarketDataProvider
from profit_pilot.backtest.commission import CommissionModel, FlatCommission, BpsCommission
from profit_pilot.backtest.slippage import SlippageModel, BpsSlippage
from profit_pilot.strategy.registry import get_strategy, STRATEGY_REGISTRY
from typing import Sequence, Tuple, Callable
from datetime import date, datetime

class BacktestResult:
    pass  # engine returns its own result type; kept for typing

class BacktestRunner:
    def __init__(self, config: BacktestRunConfig):
        self.config = config

    def run(self) -> 'BacktestResult':
        provider = _build_provider(self.config)
        engine = BacktestEngine(data_provider=provider)
        strategy = _build_strategy(self.config)
        return engine.run(self.config, strategy)
def _build_provider(config: BacktestRunConfig) -> MarketDataProvider:
    from profit_pilot.data.fastapi_client import HttpMarketDataProvider
    symbol = getattr(config, "symbol", "RELIANCE") or "RELIANCE"
    p = HttpMarketDataProvider(symbol=symbol, interval="ONE_DAY")
    p.start = config.start; p.end = config.end
    return p

def _build_strategy(config: BacktestRunConfig) -> 'Strategy':
    name = config.strategy_params.get("strategy_id", "VPA_SWING_EQUITY_LONG_V2") if isinstance(config.strategy_params, dict) else "VPA_SWING_EQUITY_LONG_V2"
    cls = get_strategy(name)
    return cls()

def run_backtest(strategy_id: str, start: str, end: str, symbol: str = "RELIANCE", initial_cash: float = 100000) -> dict:
    from datetime import date
    config = BacktestRunConfig(
        symbol=symbol,
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        initial_cash=initial_cash,
        strategy_params={"strategy_id": strategy_id},
    )
    runner = BacktestRunner(config)
    return runner.run()
