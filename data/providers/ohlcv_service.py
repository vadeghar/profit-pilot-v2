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
  - enforces a resolution-based request-range cap per interval.

Resolution-Based Caps (calendar days):
  - ONE_MINUTE                      -> 7 days   (~2,625 candles per symbol)
  - THREE_MINUTE .. FIFTEEN_MINUTE  -> 30 days
  - ONE_DAY / WEEK / MONTH          -> up to 5 years

Ranges wider than the interval's cap are rejected with HTTP 400. When
`to_date` is None, the range implicitly extends `cap` days past `from_date`
and the first buckets in that range are returned (ascending order). When
`to_date` is provided, all buckets within the validated range are returned,
with gap-filling for a continuous time series.

Institutional data (`institutional_data`) is NOT OHLCV candle data and is
therefore excluded from this service.
"""
from datetime import date, datetime, timedelta

from fastapi import HTTPException

from .db import get_db_connection
from .candle_aggregator import CandleInterval, build_ohlcv_aggregation, is_intraday
from .ohlcv_resampler import resample_and_fill

# ---------------------------------------------------------------------------
# Resolution-Based Request-Range Caps (calendar days), replacing the old flat
# 800-candle response limit.
#
#   ONE_MINUTE                       -> 7 days  (~2,625 candles per symbol)
#   THREE_MINUTE .. FIFTEEN_MINUTE   -> 30 days
#   THIRTY_MINUTE / ONE_HOUR         -> 30 days (same multi-minute bucket)
#   ONE_DAY / WEEK / MONTH           -> up to 5 years (1,825 calendar days)
#
# THREE_MINUTE, THIRTY_MINUTE, ONE_HOUR, WEEK and MONTH are not listed in the
# resolution spec; the values below are inferred (multi-minute intraday shares
# the 30-day cap; weekly/monthly match the daily 5-year cap). Adjust freely.
# ---------------------------------------------------------------------------
INTERVAL_MAX_RANGE_DAYS = {
    CandleInterval.ONE_MINUTE: 7,
    CandleInterval.THREE_MINUTE: 30,
    CandleInterval.FIVE_MINUTE: 30,
    CandleInterval.TEN_MINUTE: 30,
    CandleInterval.FIFTEEN_MINUTE: 30,
    CandleInterval.THIRTY_MINUTE: 30,
    CandleInterval.ONE_HOUR: 30,
    CandleInterval.ONE_DAY: 5 * 365,
    CandleInterval.WEEK: 5 * 365,
    CandleInterval.MONTH: 5 * 365,
}

# Only these equity symbols are currently supported by /equity.
EQUITY_ALLOWLIST = [
    "AXISBANK", "BAJFINANCE", "HDFCBANK", "ICICIBANK",
    "ITC", "LT", "RELIANCE", "SBIN",
]

# /spot currently serves only NIFTY index data.
SPOT_SYMBOL = "NIFTY"


def _to_date(value):
    """Normalize a YYYY-MM-DD string or date object to a ``datetime.date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def resolve_request_range(from_date, to_date, interval):
    """
    Validate the requested date range against the interval's resolution-based
    cap and resolve the effective date range.

    Args:
        from_date: Start date (YYYY-MM-DD).
        to_date: Optional end date (YYYY-MM-DD). When omitted, the range is
            implicitly capped to ``max_range_days`` past ``from_date``.
        interval: CandleInterval.

    Returns:
        Tuple ``(effective_from_date, effective_to_date, apply_gap_fill)``:
        - ``to_date`` unchanged when supplied; ``from_date + cap`` when not;
        - ``apply_gap_fill`` = whether a ``to_date`` was originally supplied.

    Raises:
        HTTPException(400): if the range is inverted or exceeds the cap.
    """
    apply_gap_fill = to_date is not None
    key = interval if isinstance(interval, CandleInterval) else CandleInterval(interval)
    max_days = INTERVAL_MAX_RANGE_DAYS[key]
    from_d = _to_date(from_date)
    to_d = _to_date(to_date) if to_date else from_d + timedelta(days=max_days)

    if to_d < from_d:
        raise HTTPException(
            status_code=400,
            detail=f"toDate ({to_date}) cannot be before fromDate ({from_date}).",
        )
    if (to_d - from_d).days > max_days:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Requested range {from_date} -> {to_date} exceeds the "
                f"resolution-based cap of {max_days} calendar "
                f"{'day' if max_days == 1 else 'days'} for interval "
                f"'{key.value}'. Allowed caps: ONE_MINUTE = 7 days, "
                f"multi-minute intraday = 30 days, "
                f"ONE_DAY/WEEK/MONTH = up to 5 years."
            ),
        )
    return from_d.isoformat(), to_d.isoformat(), apply_gap_fill


