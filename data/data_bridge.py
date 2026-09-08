import os

import duckdb
import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from typing import Optional, List
from datetime import date

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

app = FastAPI(
    title="ProfitPilot Data Bridge API",
    description="Local REST bridge exposing normalized DuckDB market data and metadata",
    version="2.0.0"
)

_db_raw = os.getenv("DUCKDB_PATH", os.path.join("data", "market_data.duckdb"))
# Relative paths are resolved against the project root, not the CWD,
# so the scripts work from any working directory.
DB_PATH = _db_raw if os.path.isabs(_db_raw) or os.path.splitdrive(_db_raw)[0] \
    else os.path.join(PROJECT_ROOT, _db_raw)


def df_to_records(df: pd.DataFrame):
    """Convert a DataFrame to JSON-safe dicts.

    Replaces NaN/NaT with None -- pandas NaN is not valid JSON and makes
    FastAPI's json.dumps raise "Out of range float values are not JSON
    compliant: nan". Also normalises timestamps to ISO strings.
    """
    df = df.astype(object).where(df.notna(), None)
    return df.to_dict(orient="records")


def get_db_connection():
    """
    Returns a read-only connection to the persistent DuckDB instance.
    Read-only mode prevents database locks when accessing data concurrently.
    """
    try:
        return duckdb.connect(DB_PATH, read_only=True)
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Database connection error: {str(e)}"
        )

@app.get("/health")
def health_check():
    """Verifies API status and connectivity to DuckDB."""
    con = get_db_connection()
    try:
        ticks_count = con.execute("SELECT COUNT(*) FROM options_ticks").fetchone()[0]
        spot_count = con.execute("SELECT COUNT(*) FROM nifty_spot").fetchone()[0]
        return {
            "status": "ok",
            "total_option_ticks": ticks_count,
            "total_spot_candles": spot_count
        }
    finally:
        con.close()

@app.get("/options/ticks")
def get_options_ticks(
    trade_date: date = Query(..., description="Trading date (YYYY-MM-DD)"),
    expiry_date: Optional[date] = Query(None, description="Contract expiry date (YYYY-MM-DD)"),
    strike_price: Optional[int] = Query(None, description="Strike price (e.g., 24000)"),
    option_type: Optional[str] = Query(None, description="Option type (CE, PE, or FUT)")
):
    """
    Fetch option ticks filtered by trade_date, expiry, strike, and type.
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

@app.get("/spot/candles")
def get_spot_candles(
    trade_date: date = Query(..., description="Trading date (YYYY-MM-DD)")
):
    """
    Fetch 1-minute NIFTY spot candles for a given trading day.
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

@app.get("/meta/expiries")
def get_expiries(upcoming_only: bool = Query(False, description="Filter for expiries >= today")):
    """
    Fetch expiry dates from expiry_calendar.
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

@app.get("/meta/holidays")
def get_holidays():
    """
    Fetch NSE trading holidays from holiday_calendar.
    """
    con = get_db_connection()
    try:
        query = "SELECT holiday_date, holiday_name FROM holiday_calendar ORDER BY holiday_date ASC"
        df = con.execute(query).df()
        return df_to_records(df)
    finally:
        con.close()

if __name__ == "__main__":
    import uvicorn
    # Bind to 0.0.0.0 so external machines on your local network (e.g., Mac) can query it
    uvicorn.run("data_bridge:app", host="0.0.0.0", port=8000, reload=True)