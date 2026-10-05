"""Regular trading strategies: intraday and swing systems that are not tick scalpers.

One sub-package per strategy, each holding its rules (strategy.py) and, where it has them, its own
backtest (backtest.py) and paper trader (paper_trader.py). A strategy becomes available to the
backtest engine, CLI and dashboard by registering with core.strategy.StrategyRegistry - importing
this package does that for every strategy listed below.

    index_oi_momentum/   Index Options OI Momentum (switched off: deprecated until further notice)
"""
from core.strategy import StrategyRegistry
from trading_strategies.index_oi_momentum import IndexOIMomentumStrategy

StrategyRegistry.register("index_oi_momentum", IndexOIMomentumStrategy)

__all__ = ["IndexOIMomentumStrategy"]
