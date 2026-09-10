"""
ProfitPilot Data Bridge API
============================

Local REST API exposing normalized DuckDB market data and metadata.
All endpoints are served from a single FastAPI application.

Endpoints:
    - /health          - API health check
    - /spot/daily      - Daily aggregated spot OHLCV
    - /spot/candles    - 1-minute NIFTY spot candles
    - /vix             - Latest VIX data
    - /vix/daily       - Daily aggregated VIX OHLCV
    - /options/ticks   - Option ticks with filters
    - /meta/expiries   - Expiry calendar
    - /meta/holidays   - Trading holidays

Usage:
    uvicorn data.api:app --host 0.0.0.0 --port 8000 --reload
"""
import os
from fastapi import FastAPI
from dotenv import load_dotenv

from .providers.db import get_db_connection, get_db_path
from .routers import spot, vix, options, meta

# Load environment variables
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# Create FastAPI application
app = FastAPI(
    title="ProfitPilot Data Bridge API",
    description="Local REST bridge exposing normalized DuckDB market data and metadata",
    version="2.0.0"
)


# Health check endpoint
@app.get("/health", tags=["System"])
def health_check():
    """
    Verifies API status and connectivity to DuckDB.
    
    Returns:
        dict: Health status with total row counts
    """
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


# Include routers
app.include_router(spot.router)
app.include_router(vix.router)
app.include_router(options.router)
app.include_router(meta.router)


if __name__ == "__main__":
    import uvicorn
    # Bind to 0.0.0.0 so external machines on your local network can query it
    uvicorn.run("data.api:app", host="0.0.0.0", port=8000, reload=True)
