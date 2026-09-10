"""
Options Ticks Provider
Fetches option ticks from options_ticks table
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


def get_options_ticks(trade_date, expiry_date=None, strike_price=None, option_type=None):
    """
    Fetch option ticks filtered by trade_date, expiry, strike, and type.
    
    Args:
        trade_date: Trading date (YYYY-MM-DD)
        expiry_date: Optional contract expiry date (YYYY-MM-DD)
        strike_price: Optional strike price (e.g., 24000)
        option_type: Optional option type (CE, PE, or FUT)
        
    Returns:
        List of dicts with option tick data
    """
    con = get_db_connection()
    try:
        query = "SELECT * FROM options_ticks WHERE trade_date = ?"
        params = [trade_date]

        if expiry_date:
            query += " AND expiry_date = ?"
            params.append(expiry_date)
        if strike_price:
            query += " AND strike_price = ?"
            params.append(strike_price)
        if option_type:
            query += " AND option_type = ?"
            params.append(option_type.upper())

        query += " ORDER BY trade_time ASC"
        
        df = con.execute(query, params).df()
        return df_to_records(df)
    finally:
        con.close()