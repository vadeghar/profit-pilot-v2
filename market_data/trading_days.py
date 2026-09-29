"""Trading-day calendar and a per-day Breeze fetcher.

``TradingDayFetcher`` issues one request per *trading day* and never asks the
broker for weekends or exchange holidays.  Candles are held in memory only; the
wrapped provider should be built with ``persist_cache=False``.
"""
from __future__ import annotations

import json
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from market_data.normalize import NormalizedCandle, ensure_normalized_candles
from market_data.rate_limiter import get_limiter
from utils.timezone import IST, ensure_ist

HOLIDAY_FILE = Path(__file__).resolve().parents[1] / "nse_holidays.json"
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
NO_DATA_MARKER = "No Breeze data returned"


class TradingCalendar:
    """Weekday + exchange-holiday calendar (holidays from ``nse_holidays.json``)."""

    def __init__(self, holidays: Iterable[date | str] = (), holiday_file: str | Path | None = HOLIDAY_FILE):
        self.holidays: set[date] = {_d(h) for h in holidays}
        path = Path(holiday_file) if holiday_file else None
        if path and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            values = data.get("holidays", data) if isinstance(data, dict) else data
            self.holidays.update(_d(v["date"] if isinstance(v, dict) else v) for v in values)

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def previous_trading_day(self, day: date) -> date:
        """``day`` itself if tradable, otherwise the closest earlier trading day."""
        while not self.is_trading_day(day):
            day -= timedelta(days=1)
        return day

    def trading_days(self, start: date, end: date) -> Iterator[date]:
        day = start
        while day <= end:
            if self.is_trading_day(day):
                yield day
            day += timedelta(days=1)

    def last_friday(self, year: int, month: int) -> date:
        """Last Friday of the month, rolled back to a trading day (R2)."""
        nxt = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
        day = nxt - timedelta(days=1)
        while day.weekday() != 4:
            day -= timedelta(days=1)
        return self.previous_trading_day(day)


def _d(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


class TradingDayFetcher:
    """Fetch normalized candles one trading day at a time, straight from Breeze.

    When ``cache`` is given (see ``market_data.cache.CandleCache``), a full
    trading day already in the past is served from disk without ever
    touching the network; only a cache miss (or "today", which is never
    cached) reaches the provider. Exchange history - including an expired
    option contract's - never changes once the session has closed, so a
    past-day hit is permanent.
    """

    def __init__(self, provider: Any, calendar: TradingCalendar | None = None,
                 throttle_seconds: float = 0.6, retries: int = 1,
                 sleep: Callable[[float], None] = _time.sleep,
                 cache: Any = None):
        self.provider = provider
        self.calendar = calendar or TradingCalendar()
        self.throttle_seconds = throttle_seconds
        self.retries = retries
        self._sleep = sleep
        self.cache = cache
        self.requests = 0
        self.skipped_days: set[date] = set()

    def fetch(self, instrument: Any, timeframe: str, start: datetime, end: datetime) -> list[NormalizedCandle]:
        start, end = ensure_ist(start), ensure_ist(end)
        out: dict[datetime, NormalizedCandle] = {}
        day = start.date()
        while day <= end.date():
            if not self.calendar.is_trading_day(day):
                self.skipped_days.add(day)
            else:
                lo = max(start, datetime.combine(day, SESSION_OPEN, IST))
                hi = min(end, datetime.combine(day, SESSION_CLOSE, IST))
                if lo <= hi:
                    full_day = (lo == datetime.combine(day, SESSION_OPEN, IST)
                               and hi == datetime.combine(day, SESSION_CLOSE, IST))
                    for candle in self._one_day(instrument, timeframe, lo, hi, day, full_day):
                        out[candle.timestamp] = candle
            day += timedelta(days=1)
        return [out[k] for k in sorted(out)]

    def _one_day(self, instrument: Any, timeframe: str, lo: datetime, hi: datetime,
                day: date, full_day: bool) -> list[NormalizedCandle]:
        if self.cache is not None and full_day:
            cached = self.cache.get_day(self.provider.name, instrument, timeframe, day)
            if cached is not None:
                return cached
        for attempt in range(self.retries + 1):
            self.requests += 1
            try:
                with get_limiter(getattr(self.provider, "name", "unknown")):
                    rows = ensure_normalized_candles(
                        self.provider.get_historical_candles(instrument, timeframe, lo, hi), "breeze day fetch")
            except RuntimeError as exc:
                if NO_DATA_MARKER not in str(exc):
                    raise  # auth/session failures must stop the run
                rows = []
            self._sleep(self.throttle_seconds)
            if rows:
                if self.cache is not None and full_day:
                    self.cache.put_day(self.provider.name, instrument, timeframe, day, rows)
                return rows
            if attempt < self.retries:
                self._sleep(1.5)
        return []
