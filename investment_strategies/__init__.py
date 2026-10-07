"""Investment strategies: long-horizon systems held for months.

One sub-package per strategy (as in trading_strategies). A strategy becomes available to the backtest
engine, CLI and dashboard by registering its class with core.strategy.StrategyRegistry here.

    momentum_rotation/   Monthly momentum rotation in liquid NSE stocks (research backtest only: not registered)
"""
