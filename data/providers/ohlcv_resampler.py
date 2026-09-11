"""
OHLCV Time-Grid Resampler
=========================
Post-aggregation engine that ensures a continuous, fixed-gap time series
with zero missing intervals on the timeline.

Two responsibilities:

1. **Dynamic session boundaries** — each symbol type has a parameterized
   session end time (e.g. 15:15 for standard equities, 15:30 for indices,
   15:39 for derivatives). The time grid always starts at 09:15 (NSE open).

2. **Missing-data imputation** — any aggregation bucket that has no source
   data (illiquidity, network drops, holidays within the range) is imputed:

   - Price (O, H, L, C): forward-filled from the ``close`` of the most recent
     valid candle.
   - Volume: explicitly set to ``0`` for any artificially filled interval.

The engine is intentionally decoupled from DuckDB: it receives the already
aggregated records (list of dicts) and returns a new list of dicts with the
complete time grid. This keeps the SQL layer simple and puts the time-series
logic in plain Python, which is easier to test and maintain.
"""
from datetime import datetime, timedelta, date, time

from .candle_aggregator import CandleInterval, is_intraday

# Session end times per symbol type. The grid always starts at 09:15.
SESSION_END_TIMES = {
    "equity": time(15, 15),
    "spot": time(15, 30),
    "vix": time(15, 30),
    "options": time(15, 39),
}

# Interval durations in minutes (only for intraday intervals).
INTERVAL_MINUTES = {
    CandleInterval.ONE_MINUTE: 1,
    CandleInterval.THREE_MINUTE: 3,
    CandleInterval.FIVE_MINUTE: 5,
    CandleInterval.TEN_MINUTE: 10,
    CandleInterval.FIFTEEN_MINUTE: 15,
    CandleInterval.THIRTY_MINUTE: 30,
    CandleInterval.ONE_HOUR: 60,
}

MARKET_OPEN = time(9, 15)


def _parse_date(d):
    """Parse a date string or date object to a date."""
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


def _parse_ts(t):
    """Parse a timestamp string or datetime to a datetime."""
    if isinstance(t, datetime):
        return t
    return datetime.fromisoformat(str(t))


def _session_end(symbol_type):
    """Return the session end time for a symbol type."""
    return SESSION_END_TIMES.get(symbol_type, time(15, 30))


def generate_intraday_grid(from_date, to_date, interval, symbol_type="spot"):
    """
    Generate a complete intraday time grid from 09:15 to session_end for each
    trading day in the date range.

    Args:
        from_date: Start date (YYYY-MM-DD).
        to_date: End date (YYYY-MM-DD). If None, defaults to from_date.
        interval: CandleInterval (must be an intraday interval).
        symbol_type: Symbol type for session end lookup.

    Returns:
        List of datetime objects representing bucket start times.
    """
    from_d = _parse_date(from_date)
    to_d = _parse_date(to_date) if to_date else from_d
    interval_min = INTERVAL_MINUTES.get(interval)
    if interval_min is None:
        raise ValueError(f"Cannot generate intraday grid for interval {interval}")

    session_end = _session_end(symbol_type)
    grid = []
    current_date = from_d
    while current_date <= to_d:
        bucket_start = datetime.combine(current_date, MARKET_OPEN)
        session_end_dt = datetime.combine(current_date, session_end)
        while bucket_start <= session_end_dt:
            grid.append(bucket_start)
            bucket_start += timedelta(minutes=interval_min)
        current_date += timedelta(days=1)
    return grid


def generate_daily_grid(from_date, to_date):
    """Generate a daily time grid (one bucket per calendar day)."""
    from_d = _parse_date(from_date)
    to_d = _parse_date(to_date) if to_date else from_d
    grid = []
    current_date = from_d
    while current_date <= to_d:
        grid.append(datetime.combine(current_date, time(0, 0)))
        current_date += timedelta(days=1)
    return grid


def generate_weekly_grid(from_date, to_date):
    """Generate a weekly time grid (one bucket per week, starting Monday)."""
    from_d = _parse_date(from_date)
    to_d = _parse_date(to_date) if to_date else from_d
    monday = from_d - timedelta(days=from_d.weekday())
    grid = []
    current = monday
    while current <= to_d:
        grid.append(datetime.combine(current, time(0, 0)))
        current += timedelta(weeks=1)
    return grid


def generate_monthly_grid(from_date, to_date):
    """Generate a monthly time grid (one bucket per month)."""
    from_d = _parse_date(from_date)
    to_d = _parse_date(to_date) if to_date else from_d
    grid = []
    current = date(from_d.year, from_d.month, 1)
    while current <= to_d:
        grid.append(datetime.combine(current, time(0, 0)))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return grid


def resample_and_fill(records, interval, from_date, to_date=None, symbol_type="spot"):
    """
    Ensure a continuous time series by filling gaps in aggregated data.

    Takes already-aggregated records and returns a new list with a complete
    time grid. Missing intervals are forward-filled (prices) or zero-filled
    (volume).

    Args:
        records: List of dicts with keys: trade_time, open, high, low, close, volume.
        interval: CandleInterval used for aggregation.
        from_date: Start date (YYYY-MM-DD).
        to_date: End date (YYYY-MM-DD). If None, defaults to from_date.
        symbol_type: Symbol type for session end lookup.

    Returns:
        List of dicts with complete time grid, forward-filled prices, and
        zero-filled volume for imputed intervals.
    """
    if not records:
        return []

    # Parse records into a dict keyed by trade_time
    aggregated = {}
    for r in records:
        ts = _parse_ts(r['trade_time'])
        aggregated[ts] = {
            'open': r.get('open'),
            'high': r.get('high'),
            'low': r.get('low'),
            'close': r.get('close'),
            'volume': r.get('volume'),
        }

    # Generate the complete time grid
    if is_intraday(interval):
        grid = generate_intraday_grid(from_date, to_date, interval, symbol_type)
    elif interval == CandleInterval.ONE_DAY:
        grid = generate_daily_grid(from_date, to_date)
    elif interval == CandleInterval.WEEK:
        grid = generate_weekly_grid(from_date, to_date)
    elif interval == CandleInterval.MONTH:
        grid = generate_monthly_grid(from_date, to_date)
    else:
        return records

    # Build the complete series with forward-fill
    result = []
    last_valid = None

    # Find the first valid data point for backward-fill (in case the grid
    # starts before any actual data — e.g. from_date has no data at 09:15).
    first_valid = None
    for ts in grid:
        if ts in aggregated and aggregated[ts]['close'] is not None:
            first_valid = aggregated[ts]
            break

    for ts in grid:
        if ts in aggregated and aggregated[ts]['close'] is not None:
            candle = aggregated[ts]
            result.append({
                'trade_time': ts,
                'open': candle['open'],
                'high': candle['high'],
                'low': candle['low'],
                'close': candle['close'],
                'volume': candle['volume'] if candle['volume'] is not None else 0,
            })
            last_valid = candle
        else:
            # Forward-fill from last valid candle; if none yet, backward-fill
            # from the first valid candle so the grid always starts at 09:15.
            fill = last_valid if last_valid is not None else first_valid
            if fill is not None:
                result.append({
                    'trade_time': ts,
                    'open': fill['close'],
                    'high': fill['close'],
                    'low': fill['close'],
                    'close': fill['close'],
                    'volume': 0,
                })

    return result