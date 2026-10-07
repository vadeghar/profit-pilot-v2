"""Momentum rotation: each month hold the strongest few of the 200 most-traded NSE stocks; cash in a falling market.

Research only: a backtest, no strategy class and no dashboard card yet. See docs/investing/MOMENTUM_ROTATION.md.

    strategy.py   the rules (Params, universe, scores, risk_on, select)
    data.py       daily prices of today's Nifty 500 members from Yahoo
    backtest.py   the backtest with whole shares and delivery charges, and its studies
"""
from investment_strategies.momentum_rotation.strategy import Params

__all__ = ["Params"]
