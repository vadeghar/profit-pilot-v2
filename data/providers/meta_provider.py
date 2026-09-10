"""
Metadata Provider
Fetches expiry calendar and holiday calendar data
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


def get_expiries(upcoming_only=False):
    """
    Fetch expiry dates from expiry_calendar.
    
    Args:
        upcoming_only: If True, filter for expiries >= today
        
    Returns:
        List of dicts with expiry calendar data
    """
    con = get_db_connection()
    try:
        if upcoming_only:
            query = "SELECT * FROM expiry_calendar WHERE expiry_date >= CURRENT_DATE ORDER BY expiry_date ASC"
        else:
            query = "SELECT * FROM expiry_calendar ORDER BY expiry_date ASC"
            
        df = con.execute(query).df()
        return df_to_records(df)
    finally:
        con.close()


def get_holidays():
    """
    Fetch NSE trading holidays from holiday_calendar.
    
    Returns:
        List of dicts with keys: holiday_date, holiday_name
    """
    con = get_db_connection()
    try:
        query = "SELECT holiday_date, holiday_name FROM holiday_calendar ORDER BY holiday_date ASC"
        df = con.execute(query).df()
        return df_to_records(df)
    finally:
        con.close()