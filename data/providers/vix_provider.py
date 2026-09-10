"""
VIX Data Provider
Reads india_vix table from market_data.duckdb
"""
import os
import duckdb

# Navigate to project root (parent of data/ directory)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_db_raw = os.getenv("DUCKDB_PATH", os.path.join("data", "market_data.duckdb"))
DB = _db_raw if os.path.isabs(_db_raw) else os.path.join(PROJECT_ROOT, _db_raw)


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
    con = duckdb.connect(DB)
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
    con.close()
    
    # Map to dicts matching table schema
    cols = ["trade_time", "open", "high", "low", "close", "volume", "open_interest", "source"]
    return [dict(zip(cols, r)) for r in rows]
