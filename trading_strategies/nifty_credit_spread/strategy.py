"""NIFTY trend-filtered credit spread: each weekly cycle, sell an out-of-the-money spread on the side the trend protects.

Rules (NIFTY weekly options, one spread at a time):

  1. Cycle     enter on the first trading day after a weekly expiry, at ``entry_time``, in the contract that
               expires at the end of that cycle (four or five trading days away).
  2. Trend     Nifty's previous daily close at or above its ``trend_ma``-day average: sell a put spread
               (profits unless the index falls). Below it: sell a call spread.
  3. Strikes   expected move = spot x India VIX (previous close) x sqrt(days to expiry / 365). The short
               strike is ``distance`` expected moves from spot, rounded away from it to a 50-point strike;
               the long strike is ``width`` points further out. The loss is capped at width - credit.
  4. Exits     buy the spread back when it costs ``1 - profit_target`` of the credit, or ``stop_multiple``
               times the credit, checked on every 5-minute close; otherwise at ``exit_time`` on expiry day.

Pure functions; backtest.py supplies the prices.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from market_data.expiries import weekly_expiry_weekday

STRIKE_STEP = 50


@dataclass(frozen=True)
class Params:
    trend_ma: int = 50
    side: str = "trend"                # "trend", or always "PE" / "CE"
    distance: float = 1.0
    width: int = 200
    entry_time: time = time(10, 0)
    exit_time: time = time(15, 10)
    profit_target: float = 0.5         # 0 = hold for the stop or expiry
    stop_multiple: float = 2.0         # 0 = no stop
    min_credit: float = 0.0            # skip the week if the spread pays less than this many points
    vix_min: float = 0.0               # skip the week if India VIX closed below this
    lots: int = 1


def expiries(days: Sequence[date]) -> List[date]:
    """Weekly expiry dates inside ``days`` (sorted trading days): Thursdays, Tuesdays from Sep-2025; an expiry
    that falls on a holiday moves to the trading day before."""
    trading, out = set(days), []
    d = days[0]
    while d <= days[-1]:
        if d.weekday() == weekly_expiry_weekday(d):
            e = d
            while e not in trading and (d - e).days < 4:
                e -= timedelta(days=1)
            if e in trading and (not out or e > out[-1]):
                out.append(e)
        d += timedelta(days=1)
    return out


def cycles(days: Sequence[date]) -> List[Tuple[date, date]]:
    """(entry day, expiry day) for every weekly cycle with at least one trading day before its expiry."""
    exp, out = expiries(days), []
    for previous, current in zip(exp, exp[1:]):
        later = [d for d in days if previous < d < current]
        if later:
            out.append((later[0], current))
    return out


def trend_side(daily_close: pd.Series, entry_day: date, params: Params) -> Optional[str]:
    """"PE" (sell puts) in an uptrend, "CE" in a downtrend, from closes before ``entry_day``; None without history."""
    if params.side != "trend":
        return params.side
    history = daily_close[daily_close.index < entry_day]
    if len(history) < params.trend_ma:
        return None
    return "PE" if history.iloc[-1] >= history.tail(params.trend_ma).mean() else "CE"


def strikes(spot: float, vix: float, days_to_expiry: float, right: str, params: Params) -> Tuple[int, int]:
    """(short strike, long strike) for a credit spread on ``right``."""
    move = spot * vix / 100.0 * math.sqrt(max(days_to_expiry, 0.5) / 365.0) * params.distance
    if right == "PE":
        short = int(math.floor((spot - move) / STRIKE_STEP) * STRIKE_STEP)
        return short, short - params.width
    short = int(math.ceil((spot + move) / STRIKE_STEP) * STRIKE_STEP)
    return short, short + params.width


def manage(value: np.ndarray, credit: float, params: Params) -> Tuple[int, str]:
    """Index of the exit candle and why. ``value[0]`` is the entry candle; the last candle is the time exit."""
    for i in range(1, len(value)):
        if params.profit_target and value[i] <= (1.0 - params.profit_target) * credit:
            return i, "target"
        if params.stop_multiple and value[i] >= params.stop_multiple * credit:
            return i, "stop"
    return len(value) - 1, "expiry"
