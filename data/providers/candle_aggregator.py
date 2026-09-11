"""
Candle OHLCV Interval Aggregator
=================================
Aggregates raw 1-minute OHLCV candles into higher time intervals
(3-min, 5-min, 15-min, 1-hour, daily, weekly, monthly, ...).

This is used by the symbol-based candle endpoints (``equity_spot``,
``nifty_spot``, ``india_vix``). Institutional data (``institutional_data``)
is *not* OHLCV candle data and is therefore excluded from interval
aggregation.

Aggregation rules per bucket:
    open   = FIRST(open)   ordered by trade_time
    high   = MAX(high)
    low    = MIN(low)
    close  = LAST(close)   ordered by trade_time
    volume = SUM(volume)
"""
from enum import Enum


class CandleInterval(str, Enum):
    """Supported candle aggregation intervals."""

    ONE_MINUTE = "ONE_MINUTE"
    THREE_MINUTE = "THREE_MINUTE"
    FIVE_MINUTE = "FIVE_MINUTE"
    TEN_MINUTE = "TEN_MINUTE"
    FIFTEEN_MINUTE = "FIFTEEN_MINUTE"
    THIRTY_MINUTE = "THIRTY_MINUTE"
    ONE_HOUR = "ONE_HOUR"
    ONE_DAY = "ONE_DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"


# Intraday intervals whose first candle of a session must start at 09:15.
# For these intervals, pre-market data (before 09:15) is excluded from aggregation.
# THIRTY_MINUTE is also anchored at 09:15 (buckets: 09:15, 09:45, 10:15, ...).
# ONE_DAY / WEEK / MONTH use date_trunc and are NOT in this set.
INTRADAY_INTERVALS = {
    CandleInterval.ONE_MINUTE,
    CandleInterval.THREE_MINUTE,
    CandleInterval.FIVE_MINUTE,
    CandleInterval.TEN_MINUTE,
    CandleInterval.FIFTEEN_MINUTE,
    CandleInterval.THIRTY_MINUTE,
    CandleInterval.ONE_HOUR,
}


def is_intraday(interval):
    """Return True if the interval is an intraday interval anchored at 09:15."""
    key = interval if isinstance(interval, CandleInterval) else CandleInterval(interval)
    return key in INTRADAY_INTERVALS
# - All intraday intervals (ONE_MINUTE..THIRTY_MINUTE, ONE_HOUR) are aligned to
#   the market session start so the first candle always begins at 09:15 (using a
#   custom origin to ``time_bucket``). 09:15 is the daily market open and the
#   convention every data endpoint must preserve.
# - ``time_bucket`` with 3 args: time_bucket(INTERVAL 'X', ts, origin). Because
#   1/3/5/10/15/30/60 minutes all divide a day evenly, a single fixed origin keeps
#   every successive day aligned at 09:15.
# - ONE_DAY / WEEK / MONTH use ``date_trunc`` (day / week / month boundaries).
_MARKET_OPEN = "TIMESTAMP '2000-01-01 09:15:00'"
_BUCKET_EXPRS = {
    CandleInterval.ONE_MINUTE: f"time_bucket(INTERVAL '1 minute', {{ts}}, {_MARKET_OPEN})",
    CandleInterval.THREE_MINUTE: f"time_bucket(INTERVAL '3 minutes', {{ts}}, {_MARKET_OPEN})",
    CandleInterval.FIVE_MINUTE: f"time_bucket(INTERVAL '5 minutes', {{ts}}, {_MARKET_OPEN})",
    CandleInterval.TEN_MINUTE: f"time_bucket(INTERVAL '10 minutes', {{ts}}, {_MARKET_OPEN})",
    CandleInterval.FIFTEEN_MINUTE: f"time_bucket(INTERVAL '15 minutes', {{ts}}, {_MARKET_OPEN})",
    CandleInterval.THIRTY_MINUTE: f"time_bucket(INTERVAL '30 minutes', {{ts}}, {_MARKET_OPEN})",
    CandleInterval.ONE_HOUR: f"time_bucket(INTERVAL '1 hour', {{ts}}, {_MARKET_OPEN})",
    CandleInterval.ONE_DAY: "date_trunc('day', {ts})",
    CandleInterval.WEEK: "date_trunc('week', {ts})",
    CandleInterval.MONTH: "date_trunc('month', {ts})",
}


def bucket_expr(interval, ts_col):
    """
    Return the DuckDB GROUP BY bucket expression for the given interval.

    Args:
        interval: CandleInterval or its value string (e.g. "FIVE_MINUTE").
        ts_col: Name of the timestamp column to bucket by.

    Returns:
        str: DuckDB expression producing a bucket start timestamp.

    Raises:
        ValueError: If interval is not a supported CandleInterval.
    """
    key = interval if isinstance(interval, CandleInterval) else CandleInterval(interval)
    return _BUCKET_EXPRS[key].format(ts=ts_col)


def build_ohlcv_aggregation(source_select, interval, ts_col="trade_time", key_cols=None, limit=None):
    """
    Wrap a raw 1-minute candle SELECT into an OHLCV aggregation query.

    Args:
        source_select: SQL SELECT that returns raw candles and must expose
            the key columns, ``ts_col`` and ``open/high/low/close/volume``.
        interval: CandleInterval or its value string.
        ts_col: Name of the timestamp column to bucket by (default ``trade_time``).
        key_cols: List of grouping identity columns (e.g. ``["symbol"]``).
        limit: Optional int. Caps the result to the ``limit`` most recent
            aggregation buckets, returned in ascending time order. Useful to
            enforce a maximum candle count after aggregation.

    Returns:
        str: Complete SQL query that aggregates the source into OHLCV buckets.
            Output columns: key_cols..., trade_time, open, high, low, close, volume
    """
    bucket = bucket_expr(interval, ts_col)
    key_cols = key_cols or []
    keys_with_alias = ", ".join(key_cols + [f"{bucket} AS trade_time"])
    group_keys = ", ".join(key_cols + [bucket])
    # The derived aggregating sub-query exposes the bucket as the aliased
    # ``trade_time`` column, so ordering can use the stable select-list aliases.
    order_aliases = ", ".join(key_cols + ["trade_time"])

    inner = f"""
        SELECT {keys_with_alias},
               FIRST(open ORDER BY {ts_col}) AS open,
               MAX(high) AS high,
               MIN(low) AS low,
               LAST(close ORDER BY {ts_col}) AS close,
               SUM(volume) AS volume
        FROM ( {source_select} )
        GROUP BY {group_keys}
    """

    if limit:
        # Return the most recent ``limit`` buckets, re-ordered ascending.
        return f"""
            SELECT * FROM (
                {inner}
                ORDER BY {order_aliases} DESC
                LIMIT {int(limit)}
            ) AS candles
            ORDER BY {order_aliases} ASC
        """
    return f"{inner}\n        ORDER BY {order_aliases} ASC"