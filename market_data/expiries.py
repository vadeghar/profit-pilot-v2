"""NIFTY derivative expiry dates and lot sizes (calendar rules, no broker call).

Weekly options expire on Tuesday since Sep-2025 (Thursday before); monthly contracts on the last such
weekday of the month. An expiry that falls on a holiday moves to the previous trading day.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from market_data.trading_days import TradingCalendar

WEEKLY_EXPIRY_WEEKDAY_CHANGE_DATE = date(2025, 9, 1)  # NIFTY expiry moved Thu -> Tue
# (effective from, lot size): 75 from the Nov-2024 revision, 65 from Jan-2026.
NIFTY_LOT_SIZES = ((date(2025, 1, 1), 75), (date(2026, 1, 1), 65))


def weekly_expiry_weekday(day: date) -> int:
    """0=Mon..6=Sun. NIFTY weeklies: Tuesday from Sep-2025, Thursday before."""
    return 1 if day >= WEEKLY_EXPIRY_WEEKDAY_CHANGE_DATE else 3


def resolve_weekly_expiry(entry_day: date, calendar: TradingCalendar) -> date:
    """Next weekly expiry on/after ``entry_day``, rolled back a day on holidays."""
    weekday = weekly_expiry_weekday(entry_day)
    delta = (weekday - entry_day.weekday()) % 7
    candidate = entry_day + timedelta(days=delta)
    return calendar.previous_trading_day(candidate)


def monthly_expiry_from_calendar(year: int, month: int, holidays: Iterable[date] = ()) -> date:
    """Monthly expiry by the NSE rule: last Thursday through Aug-2025, last Tuesday thereafter."""
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


def nifty_lot_size(day: date) -> int:
    """NIFTY lot size in force on ``day`` (the latest regime for earlier dates than the table covers)."""
    sizes = [lot for start, lot in NIFTY_LOT_SIZES if start <= day]
    return sizes[-1] if sizes else NIFTY_LOT_SIZES[-1][1]
