"""
Spot Candles Provider
Fetches 1-minute NIFTY spot candles from nifty_spot table
"""
import pandas as pd
from .db import get_db_connection
from .candle_aggregator import build_ohlcv_aggregation


def df_to_records(df: pd.DataFrame):
    """Convert a DataFrame to JSON-safe dicts.
    
    Replaces NaN/NaT with None -- pandas NaN is not valid JSON and makes
    FastAPI's json.dumps raise "Out of range float values are not JSON
    compliant: nan". Also normalises timestamps to ISO strings.
    """
    df = df.astype(object).where(df.notna(), None)
    return df.to_dict(orient="records")


def get_spot_candles(trade_date, interval=None):
    """
    Fetch NIFTY spot candles for a given trading day, optionally aggregated
    into OHLCV buckets via the ``interval`` parameter.

    Args:
        trade_date: Trading date (YYYY-MM-DD)
        interval (CandleInterval or str, optional): Aggregation interval.
            Supported: ONE_MINUTE, THREE_MINUTE, FIVE_MINUTE, TEN_MINUTE,
            FIFTEEN_MINUTE, THIRTY_MINUTE, ONE_HOUR, ONE_DAY, WEEK, MONTH.

    Returns:
        List of dicts with keys: trade_time, trade_date, open, high, low,
        close, volume. With ``interval`` set, ``trade_time`` is the bucket
        start timestamp.
    """
    con = get_db_connection()
    try:
        query = (
            "SELECT trade_time, trade_date, open, high, low, close, volume "
            "FROM nifty_spot WHERE trade_date = ?"
        )
        if interval:
            query = build_ohlcv_aggregation(query, interval, ts_col="trade_time")
            df = con.execute(query, [trade_date]).df()
            # Reinstate trade_date (bucket date) for compatibility.
            df["trade_date"] = df["trade_time"].dt.date
            df = df[["trade_time", "trade_date", "open", "high", "low", "close", "volume"]]
        else:
            df = con.execute(query + " ORDER BY trade_time ASC", [trade_date]).df()
        return df_to_records(df)
    finally:
        con.close()