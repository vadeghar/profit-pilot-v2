"""
Consolidated OHLCV Candle Service
=================================
Provides a single aggregated-OHLCV data source per market data type,
backing the consolidated endpoints:

    /equity  -> equity_spot   (per symbol)
    /spot    -> nifty_spot    (NIFTY index only)
    /vix     -> india_vix     (single instrument)
    /options -> options_ticks (per strike / expiry / option type)

Every function:
  - filters the raw 1-minute candles by a mandatory `from_date` and an
    optional `to_date`,
  - aggregates them into OHLCV buckets via the requested `interval`, and
  - caps the response to `MAX_CANDLES` (800) aggregated buckets.

When `to_date` is None, the first 800 buckets from `from_date` are returned
(ascending order). When `to_date` is provided, the most recent 800 buckets
within the date range are returned, with gap-filling for a continuous time
series.

Institutional data (`institutional_data`) is NOT OHLCV candle data and is
therefore excluded from this service.
"""
from fastapi import HTTPException

from .db import get_db_connection
from .candle_aggregator import build_ohlcv_aggregation, is_intraday
from .ohlcv_resampler import resample_and_fill

# Maximum number of aggregation buckets returned per request.
MAX_CANDLES = 800

# Only these equity symbols are currently supported by /equity.
EQUITY_ALLOWLIST = [
    "AXISBANK", "BAJFINANCE", "HDFCBANK", "ICICIBANK",
    "ITC", "LT", "RELIANCE", "SBIN",
]

# /spot currently serves only NIFTY index data.
SPOT_SYMBOL = "NIFTY"


def _df_to_records(df):
    """Convert a DataFrame to JSON-safe records."""
    df = df.astype(object).where(df.notna(), None)
    return df.to_dict(orient="records")


def _run(sql, params, interval, ts_col, key_cols, from_date, to_date=None, symbol_type="spot"):
    """
    Run an aggregated OHLCV query capped at MAX_CANDLES buckets, then
    resample and forward-fill gaps so the output is a continuous time series.

    When ``to_date`` is None, returns the FIRST MAX_CANDLES buckets from
    ``from_date`` (ascending order) without resampling.

    When ``to_date`` is provided, returns the most recent MAX_CANDLES buckets
    within the date range, then resamples to fill gaps.

    Args:
        sql: Raw candle SELECT.
        params: Query parameters.
        interval: CandleInterval.
        ts_col: Timestamp column name.
        key_cols: Identity columns (e.g. ["symbol"]).
        from_date: Start date (YYYY-MM-DD).
        to_date: Optional end date (YYYY-MM-DD).
        symbol_type: Session boundary lookup key (e.g. "equity", "spot", "vix").
    """
    if to_date is None:
        # Return the FIRST MAX_CANDLES buckets from from_date (ascending).
        # No resampling needed since data is contiguous within the returned range.
        query = build_ohlcv_aggregation(
            sql, interval, ts_col=ts_col, key_cols=key_cols
        )
        con = get_db_connection()
        try:
            records = _df_to_records(con.execute(query, params).df())
        finally:
            con.close()
        return records[:MAX_CANDLES]

    # to_date is provided: return the most recent MAX_CANDLES buckets within
    # the date range, then resample to fill gaps for a continuous series.
    query = build_ohlcv_aggregation(
        sql, interval, ts_col=ts_col, key_cols=key_cols, limit=MAX_CANDLES
    )
    con = get_db_connection()
    try:
        records = _df_to_records(con.execute(query, params).df())
    finally:
        con.close()

    if not records:
        return []

    # Resample to a continuous grid and forward-fill any gaps.
    resampled = resample_and_fill(records, interval, from_date, to_date, symbol_type)
    # Re-apply the cap after gap-filling so we never exceed MAX_CANDLES.
    if len(resampled) > MAX_CANDLES:
        resampled = resampled[-MAX_CANDLES:]
    return resampled


def _date_bounds(from_date, to_date):
    """
    Build the SQL date-range predicate + params.

    When ``to_date`` is omitted, NO upper bound is applied — every candle from
    ``from_date`` onward is considered (the response is still limited to
    MAX_CANDLES by the aggregator).
    """
    sql = "AND CAST({col} AS DATE) >= CAST(? AS DATE)"
    params = [from_date]
    if to_date:
        sql = (
            "AND CAST({col} AS DATE) >= CAST(? AS DATE) "
            "AND CAST({col} AS DATE) <= CAST(? AS DATE)"
        )
        params = [from_date, to_date]
    return sql, params


