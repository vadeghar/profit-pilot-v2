"""Volume shock: buy liquid stocks that have just traded far more than usual (the high-volume return premium).

Research only, and the result is NO GO: see docs/investing/VOLUME_SHOCK.md.

    strategy.py   the rules (Params, shock_ratio, ranked)
    backtest.py   the signal test and the backtest (on the momentum rotation's monthly simulator)
"""
from investment_strategies.volume_shock.strategy import Params

__all__ = ["Params"]
