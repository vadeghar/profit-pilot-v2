"""Trend breakout: buy a liquid stock when it closes at a new 52-week high, hold it until its trend breaks.

Rules (daily candles; decided at a close, traded at the next open) - Donchian / turtle-style trend following:

  1. Universe   the ``universe_size`` most-traded NSE stocks over the last six months, refreshed monthly.
  2. Market     no new buys while the Nifty 50 is below its ``market_ma``-day average (0 switches this off).
                With ``market_exit`` the holdings are sold as well.
  3. Buy        the stock closes at its highest close of the last ``entry_days`` days. Next open, into a free
                slot; with more breakouts than slots the strongest 6-month return goes first
                (``rank`` "turnover": the most traded first).
  4. Sell       ``exit``: "low" - a close below the lowest close of the previous ``exit_days`` days;
                "average" - a close below the ``exit_days``-day average; "chandelier" - a close more than
                ``atr_mult`` ATRs below the highest high of the last ``exit_days`` days.

  5. Volume     optional (the "volume price analysis" claim that a breakout on heavy volume is the real one):
                the day's turnover must be at least ``volume_mult`` times its average over the previous
                ``volume_days`` days (and below ``volume_max`` times, to test the opposite), and the close
                must be in the top ``close_strength`` share of the day's range.

A stock that was sold can be bought again at its next new high. ``indicators`` returns what the rules need as
date x symbol frames; backtest.py does the money.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Params:
    universe_size: int = 200
    slots: int = 6
    entry_days: int = 252
    exit: str = "low"                # "low" | "average" | "chandelier"
    exit_days: int = 50
    atr_mult: float = 3.0
    market_ma: int = 200             # 0 = no market filter
    market_exit: bool = False
    rank: str = "momentum"           # "momentum" | "turnover"
    volume_mult: float = 0.0         # 0 = no volume condition
    volume_max: float = 0.0          # 0 = no upper limit
    volume_days: int = 50
    close_strength: float = 0.0      # 0.67 = close in the top third of the day's range
    max_hold: int = 100_000          # no time limit (fields below are read by the shared simulator)
    stop_pct: float = 0.0


def relative_volume(turnover: pd.DataFrame, days: int = 50) -> pd.DataFrame:
    """Today's turnover as a multiple of its average over the previous ``days`` days."""
    return turnover / turnover.shift(1).rolling(days).mean()


def indicators(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame, turnover: pd.DataFrame, params: Params) -> Dict[str, pd.DataFrame]:
    """setup (bool: new high today), score (lower = buy first), exit_ok (bool: the trailing exit has triggered)."""
    setup = close >= close.rolling(params.entry_days, min_periods=params.entry_days).max()
    relative = relative_volume(turnover, params.volume_days)
    if params.volume_mult:
        setup &= relative >= params.volume_mult
    if params.volume_max:
        setup &= relative < params.volume_max
    if params.close_strength:
        setup &= (close - low) / (high - low).replace(0.0, np.nan) >= params.close_strength
    if params.exit == "average":
        broken = close < close.rolling(params.exit_days).mean()
    elif params.exit == "chandelier":
        previous = close.shift(1)
        true_range = np.maximum(high - low, np.maximum((high - previous).abs(), (low - previous).abs()))
        atr = true_range.ewm(alpha=1.0 / 22, adjust=False, min_periods=22).mean()
        broken = close < high.rolling(params.exit_days).max() - params.atr_mult * atr
    else:
        broken = close < close.shift(1).rolling(params.exit_days).min()
    score = -(close / close.shift(126) - 1.0) if params.rank == "momentum" else -turnover.rolling(126).median()
    return {"setup": setup.fillna(False), "score": score, "exit_ok": broken.fillna(False)}
