"""Momentum rotation: once a month, hold the strongest few of the most-traded NSE stocks; cash in a falling market.

Rules (decided at the close of the last trading day of a month, traded at the next open):

  1. Universe   the ``universe_size`` stocks with the highest median daily turnover over the last six months,
                with at least ``min_history`` days of prices. Liquidity is measured as of that day.
  2. Score      ``index``: NSE's Nifty200 Momentum 30 method - 6-month and 12-month price return, each divided
                by the stock's annualised daily volatility over a year, turned into z-scores across the universe
                and averaged. ``12-1``: 12-month return skipping the latest month. ``6m``: 6-month return.
  3. Regime     if the Nifty 50 closes below its ``regime_ma``-day average: ``exit`` sells everything and waits
                in cash, ``no_new`` keeps what is held but buys nothing, ``none`` ignores it.
  4. Selection  hold ``top_n`` stocks, equal rupee slots. A stock already held is kept while it ranks inside
                the top ``top_n x hold_buffer``; freed slots go to the highest-ranked stocks not held.

These functions are pure: prices in, names out. backtest.py does the money.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import numpy as np
import pandas as pd

DAYS_6M, DAYS_12M, DAYS_1M = 126, 252, 21


@dataclass(frozen=True)
class Params:
    universe_size: int = 200
    min_history: int = 273
    top_n: int = 6
    score: str = "index"             # "index" | "12-1" | "6m"
    hold_buffer: float = 2.0
    regime: str = "exit"             # "exit" | "no_new" | "none"
    regime_ma: int = 200
    rebalance_months: int = 1        # 1 = monthly, 6 = half-yearly like the NSE index
    volume_rule: str = "none"        # "skip_surge": drop stocks whose volume ratio is above ``volume_level``;
    volume_level: float = 2.0        # "quiet": keep only those at or below it; "rising": only those at or above it


def month_ends(days: pd.DatetimeIndex, every: int = 1) -> List[pd.Timestamp]:
    """Last trading day of each month (the final, still-open month is left out); every ``every``-th one."""
    last = pd.Series(days, index=days).groupby([days.year, days.month]).max().tolist()[:-1]
    return last[::every]


def universe(turnover: pd.DataFrame, close: pd.DataFrame, at: pd.Timestamp, params: Params) -> List[str]:
    """The most-traded stocks as of ``at``; ``turnover`` and ``close`` hold nothing later than ``at``."""
    window = turnover.loc[:at].tail(DAYS_6M)
    enough = close.loc[:at].notna().sum() >= params.min_history
    traded = window.notna().sum() >= DAYS_6M * 0.9
    liquidity = window.median()[enough & traded & close.loc[at].notna()]
    return liquidity.nlargest(params.universe_size).index.tolist()


def scores(close: pd.DataFrame, at: pd.Timestamp, names: Sequence[str], params: Params) -> pd.Series:
    """Momentum score of each of ``names`` as of ``at``, highest first; stocks without the history are dropped."""
    prices = close.loc[:at, list(names)].tail(DAYS_12M + DAYS_1M + 1)
    if len(prices) < DAYS_12M + DAYS_1M + 1:
        return pd.Series(dtype=float)
    last = prices.iloc[-1]
    r12, r6 = last / prices.iloc[-1 - DAYS_12M] - 1.0, last / prices.iloc[-1 - DAYS_6M] - 1.0
    if params.score == "12-1":
        out = prices.iloc[-1 - DAYS_1M] / prices.iloc[-1 - DAYS_12M - DAYS_1M] - 1.0
    elif params.score == "6m":
        out = r6
    else:
        vol = np.log(prices / prices.shift(1)).tail(DAYS_12M).std() * np.sqrt(DAYS_12M)
        z = lambda s: (s - s.mean()) / s.std()
        out = (z(r12 / vol) + z(r6 / vol)) / 2.0
    return out.replace([np.inf, -np.inf], np.nan).dropna().sort_values(ascending=False)


def volume_ratio(turnover: pd.DataFrame, at: pd.Timestamp, names: Sequence[str]) -> pd.Series:
    """Recent trading against the stock's own norm: median daily turnover of the last month over the last year."""
    window = turnover.loc[:at, list(names)].tail(DAYS_12M)
    return window.tail(DAYS_1M).median() / window.median()


def volume_screen(ranked: pd.Series, ratio: pd.Series, params: Params) -> pd.Series:
    """``ranked`` with the stocks the volume rule rejects removed (order kept)."""
    if params.volume_rule == "none":
        return ranked
    r = ratio.reindex(ranked.index)
    keep = {"skip_surge": r <= params.volume_level, "quiet": r <= params.volume_level, "rising": r >= params.volume_level}[params.volume_rule]
    return ranked[keep.fillna(False)]


def risk_on(index_close: pd.Series, at: pd.Timestamp, params: Params) -> bool:
    history = index_close.loc[:at].dropna()
    return params.regime == "none" or len(history) < params.regime_ma or history.iloc[-1] >= history.tail(params.regime_ma).mean()


def select(ranked: pd.Series, held: Sequence[str], params: Params, market_up: bool, affordable=lambda name: True) -> List[str]:
    """Names to hold next month. ``affordable(name)`` lets the caller skip a stock whose one share exceeds a slot."""
    if not market_up and params.regime == "exit":
        return []
    keep_rank = int(round(params.top_n * params.hold_buffer))
    order = ranked.index.tolist()
    keep = [name for name in held if name in order[:keep_rank]] if market_up or params.regime != "exit" else []
    if not market_up and params.regime == "no_new":
        return keep
    target = keep[:params.top_n]
    for name in order:
        if len(target) >= params.top_n:
            break
        if name not in target and affordable(name):
            target.append(name)
    return target
