"""
VIX Data Router
Endpoints: /vix, /vix/daily
"""
from fastapi import APIRouter, Query
from typing import Optional
from datetime import date

from ..providers.vix_provider import get_vix_latest
from ..providers.daily_provider import get_vix_daily

router = APIRouter(tags=["VIX Data"])


@router.get("/vix")
def vix_endpoint(trade_date: str = None):
    """
    Fetch latest VIX data.
    Uses vix_provider (reads india_vix table from market_data.duckdb).
    """
    rows = get_vix_latest(trade_date=trade_date)
    return rows if isinstance(rows, list) else []


@router.get("/vix/daily")
def vix_daily(trade_date: str = None):
    """
    Fetch daily aggregated VIX OHLCV data.
    Aggregates 1-minute data from india_vix into daily OHLCV.
    """
    return get_vix_daily(trade_date)
