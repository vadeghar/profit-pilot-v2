"""Trend breakout: buy a liquid stock at a new 52-week high, hold it until it closes below its 50-day low.

Research only, and the result is NO GO: see docs/investing/TREND_BREAKOUT.md.

    strategy.py   the rules (Params, indicators)
    backtest.py   the backtest (it runs on the day-by-day simulator in trading_strategies/stock_pullback) and its studies
"""
from investment_strategies.trend_breakout.strategy import Params

__all__ = ["Params"]
