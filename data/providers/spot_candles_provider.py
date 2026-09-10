"""
Spot Candles Provider
Fetches 1-minute NIFTY spot candles from nifty_spot table
"""
import pandas as pd
from .db import get_db_connection


def df_to_records(df: pd.DataFrame):
    """Convert a DataFrame to JSON-safe dicts.
    
    Replaces NaN/NaT with None -- pandas NaN is not valid JSON and makes
    FastAPI's json.dumps raise "Out of range float values are not JSON
    compliant: nan". Also normalises timestamps to ISO strings.
    """
    df = df.astype(object).where(df.notna(), None)
    return df.to_dict(orient="records")


def get_spot_candles(trade_date):
    """
    Fetch 1-minute NIFTY spot candles for a given trading day.
    
    Args:
        trade_date: Trading date (YYYY-MM-DD)
        
    Returns:
        List of dicts with keys: trade_time, trade_date, open, high, low, close, volume
    """
    con = get_db_connection()
    try:
        query = """
            SELECT trade_time, trade_date, open, high, low, close, volume 
            FROM nifty_spot 
            WHERE trade_date = ? 
            ORDER BY trade_time ASC
        """
        df = con.execute(query, [trade_date]).df()
        return df_to_records(df)
    finally:
        con.close()