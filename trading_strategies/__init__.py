"""Regular trading strategies: intraday and swing systems that are not tick scalpers.

One sub-package per strategy, each holding its rules (strategy.py) and, where it has them, its own
backtest (backtest.py) and paper trader (paper_trader.py). A strategy becomes available to the
backtest engine, CLI and dashboard by registering with core.strategy.StrategyRegistry - importing
this package does that for every strategy listed below.

    index_oi_momentum/          Index Options OI Momentum (switched off: deprecated until further notice)
    nifty_afternoon_momentum/   NIFTY Afternoon Momentum (experimental: research candidate, no dashboard card yet)
    self_aware_trend/           Self-Aware Trend System (research backtest only: no strategy class, not registered)
    nifty_credit_spread/        NIFTY trend-filtered credit spread (research backtest only: tested NO GO, not registered)
    stock_pullback/             Stock pullback mean reversion (research backtest only: tested NO GO, not registered)
"""
from core.strategy import StrategyRegistry
from trading_strategies.index_oi_momentum import IndexOIMomentumStrategy
from trading_strategies.nifty_afternoon_momentum import NiftyAfternoonMomentumStrategy

StrategyRegistry.register("index_oi_momentum", IndexOIMomentumStrategy)
StrategyRegistry.register("nifty_afternoon_momentum", NiftyAfternoonMomentumStrategy)

__all__ = ["IndexOIMomentumStrategy", "NiftyAfternoonMomentumStrategy"]
