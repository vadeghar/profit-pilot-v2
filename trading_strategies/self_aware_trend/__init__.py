"""Self-Aware Trend System (SATS) on NIFTY: an adaptive SuperTrend, ported from a TradingView indicator.

Research only: a backtest, no live strategy class and no dashboard card yet. See docs/trading/SELF_AWARE_TREND.md.

    indicator.py  the indicator (Settings, compute)
    strategy.py   the trade plan around its signals and the simulator (Plan, simulate)
    data.py       5-minute candles resampled to the other timeframes
    backtest.py   the backtest: the script's own score per timeframe, then intraday as bought options
"""
from trading_strategies.self_aware_trend.indicator import Settings, compute
from trading_strategies.self_aware_trend.strategy import INTRADAY, SCRIPT, Plan, simulate

__all__ = ["Settings", "compute", "Plan", "SCRIPT", "INTRADAY", "simulate"]
