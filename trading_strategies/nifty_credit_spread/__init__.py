"""NIFTY trend-filtered credit spread: each week sell an out-of-the-money spread on the side the trend protects.

Research only, and the result is NO GO: see docs/trading/NIFTY_CREDIT_SPREAD.md. Kept for its recorded
option-price cache and backtest, which other NIFTY option studies can reuse.

    strategy.py   the rules (Params, cycles, trend_side, strikes, manage)
    data.py       index candles, India VIX and recorded weekly-option candles from Breeze
    backtest.py   the backtest on recorded prices, and its studies
"""
from trading_strategies.nifty_credit_spread.strategy import Params

__all__ = ["Params"]
