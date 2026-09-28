"""Independent, pure reference implementation of the NIFTY No Brainer rules.

This module deliberately has no dependency on the production strategy or broker
adapters.  Prices are per index point; quantities are expressed in lots and the
caller supplies the period-correct contract-master lot size.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class Strikes:
    atm: int
    near_buy: int
    sell: int
    hedge: int


@dataclass(frozen=True)
class EntryDecision:
    decision: str
    strikes: Strikes
    net_premium: float
    net_premium_pct: float
    steps: int = 0
    reason: str = ""


@dataclass(frozen=True)
class ExitDecision:
    reason: str | None
    fill_pnl: float | None = None


def atm_from_spot(spot: float, grid: int = 100) -> int:
    """Video ATM rule: nearest 50, with the supplied upward 50-band behavior."""
    # The video vectors define the lower half of each 100 band as selecting
    # the upper 50 (24,830 -> 24,900), while 24,820 remains at 24,800.
    lower = int(spot // grid) * grid
    return lower + grid if spot - lower >= 30 else lower


def hedge_strike(sell: int, low: int = 800, high: int = 1400,
                 target: int = 1000, grid: int = 500) -> int:
    candidates = [s for s in range(sell + low, sell + high + 1)
                  if s % grid == 0]
    if not candidates:
        raise ValueError("No hedge strike in configured distance band")
    return min(candidates, key=lambda s: (abs(s - sell - target), -s))


def select_strikes(spot: float, near_offset: int = 300,
                   sell_gap: int = 300, **hedge_params: int) -> Strikes:
    atm = atm_from_spot(spot)
    near = atm + near_offset
    sell = near + sell_gap
    return Strikes(atm, near, sell, hedge_strike(sell, **hedge_params))


def net_premium(prices: Mapping[str, float], lot_size: int = 1) -> float:
    """Credit positive, debit negative: 2*sell - near_buy - hedge."""
    return (2 * prices["sell"] - prices["near_buy"] - prices["hedge"]) * lot_size


def downside_pnl(prices: Mapping[str, float], lot_size: int = 1) -> float:
    return net_premium(prices, lot_size)


def net_premium_pct(prices: Mapping[str, float], margin: float,
                    lot_size: int = 1) -> float:
    if margin <= 0:
        raise ValueError("margin must be positive")
    return net_premium(prices, lot_size) / margin


def shift_until_credit_ok(spot: float, price_for: Callable[[Strikes], Mapping[str, float]],
                          margin: float, lot_size: int = 1, band: float = .01,
                          max_steps: int | None = None,
                          hedge_recalc_on_shift: bool = True, **hedge_params: int) -> EntryDecision:
    step = 0
    while max_steps is None or step <= max_steps:
        offset = 300 + 100 * step
        params = hedge_params if hedge_recalc_on_shift else {"low": hedge_params.get("low", 800),
                  "high": hedge_params.get("high", 1400), "target": hedge_params.get("target", 1000),
                  "grid": hedge_params.get("grid", 500)}
        strikes = select_strikes(spot, offset, **params)
        premium = net_premium(price_for(strikes), lot_size)
        pct = premium / margin
        if premium <= margin * band:
            if premium < -margin * band:
                return EntryDecision("SKIP_DEBIT", strikes, premium, pct, step,
                                     "net debit exceeds 1% of deployed margin")
            return EntryDecision("TRADE", strikes, premium, pct, step)
        step += 1
    return EntryDecision("ABORT", strikes, premium, pct, step, "maximum shift steps exhausted")


def entry_decision(spot: float, prices: Mapping[str, float], margin: float,
                   lot_size: int = 1, max_shift_steps: int | None = None,
                   price_for: Callable[[Strikes], Mapping[str, float]] | None = None,
                   **hedge_params: int) -> EntryDecision:
    strikes = select_strikes(spot, **hedge_params)
    premium = net_premium(prices, lot_size)
    pct = premium / margin
    if premium < -margin * .01:
        return EntryDecision("SKIP_DEBIT", strikes, premium, pct, 0,
                             "net debit exceeds 1% of deployed margin")
    if premium > margin * .01:
        return shift_until_credit_ok(spot, price_for or (lambda candidate: prices),
                                     margin, lot_size, max_steps=max_shift_steps, **hedge_params)
    return EntryDecision("TRADE", strikes, premium, pct)


def exit_evaluator(mtm: float, margin: float, entry_at: datetime, now: datetime,
                   expiry: date, max_hold_days: int = 18, gap_through_pnl: float | None = None,
                   target_pct: float = .025, stop_pct: float = .03,
                   exit_time: time = time(15, 16)) -> ExitDecision:
    if mtm >= margin * target_pct:
        return ExitDecision("TARGET", mtm)
    if mtm <= -margin * stop_pct:
        return ExitDecision("STOP_LOSS", gap_through_pnl if gap_through_pnl is not None else mtm)
    deadline = entry_at.date() + timedelta(days=max_hold_days)
    if now.date() >= deadline and now.timetz().replace(tzinfo=None) >= exit_time:
        return ExitDecision("MAX_HOLD", mtm)
    if now.date() >= expiry and now.timetz().replace(tzinfo=None) >= exit_time:
        return ExitDecision("EXPIRY", mtm)
    return ExitDecision(None)


def payoff_table(strikes: Strikes, prices: Mapping[str, float], spots: Sequence[float],
                 lot_size: int = 1, margin: float = 1.0) -> list[dict[str, float]]:
    debit = prices["near_buy"] + prices["hedge"] - 2 * prices["sell"]
    rows = []
    for spot in spots:
        intrinsic = max(spot - strikes.near_buy, 0) + max(spot - strikes.hedge, 0) - 2 * max(spot - strikes.sell, 0)
        pnl = (intrinsic - debit) * lot_size
        rows.append({"spot": spot, "pnl": pnl, "pnl_pct_margin": pnl / margin})
    return rows


def last_friday(year: int, month: int, holidays: Iterable[date] = ()) -> date:
    day = date(year + (month == 12), 1 if month == 12 else month + 1, 1) - timedelta(days=1)
    while day.weekday() != 4:
        day -= timedelta(days=1)
    holiday_set = set(holidays)
    while day.weekday() >= 5 or day in holiday_set:
        day -= timedelta(days=1)
    return day


def monthly_expiry_from_master(year: int, month: int,
                               contract_master: Iterable[Mapping[str, object]]) -> date:
    rows = [r for r in contract_master if str(r.get("underlying", "NIFTY")).upper() == "NIFTY"
            and str(r.get("expiry_type", "MONTHLY")).upper() == "MONTHLY"
            and str(r.get("expiry", "")).startswith(f"{year:04d}-{month:02d}")]
    if not rows:
        raise ValueError("monthly NIFTY expiry missing from contract master")
    return min(date.fromisoformat(str(r["expiry"])) for r in rows)


def monthly_expiry_from_calendar(year: int, month: int,
                                 holidays: Iterable[date] = ()) -> date:
    """Fallback NSE rule: Thursday through Aug-2025, Tuesday thereafter."""
    weekday = 3 if (year, month) < (2025, 9) else 1
    if month == 12:
        day = date(year, 12, 31)
    else:
        day = date(year, month + 1, 1) - timedelta(days=1)
    day -= timedelta(days=(day.weekday() - weekday) % 7)
    holidays = set(holidays)
    while day.weekday() >= 5 or day in holidays:
        day -= timedelta(days=1)
    return day