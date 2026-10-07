"""Stock pullback (mean reversion): buy a two-day dip in a liquid stock in an uptrend, sell the bounce.

Research only, and the result is NO GO: the edge per trade is no larger than what a delivery trade costs.
See docs/trading/STOCK_PULLBACK.md.

    strategy.py   the rules (Params, rsi, indicators, market_ok)
    backtest.py   the backtest with whole shares and delivery charges, and its studies
"""
from trading_strategies.stock_pullback.strategy import Params

__all__ = ["Params"]
