"""
Equity Spot Data Provider
Fetches 1-minute equity spot candles and daily aggregates from equity_spot table.
Supports multiple Indian stocks (Nifty 50 / F&O stocks).
"""
import pandas as pd
from .db import get_db_connection
from .candle_aggregator import build_ohlcv_aggregation


def df_to_records(df: pd.DataFrame):
    """Convert a DataFrame to JSON-safe dicts."""
    df = df.astype(object).where(df.notna(), None)
    return df.to_dict(orient="records")


def get_equity_symbols():
    """Fetch all distinct symbols available in the equity_spot table."""
    con = get_db_connection()
    try:
        rows = con.execute(
            "SELECT DISTINCT symbol FROM equity_spot ORDER BY symbol ASC"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def get_equity_candles(
    symbol,
    trade_date=None,
    start_date=None,
    end_date=None,
    start_time=None,
    end_time=None,
    limit=None,
    interval=None
):
    """
    Fetch equity spot candles with flexible filtering, optionally aggregated
    into OHLCV buckets via the ``interval`` parameter.

    Query Params:
        symbol (str, required): Stock symbol (e.g., "RELIANCE", "HDFCBANK")
        trade_date (str, optional): Specific trading date (YYYY-MM-DD)
        start_date (str, optional): Start of date range (YYYY-MM-DD, inclusive)
        end_date (str, optional): End of date range (YYYY-MM-DD, inclusive)
        start_time (str, optional): Start time filter (HH:MM:SS) within dates
        end_time (str, optional): End time filter (HH:MM:SS) within dates
        limit (int, optional): Max number of rows to return
        interval (CandleInterval or str, optional): Aggregation interval.
            When provided, 1-minute candles are aggregated into OHLCV buckets.
            Supported values: ONE_MINUTE, THREE_MINUTE, FIVE_MINUTE,
            TEN_MINUTE, FIFTEEN_MINUTE, THIRTY_MINUTE, ONE_HOUR, ONE_DAY,
            WEEK, MONTH.

    Returns:
        List of dicts: symbol, trade_time, open, high, low, close, volume.
        With ``interval`` set, ``trade_time`` is the bucket start timestamp.
    """
    con = get_db_connection()
    try:
        query = "SELECT symbol, trade_time, open, high, low, close, volume FROM equity_spot WHERE 1=1"
        params = []

        if symbol:
            query += " AND UPPER(symbol) = UPPER(?)"
            params.append(symbol)
        if trade_date:
            query += " AND CAST(trade_time AS DATE) = CAST(? AS DATE)"
            params.append(trade_date)
        else:
            if start_date:
                query += " AND CAST(trade_time AS DATE) >= CAST(? AS DATE)"
                params.append(start_date)
            if end_date:
                query += " AND CAST(trade_time AS DATE) <= CAST(? AS DATE)"
                params.append(end_date)
        if start_time:
            query += " AND CAST(trade_time AS TIME) >= CAST(? AS TIME)"
            params.append(start_time)
        if end_time:
            query += " AND CAST(trade_time AS TIME) <= CAST(? AS TIME)"
            params.append(end_time)

        if interval:
            query = build_ohlcv_aggregation(
                query, interval, ts_col="trade_time", key_cols=["symbol"]
            )
        else:
            query += " ORDER BY symbol ASC, trade_time ASC"
            if limit:
                query += f" LIMIT {int(limit)}"

        df = con.execute(query, params).df()
        return df_to_records(df)
    finally:
        con.close()


def get_equity_daily(
    symbol=None,
    trade_date=None,
    start_date=None,
    end_date=None
):
    """
    Fetch daily aggregated OHLCV for equity spot data.

    Query Params:
        symbol (str, optional): Stock symbol filter
        trade_date (str, optional): Specific trading date (YYYY-MM-DD)
        start_date (str, optional): Start of date range (YYYY-MM-DD, inclusive)
        end_date (str, optional): End of date range (YYYY-MM-DD, inclusive)

    Returns:
        List of dicts: symbol, trade_date, open, high, low, close, volume
    """
    con = get_db_connection()
    try:
        query = """
            SELECT
                symbol,
                CAST(trade_time AS DATE) as trade_date,
                FIRST(open ORDER BY trade_time) as open,
                MAX(high) as high,
                MIN(low) as low,
                LAST(close ORDER BY trade_time) as close,
                SUM(volume) as volume
            FROM equity_spot
            WHERE 1=1
        """
        params = []

        if symbol:
            query += " AND UPPER(symbol) = UPPER(?)"
            params.append(symbol)
        if trade_date:
            query += " AND CAST(trade_time AS DATE) = CAST(? AS DATE)"
            params.append(trade_date)
        else:
            if start_date:
                query += " AND CAST(trade_time AS DATE) >= CAST(? AS DATE)"
                params.append(start_date)
            if end_date:
                query += " AND CAST(trade_time AS DATE) <= CAST(? AS DATE)"
                params.append(end_date)

        query += " GROUP BY symbol, CAST(trade_time AS DATE) ORDER BY symbol ASC, trade_date ASC"

        df = con.execute(query, params).df()
        return df_to_records(df)
    finally:
        con.close()


def get_equity_latest(symbol=None):
    """
    Fetch the most recent equity spot candle(s).

    Query Params:
        symbol (str, optional): Stock symbol. If None, returns latest per symbol.

    Returns:
        List of dicts: symbol, trade_time, open, high, low, close, volume
    """
    con = get_db_connection()
    try:
        if symbol:
            query = """
                SELECT symbol, trade_time, open, high, low, close, volume
                FROM equity_spot
                WHERE UPPER(symbol) = UPPER(?)
                ORDER BY trade_time DESC
                LIMIT 1
            """
            df = con.execute(query, [symbol]).df()
        else:
            query = """
                SELECT symbol, trade_time, open, high, low, close, volume
                FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_time DESC) as rn
                    FROM equity_spot
                ) sub
                WHERE rn = 1
                ORDER BY symbol ASC
            """
            df = con.execute(query).df()
        return df_to_records(df)
    finally:
        con.close()