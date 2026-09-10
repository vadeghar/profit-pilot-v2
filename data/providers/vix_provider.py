"""
VIX Data Provider
Reads india_vix table from market_data.duckdb
"""
from .db import get_db_connection


def get_vix_latest(trade_date=None):
    """
    Fetch latest VIX data from india_vix table.
    
    Args:
        trade_date: Optional trading date string (YYYY-MM-DD).
                   If None, returns the most recent record.
        
    Returns:
        List of dicts with keys: trade_time, open, high, low, close, 
        volume, open_interest, source
    """
    con = get_db_connection()
    try:
        if trade_date:
            rows = con.execute(
                "SELECT trade_time, open, high, low, close, volume, open_interest, source "
                "FROM india_vix WHERE trade_date = ?",
                [trade_date]
            ).fetchall()
        else:
            # Latest available
            rows = con.execute(
                "SELECT trade_time, open, high, low, close, volume, open_interest, source "
                "FROM india_vix ORDER BY trade_time DESC LIMIT 1"
            ).fetchall()
        
        # Map to dicts matching table schema
        cols = ["trade_time", "open", "high", "low", "close", "volume", "open_interest", "source"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        con.close()