def _df_to_records(df):
    """Convert a DataFrame to JSON-safe records."""
    df = df.astype(object).where(df.notna(), None)
    return df.to_dict(orient="records")


def _run(sql, params, interval, ts_col, key_cols, from_date, to_date,
         symbol_type="spot", apply_gap_fill=True):
    """
    Run an aggregated OHLCV query bounded by the interval's resolution-based
    range cap, then optionally resample and forward-fill gaps.

    When ``apply_gap_fill`` is False (no ``to_date`` supplied), the FIRST
    buckets from ``from_date`` are returned (ascending order) without
    resampling. When True, all buckets in the range are returned then
    resampled onto a continuous grid.

    Args:
        sql: Raw candle SELECT.
        params: Query parameters.
        interval: CandleInterval.
        ts_col: Timestamp column name.
        key_cols: Identity columns (e.g. ["symbol"]).
        from_date: Effective start date (YYYY-MM-DD).
        to_date: Effective end date (YYYY-MM-DD; never None here).
        symbol_type: Session boundary lookup key (e.g. "equity", "spot", "vix").
        apply_gap_fill: Resample/fill the series when True.
    """
    query = build_ohlcv_aggregation(sql, interval, ts_col=ts_col, key_cols=key_cols)
    con = get_db_connection()
    try:
        records = _df_to_records(con.execute(query, params).df())
    finally:
        con.close()

    if not records:
        return []

    # The resolution-based range cap (enforced in resolve_request_range) bounds
    # every legal request, so no extra count limit is applied here — doing so
    # would truncate legitimate multi-series requests (e.g. options CE + PE).
    if not apply_gap_fill:
        # First buckets within the capped range (ascending), no resampling.
        return records

    # Resample to a continuous grid and forward-fill any gaps.
    return resample_and_fill(records, interval, from_date, to_date,
                             symbol_type, key_cols)


