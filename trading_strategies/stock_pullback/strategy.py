"""Stock pullback (mean reversion): buy a sharp two-day dip in a liquid stock that is in an uptrend, sell the bounce.

Rules (daily candles; decided at a close, traded at the next open) - the RSI(2) method of Connors and Alvarez:

  1. Universe   the ``universe_size`` most-traded NSE stocks over the last six months, refreshed monthly.
  2. Market     no new buys while the Nifty 50 is below its ``market_ma``-day average (0 switches this off).
  3. Setup      the stock closes above its own ``trend_ma``-day average and its ``signal`` is oversold:
                "rsi2" - 2-day RSI below ``threshold``; "ibs" - (close - low) / (high - low) below ``threshold``.
  4. Buy        next open, into a free slot; with more setups than slots the most oversold go first.
  5. Sell       next open after the close is above the ``exit_ma``-day average, or after ``max_hold`` trading
                days, or after a close ``stop_pct`` below the purchase price (0 = no stop).

``indicators`` returns everything the rules need as date x symbol frames; backtest.py does the money.
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
    signal: str = "rsi2"             # "rsi2" | "ibs"
    threshold: float = 10.0          # RSI points, or an IBS fraction (0.2) when signal is "ibs"
    trend_ma: int = 200              # 0 = no trend filter
    market_ma: int = 200             # 0 = no market filter
    exit_ma: int = 5
    max_hold: int = 10
    stop_pct: float = 0.0
    rank: str = "oversold"           # "oversold" | "momentum" (strongest 6-month return first)


def rsi(close: pd.DataFrame, n: int = 2) -> pd.DataFrame:
    """Wilder's RSI over ``n`` days for every column."""
    change = close.diff()
    up = change.clip(lower=0).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    down = (-change.clip(upper=0)).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    value = 100.0 - 100.0 / (1.0 + up / down.replace(0.0, np.nan)).where(down > 0, np.inf)
    return value.where(up.notna() & down.notna())


def indicators(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame, params: Params) -> Dict[str, pd.DataFrame]:
    """setup (bool), score (lower = buy first), exit_ok (bool: close above the exit average)."""
    in_trend = close > close.rolling(params.trend_ma).mean() if params.trend_ma else close.notna()
    if params.signal == "ibs":
        spread = (high - low).replace(0.0, np.nan)
        signal = (close - low) / spread
    else:
        signal = rsi(close, 2)
    setup = in_trend & (signal < params.threshold)
    score = signal if params.rank == "oversold" else -(close / close.shift(126) - 1.0)
    return {"setup": setup.fillna(False), "score": score, "exit_ok": (close > close.rolling(params.exit_ma).mean()).fillna(False)}


def market_ok(index_close: pd.Series, params: Params) -> pd.Series:
    """True on days the Nifty closed at or above its average (always True when the filter is off)."""
    if not params.market_ma:
        return pd.Series(True, index=index_close.index)
    return (index_close >= index_close.rolling(params.market_ma).mean()).fillna(False)
