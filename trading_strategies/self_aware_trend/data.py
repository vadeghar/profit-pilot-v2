"""Candles for the Self-Aware Trend backtest: the NIFTY 5-minute research dataset, resampled to other timeframes.

The 5-minute candles and India VIX closes are the cache built by ``nifty_afternoon_momentum.data``
(``<data root>/research/nifty_intraday/``). Longer bars are built from them inside each session, anchored at
09:15 like a TradingView chart, so the last bar of a session is short (15:15-15:30 on 30 and 60 minutes).
Nothing finer than 5 minutes is on disk.
"""
from __future__ import annotations

from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd

from trading_strategies.nifty_afternoon_momentum import data as dataset

DEFAULT_DATA_ROOT = dataset.DEFAULT_DATA_ROOT
BASE_MINUTES = 5
SESSION_OPEN_MINUTE = 9 * 60 + 15
BARS_PER_SESSION = 75


def load(data_root: Path = DEFAULT_DATA_ROOT, refresh: bool = False) -> Tuple[pd.DataFrame, pd.Series]:
    """(5-minute candles t, o, h, l, c of complete sessions, India VIX close by date)."""
    candles, _, vix = dataset.load(data_root, refresh=refresh)
    candles = candles[candles["t"].dt.minute % BASE_MINUTES == 0]      # a few sessions carry stray 1-minute rows
    per_day = candles.groupby(candles["t"].dt.date)["t"].transform("size")
    candles = candles[per_day == BARS_PER_SESSION].sort_values("t").reset_index(drop=True)
    return candles[["t", "o", "h", "l", "c"]], vix


def resample(candles: pd.DataFrame, minutes: int) -> Tuple[pd.DataFrame, np.ndarray]:
    """(bars of ``minutes`` built from the 5-minute ``candles``, index of the 5-minute candle each one closes on)."""
    if minutes == BASE_MINUTES:
        return candles.copy(), np.arange(len(candles))
    t = candles["t"]
    bucket = (t.dt.hour * 60 + t.dt.minute - SESSION_OPEN_MINUTE) // minutes
    grouped = candles.assign(_i=np.arange(len(candles))).groupby([t.dt.date, bucket], sort=True)
    bars = grouped.agg(t=("t", "first"), o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), _i=("_i", "last"))
    return bars[["t", "o", "h", "l", "c"]].reset_index(drop=True), bars["_i"].to_numpy()
