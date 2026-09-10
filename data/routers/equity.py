"""
Equity Spot Data Router
Endpoints: /equity/candles, /equity/daily, /equity/latest, /equity/symbols
"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional, List

from ..providers.equity_spot_provider import (
    get_equity_candles,
    get_equity_daily,
    get_equity_latest,
    get_equity_symbols,
)

router = APIRouter(tags=["Equity Spot Data"])


@router.get("/equity/candles")
def equity_candles(
    symbol: str = Query(..., description="Stock symbol (e.g., RELIANCE, HDFCBANK)"),
    trade_date: Optional[str] = Query(None, description="Specific trading date (YYYY-MM-DD)"),
    start_date: Optional[str] = Query(None, description="Start of date range (YYYY-MM-DD, inclusive)"),
    end_date: Optional[str] = Query(None, description="End of date range (YYYY-MM-DD, inclusive)"),
    start_time: Optional[str] = Query(None, description="Start time filter (HH:MM:SS) within dates"),
    end_time: Optional[str] = Query(None, description="End time filter (HH:MM:SS) within dates"),
    limit: Optional[int] = Query(None, description="Max number of rows to return"),
):
    """
    Fetch 1-minute equity spot candles with flexible filtering.
    """
    try:
        return get_equity_candles(symbol, trade_date, start_date, end_date, start_time, end_time, limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/equity/daily")
def equity_daily(
    symbol: Optional[str] = Query(None, description="Stock symbol filter"),
    trade_date: Optional[str] = Query(None, description="Specific trading date (YYYY-MM-DD)"),
    start_date: Optional[str] = Query(None, description="Start of date range (YYYY-MM-DD, inclusive)"),
    end_date: Optional[str] = Query(None, description="End of date range (YYYY-MM-DD, inclusive)"),
):
    """
    Fetch daily aggregated OHLCV for equity spot data.
    Aggregates 1-minute candles into daily OHLCV per symbol.
    """
    try:
        return get_equity_daily(symbol, trade_date, start_date, end_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/equity/latest")
def equity_latest(
    symbol: Optional[str] = Query(None, description="Stock symbol. If omitted, returns latest for each symbol."),
):
    """
    Fetch the most recent equity spot candle(s).
    """
    try:
        return get_equity_latest(symbol)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/equity/symbols")
def equity_symbols():
    """
    List all available equity symbols in the database.
    """
    try:
        return get_equity_symbols()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))