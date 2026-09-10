"""
Spot Data Router
Endpoints: /spot/daily, /spot/candles
"""
from fastapi import APIRouter, Query
from typing import Optional
from datetime import date

from ..providers.daily_provider import get_spot_daily
from ..providers.spot_candles_provider import get_spot_candles

router = APIRouter(tags=["Spot Data"])


@router.get("/spot/daily")
def spot_daily(trade_date: str = None):
    """
    Fetch daily aggregated spot OHLCV data.
    Aggregates 1-minute data from nifty_spot into daily OHLCV.
    """
    return get_spot_daily(trade_date)


@router.get("/spot/candles")
def get_spot_candles_endpoint(
    trade_date: date = Query(..., description="Trading date (YYYY-MM-DD)")
):
    """
    Fetch 1-minute NIFTY spot candles for a given trading day.
    """
    return get_spot_candles(trade_date)