def get_equity_candles(symbol, from_date, to_date=None, interval=None):
    """
    Aggregated OHLCV candles for an allowed equity symbol.

    Args:
        symbol: Stock symbol (one of EQUITY_ALLOWLIST).
        from_date: Start date (YYYY-MM-DD).
        to_date: Optional end date (YYYY-MM-DD); defaults to from_date.
        interval: Aggregation interval.

    Returns:
        List of dicts: symbol, trade_time, open, high, low, close, volume
        (capped at MAX_CANDLES buckets).
    """
    sym = (symbol or "").strip().upper()
    if sym not in EQUITY_ALLOWLIST:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported equity symbol '{symbol}'. Supported: {', '.join(EQUITY_ALLOWLIST)}",
        )
    bounds, date_params = _date_bounds(from_date, to_date)
    sql = (
        "SELECT symbol, trade_time, open, high, low, close, volume "
        "FROM equity_spot WHERE UPPER(symbol) = UPPER(?) "
        + bounds.format(col="trade_time")
    )
    if is_intraday(interval):
        sql += " AND CAST(trade_time AS TIME) >= CAST('09:15:00' AS TIME)"
    return _run(sql, [sym] + date_params, interval, "trade_time", ["symbol"],
                from_date, to_date, "equity")


def get_spot_candles(symbol, from_date, to_date=None, interval=None):
    """
    Aggregated OHLCV candles for NIFTY spot data.

    Args:
        symbol: Symbol — currently only "NIFTY" is supported.
        from_date: Start date (YYYY-MM-DD).
        to_date: Optional end date (YYYY-MM-DD); defaults to from_date.
        interval: Aggregation interval.

    Returns:
        List of dicts: trade_time, open, high, low, close, volume
        (capped at MAX_CANDLES buckets).
    """
    sym = (symbol or "").strip().upper()
    if sym != SPOT_SYMBOL:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported spot symbol '{symbol}'. Currently only '{SPOT_SYMBOL}' is supported.",
        )
    bounds, date_params = _date_bounds(from_date, to_date)
    sql = (
        "SELECT trade_time, open, high, low, close, volume "
        "FROM nifty_spot WHERE 1=1 " + bounds.format(col="trade_date")
    )
    if is_intraday(interval):
        sql += " AND CAST(trade_time AS TIME) >= CAST('09:15:00' AS TIME)"
    return _run(sql, date_params, interval, "trade_time", [],
                from_date, to_date, "spot")


def get_vix_candles(from_date, to_date=None, interval=None):
    """
    Aggregated OHLCV candles for VIX (india_vix).

    Args:
        from_date: Start date (YYYY-MM-DD).
        to_date: Optional end date (YYYY-MM-DD). When omitted, the first 800
            candles from `from_date` are returned (ascending order).
        interval: Aggregation interval.

    Returns:
        List of dicts: trade_time, open, high, low, close, volume
        (capped at MAX_CANDLES buckets).
    """
    bounds, date_params = _date_bounds(from_date, to_date)
    sql = (
        "SELECT trade_time, open, high, low, close, volume "
        "FROM india_vix WHERE 1=1 " + bounds.format(col="trade_date")
    )
    if is_intraday(interval):
        sql += " AND CAST(trade_time AS TIME) >= CAST('09:15:00' AS TIME)"
    return _run(sql, date_params, interval, "trade_time", [],
                from_date, to_date, "vix")


def get_options_candles(strike, expiry, option_type=None, from_date=None, to_date=None, interval=None):
    """
    Aggregated OHLCV candles for option contracts.

    Args:
        strike: Strike price (int).
        expiry: Contract expiry date (YYYY-MM-DD).
        option_type: Optional single option type ("CE", "PE" or "FUT").
            Defaults to both CE and PE when omitted.
        from_date: Start date (YYYY-MM-DD).
        to_date: Optional end date (YYYY-MM-DD). When omitted, the first 800
            candles from `from_date` are returned (ascending order).
        interval: Aggregation interval.

    Returns:
        List of dicts: trade_time, open, high, low, close, volume
        (capped at MAX_CANDLES buckets).
    """
    # options_ticks stores the closing price in the `price` column.
    allowed = {"CE", "PE", "FUT"}
    if option_type:
        opt = option_type.strip().upper()
        if opt not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid option_type '{option_type}'. Allowed: CE, PE, FUT",
            )
        opt_types = [opt]
    else:
        opt_types = ["CE", "PE"]

    bounds, date_params = _date_bounds(from_date, to_date)
    placeholders = ", ".join("?" for _ in opt_types)
    sql = (
        "SELECT trade_time, price AS close, open, high, low, volume "
        "FROM options_ticks "
        "WHERE strike_price = ? AND expiry_date = CAST(? AS DATE) "
        + bounds.format(col="trade_date")
        + f" AND UPPER(option_type) IN ({placeholders})"
    )
    if is_intraday(interval):
        sql += " AND CAST(trade_time AS TIME) >= CAST('09:15:00' AS TIME)"
    params = [strike, expiry] + date_params + opt_types
    return _run(sql, params, interval, "trade_time", [],
                from_date, to_date, "options")