def _date_bounds(from_date, to_date):
    """
    Build the SQL date-range predicate + params.

    ``from_date`` and ``to_date`` are the effective, resolution-capped range
    (never None), so the predicate always carries an upper bound.
    """
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
        to_date: Optional end date (YYYY-MM-DD); capped per resolution.
        interval: Aggregation interval.

    Returns:
        List of dicts: symbol, trade_time, open, high, low, close, volume
        (bounded by the interval's resolution-based range cap).
    """
    sym = (symbol or "").strip().upper()
    if sym not in EQUITY_ALLOWLIST:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported equity symbol '{symbol}'. Supported: {', '.join(EQUITY_ALLOWLIST)}",
        )
    from_d, to_d, apply_gap_fill = resolve_request_range(from_date, to_date, interval)
    bounds, date_params = _date_bounds(from_d, to_d)
    sql = (
        "SELECT symbol, trade_time, open, high, low, close, volume "
        "FROM equity_spot WHERE UPPER(symbol) = UPPER(?) "
        + bounds.format(col="trade_time")
    )
    if is_intraday(interval):
        sql += " AND CAST(trade_time AS TIME) >= CAST('09:15:00' AS TIME)"
    return _run(sql, [sym] + date_params, interval, "trade_time", ["symbol"],
                from_d, to_d, "equity", apply_gap_fill=apply_gap_fill)


def get_spot_candles(symbol, from_date, to_date=None, interval=None):
    """
    Aggregated OHLCV candles for NIFTY spot data.

    Args:
        symbol: Symbol — currently only "NIFTY" is supported.
        from_date: Start date (YYYY-MM-DD).
        to_date: Optional end date (YYYY-MM-DD); capped per resolution.
        interval: Aggregation interval.

    Returns:
        List of dicts: trade_time, open, high, low, close, volume
        (bounded by the interval's resolution-based range cap).
    """
    sym = (symbol or "").strip().upper()
    if sym != SPOT_SYMBOL:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported spot symbol '{symbol}'. Currently only '{SPOT_SYMBOL}' is supported.",
        )
    from_d, to_d, apply_gap_fill = resolve_request_range(from_date, to_date, interval)
    bounds, date_params = _date_bounds(from_d, to_d)
    sql = (
        "SELECT trade_time, open, high, low, close, volume "
        "FROM nifty_spot WHERE 1=1 " + bounds.format(col="trade_date")
    )
    if is_intraday(interval):
        sql += " AND CAST(trade_time AS TIME) >= CAST('09:15:00' AS TIME)"
    return _run(sql, date_params, interval, "trade_time", [],
                from_d, to_d, "spot", apply_gap_fill=apply_gap_fill)


def get_vix_candles(from_date, to_date=None, interval=None):
    """
    Aggregated OHLCV candles for VIX (india_vix).

    Args:
        from_date: Start date (YYYY-MM-DD).
        to_date: Optional end date (YYYY-MM-DD); capped per resolution. When
            omitted, the range is capped to the interval's maximum and the
            first buckets from `from_date` are returned (ascending order).
        interval: Aggregation interval.

    Returns:
        List of dicts: trade_time, open, high, low, close, volume
        (bounded by the interval's resolution-based range cap).
    """
    from_d, to_d, apply_gap_fill = resolve_request_range(from_date, to_date, interval)
    bounds, date_params = _date_bounds(from_d, to_d)
    sql = (
        "SELECT trade_time, open, high, low, close, volume "
        "FROM india_vix WHERE 1=1 " + bounds.format(col="trade_date")
    )
    if is_intraday(interval):
        sql += " AND CAST(trade_time AS TIME) >= CAST('09:15:00' AS TIME)"
    return _run(sql, date_params, interval, "trade_time", [],
                from_d, to_d, "vix", apply_gap_fill=apply_gap_fill)


def get_options_candles(strike, expiry, option_type=None, from_date=None, to_date=None, interval=None):
    """
    Aggregated OHLCV candles for option contracts.

    Args:
        strike: Strike price (int).
        expiry: Contract expiry date (YYYY-MM-DD).
        option_type: Optional single option type ("CE", "PE" or "FUT").
            Defaults to both CE and PE when omitted.
        from_date: Start date (YYYY-MM-DD).
        to_date: Optional end date (YYYY-MM-DD); capped per resolution. When
            omitted, the range is capped to the interval's maximum and the
            first buckets from `from_date` are returned (ascending order).
        interval: Aggregation interval.

    Returns:
        List of dicts: option_type, trade_time, open, high, low, close, volume
        (bounded by the interval's resolution-based range cap). When both CE
        and PE are requested, buckets are resampled independently per
        option_type so each series keeps its own grid. option_type is "CE" or
        "PE" (or "FUT" if filtered).
    """
    # options_ticks stores the closing price in the `close` column.
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

    from_d, to_d, apply_gap_fill = resolve_request_range(from_date, to_date, interval)
    bounds, date_params = _date_bounds(from_d, to_d)
    placeholders = ", ".join("?" for _ in opt_types)
    sql = (
        "SELECT trade_time, close AS open, close AS high, close AS low, "
        "close, volume, option_type "
        "FROM options_ticks "
        "WHERE strike_price = ? AND expiry_date = CAST(? AS DATE) "
        + bounds.format(col="trade_date")
        + f" AND UPPER(option_type) IN ({placeholders})"
    )
    if is_intraday(interval):
        sql += " AND CAST(trade_time AS TIME) >= CAST('09:15:00' AS TIME)"
    params = [strike, expiry] + date_params + opt_types
    return _run(sql, params, interval, "trade_time", ["option_type"],
                from_d, to_d, "options", apply_gap_fill=apply_gap_fill)
