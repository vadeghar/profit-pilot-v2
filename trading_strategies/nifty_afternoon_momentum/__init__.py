"""NIFTY Afternoon Momentum: on high-VIX days, buy an ATM weekly option at 14:25 in the direction of the day, flat by 15:10.

Experimental. The backtest uses modelled option premiums and its profit comes from one half-year; see
docs/trading/NIFTY_AFTERNOON_MOMENTUM.md before giving it any capital.

    strategy.py   the rules (Params, decide, NiftyAfternoonMomentumStrategy)
    pricing.py    option premium model used when no option candles exist
    data.py       builds the 5-minute candle / VIX research dataset
    backtest.py   the backtest, and the family comparison that led to these rules
"""
from trading_strategies.nifty_afternoon_momentum.strategy import NiftyAfternoonMomentumStrategy, Params

__all__ = ["NiftyAfternoonMomentumStrategy", "Params"]
