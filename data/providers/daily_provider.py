"""
Daily Data Provider
Aggregates 1-minute data from nifty_spot / india_vix into daily OHLCV
"""
import os
import duckdb

# Navigate to project root (parent of data/ directory)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_db_raw = os.getenv("DUCKDB_PATH", os.path.join("data", "market_data.duckdb"))
DB = _db_raw if os.path.isabs(_db_raw) else os.path.join(PROJECT_ROOT, _db_raw)


def get_spot_daily(trade_date):
    """
    Fetch daily aggregated spot OHLCV data from nifty_spot table.
    
    Args:
        trade_date: Trading date string (YYYY-MM-DD)
        
    Returns:
        List of dicts with keys: trade_date, open, high, low, close, volume
    """
    con = duckdb.connect(DB, read_only=True)
    rows = con.execute(
        "SELECT MIN(open) as open, MAX(high) as high, MIN(low) as low, "
        "MAX(close) as close, SUM(volume) as volume "
        "FROM nifty_spot WHERE trade_date = ?",
        [trade_date]
    ).fetchall()
    con.close()
    return [
        {"trade_date": trade_date, "open": r[0], "high": r[1], "low": r[2], "close": r[3], "volume": r[4]}
        for r in rows if r[0]
    ]


def get_vix_daily(trade_date):
    """
    Fetch daily aggregated VIX OHLCV data from india_vix table.
    
    Args:
        trade_date: Trading date string (YYYY-MM-DD)
        
    Returns:
        List of dicts with keys: trade_date, open, high, low, close, volume
    """
    con = duckdb.connect(DB, read_only=True)
    rows = con.execute(
        "SELECT MIN(open) as open, MAX(high) as high, MIN(low) as low, "
        "MAX(close) as close, SUM(volume) as volume "
        "FROM india_vix WHERE trade_date = ?",
        [trade_date]
    ).fetchall()
    con.close()
    return [
        {"trade_date": trade_date, "open": r[0], "high": r[1], "low": r[2], "close": r[3], "volume": r[4]}
        for r in rows if r[0]
    ]
