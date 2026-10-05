"""Option premium model for backtests that have index candles but no option candles.

Black-Scholes on the forward, with two choices that lean against an intraday option buyer:

  * time runs in *variance time*: a trading day is one unit, of which ``SESSION_SHARE`` is spent between
    09:15 and 15:30. Calendar time would give the session only 6.25h / 24h of a day's decay, which flatters
    anyone who buys and sells inside the session.
  * implied volatility moves against the index (``iv_beta``): calls lose IV as the index rises, puts gain it
    as the index falls - the usual spot/vol relationship.

IV level comes from India VIX (previous close). This is a model, not market data: results built on it
are an estimate until re-run on recorded option prices.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from market_data.trading_days import TradingCalendar

SESSION_MINUTES = 375          # 09:15 -> 15:30
SESSION_SHARE = 0.7            # share of a trading day's variance realised inside the session
TRADING_DAYS_PER_YEAR = 252
RISK_FREE = 0.065


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot: float, strike: float, t_years: float, vol: float, right: str, r: float = RISK_FREE) -> float:
    """European option value; ``right`` is "CE" or "PE". Intrinsic value at or after expiry."""
    if t_years <= 1e-9 or vol <= 0:
        return max(0.0, spot - strike) if right == "CE" else max(0.0, strike - spot)
    fwd = spot * math.exp(r * t_years)
    sd = vol * math.sqrt(t_years)
    d1 = (math.log(fwd / strike) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    df = math.exp(-r * t_years)
    if right == "CE":
        return df * (fwd * _ncdf(d1) - strike * _ncdf(d2))
    return df * (strike * _ncdf(-d2) - fwd * _ncdf(-d1))


def variance_time_years(day: date, minutes_into_session: float, expiry: date, calendar: TradingCalendar) -> float:
    """Variance time left until 15:30 on ``expiry``, in years of 252 trading days."""
    left_today = max(0.0, SESSION_MINUTES - minutes_into_session) / SESSION_MINUTES
    units = SESSION_SHARE * left_today
    d = day + timedelta(days=1)
    while d <= expiry:
        if calendar.is_trading_day(d):
            units += 1.0
        d += timedelta(days=1)
    return units / TRADING_DAYS_PER_YEAR


def shifted_iv(iv_at_entry: float, spot: float, spot_at_entry: float, iv_beta: float) -> float:
    """IV after the index moved from ``spot_at_entry`` to ``spot`` (floored at half the entry IV)."""
    return iv_at_entry * max(0.5, 1.0 - iv_beta * (spot / spot_at_entry - 1.0))
