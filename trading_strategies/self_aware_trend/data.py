"""Candles for the Self-Aware Trend backtest: the NIFTY 5-minute research dataset, resampled to other timeframes.

The 5-minute candles and India VIX closes are the cache built by ``nifty_afternoon_momentum.data``
(``<data root>/research/nifty_intraday/``). Longer bars are built from them inside each session, anchored at
09:15 like a TradingView chart, so the last bar of a session is short (15:15-15:30 on 30 and 60 minutes).
Nothing finer than 5 minutes is on disk.

Stocks come from Yahoo Finance (``load_yahoo``), which serves 5-minute candles for the last 60 days only and
hourly candles for about three years; they are cached under ``<data root>/research/self_aware_trend/``.
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
YAHOO = {5: ("5m", "60d", 72), 60: ("1h", "730d", 6)}      # base minutes -> (interval, period, fewest bars in a usable session)


def load(data_root: Path = DEFAULT_DATA_ROOT, refresh: bool = False) -> Tuple[pd.DataFrame, pd.Series]:
    """(5-minute candles t, o, h, l, c of complete sessions, India VIX close by date)."""
    candles, _, vix = dataset.load(data_root, refresh=refresh)
    candles = candles[candles["t"].dt.minute % BASE_MINUTES == 0]      # a few sessions carry stray 1-minute rows
    per_day = candles.groupby(candles["t"].dt.date)["t"].transform("size")
    candles = candles[per_day == BARS_PER_SESSION].sort_values("t").reset_index(drop=True)
    return candles[["t", "o", "h", "l", "c"]], vix


def load_yahoo(symbol: str, base_minutes: int, data_root: Path = DEFAULT_DATA_ROOT, refresh: bool = False) -> pd.DataFrame:
    """Candles t, o, h, l, c, v for a Yahoo ``symbol`` (``HDFCBANK.NS``, ``^NSEI``), complete past sessions only."""
    interval, period, min_bars = YAHOO[base_minutes]
    path = Path(data_root) / "research" / "self_aware_trend" / f"{symbol.strip('^').replace('.', '_')}_{interval}.csv"
    if refresh or not path.exists():
        y = dataset._yahoo(symbol, period=period, interval=interval)
        candles = pd.DataFrame({"t": pd.to_datetime(y["t"]).dt.tz_localize(None), "o": y["Open"], "h": y["High"],
                                "l": y["Low"], "c": y["Close"], "v": y["Volume"]}).dropna().sort_values("t")
        candles = candles[candles["t"].dt.date < pd.Timestamp.now().date()]
        per_day = candles.groupby(candles["t"].dt.date)["t"].transform("size")
        path.parent.mkdir(parents=True, exist_ok=True)
        candles[per_day >= min_bars].to_csv(path, index=False)
    return pd.read_csv(path, parse_dates=["t"])


def resample(candles: pd.DataFrame, minutes: int, base_minutes: int = BASE_MINUTES) -> Tuple[pd.DataFrame, np.ndarray]:
    """(bars of ``minutes`` built from the ``base_minutes`` ``candles``, index of the candle each one closes on)."""
    if minutes == base_minutes:
        return candles.copy(), np.arange(len(candles))
    t = candles["t"]
    bucket = (t.dt.hour * 60 + t.dt.minute - SESSION_OPEN_MINUTE) // minutes
    grouped = candles.assign(_i=np.arange(len(candles))).groupby([t.dt.date, bucket], sort=True)
    columns = dict(t=("t", "first"), o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), _i=("_i", "last"))
    if "v" in candles:
        columns["v"] = ("v", "sum")
    bars = grouped.agg(**columns)
    return bars.drop(columns="_i").reset_index(drop=True), bars["_i"].to_numpy()
