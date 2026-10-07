"""Volume shock: buy liquid stocks that have just traded far more than usual, hold them for a month.

The "high-volume return premium" of Gervais, Kaniel and Mingelgrin (2001): a stock whose volume over a day or a
week is unusually high against its own recent history tended to rise over the following month, whatever its
price did during the shock; the explanation offered is that the burst of trading makes the stock visible to
more buyers.

Rules (decided at the close of the last trading day of a month, traded at the next open):

  1. Universe   the ``universe_size`` most-traded NSE stocks over the last six months.
  2. Shock      average daily turnover of the last ``recent_days`` days divided by the median of the
                ``base_days`` days before them. A candidate has a shock of at least ``min_shock``.
  3. Direction  ``direction`` "any" takes every candidate; "up" / "down" only those whose price rose / fell
                over the shock days.
  4. Selection  the ``top_n`` largest shocks, equal rupee slots, replaced every ``rebalance_months``.
  5. Regime     as the momentum rotation: cash while the Nifty 50 is below its ``regime_ma``-day average
                (``regime`` "none" ignores it).

The money, the universe and the regime rule are the momentum rotation's (investment_strategies/momentum_rotation).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pandas as pd

from investment_strategies.momentum_rotation.strategy import Params as RotationParams


@dataclass(frozen=True)
class Params:
    universe_size: int = 200
    top_n: int = 6
    recent_days: int = 5
    base_days: int = 50
    min_shock: float = 2.0
    direction: str = "any"           # "any" | "up" | "down"
    regime: str = "exit"             # "exit" | "none"
    regime_ma: int = 200
    rebalance_months: int = 1

    def rotation(self) -> RotationParams:
        """The momentum rotation's settings that carry the same mechanics (no holding buffer: picks are replaced)."""
        return RotationParams(universe_size=self.universe_size, top_n=self.top_n, hold_buffer=1.0, regime=self.regime,
                              regime_ma=self.regime_ma, rebalance_months=self.rebalance_months)


def shock_ratio(turnover: pd.DataFrame, recent_days: int = 5, base_days: int = 50) -> pd.DataFrame:
    """Recent average daily turnover over the median of the ``base_days`` days before it, for every day and stock."""
    return turnover.rolling(recent_days).mean() / turnover.shift(recent_days).rolling(base_days).median()


def ranked(turnover: pd.DataFrame, close: pd.DataFrame, at: pd.Timestamp, names: Sequence[str], params: Params) -> pd.Series:
    """Candidates as of ``at``, largest shock first."""
    volume = turnover.loc[:at, list(names)].tail(params.recent_days + params.base_days)
    prices = close.loc[:at, list(names)].tail(params.recent_days + 1)
    if len(volume) < params.recent_days + params.base_days:
        return pd.Series(dtype=float)
    shock = volume.tail(params.recent_days).mean() / volume.head(params.base_days).median()
    move = prices.iloc[-1] / prices.iloc[0] - 1.0
    keep = shock >= params.min_shock
    if params.direction == "up":
        keep &= move > 0
    elif params.direction == "down":
        keep &= move < 0
    return shock[keep].dropna().sort_values(ascending=False)
