"""Spot Data Router
Endpoints: /spot - Aggregated NIFTY spot OHLCV candles
"""
from fastapi import APIRouter, Query
from typing import Optional
from datetime import date

from ..providers.candle_aggregator import CandleInterval
from ..providers.ohlcv_service import get_spot_candles

router = APIRouter(tags=["Spot Data"])


@router.get("/spot")
def spot_candles(
    symbol: str = Query(..., description="Symbol (currently only NIFTY is supported)"),
    fromDate: date = Query(..., description="Start date (YYYY-MM-DD)"),
    toDate: Optional[date] = Query(None, description="End date (YYYY-MM-DD, optional; defaults to fromDate)"),
    interval: CandleInterval = Query(..., description=(
        "Aggregation interval. One of: ONE_MINUTE, THREE_MINUTE, FIVE_MINUTE, "
        "TEN_MINUTE, FIFTEEN_MINUTE, THIRTY_MINUTE, ONE_HOUR, ONE_DAY, WEEK, MONTH."
    )),
):
    """
    Fetch aggregated NIFTY spot OHLCV candles, capped at 800 buckets.
    """
    return get_spot_candles(
        symbol,
        fromDate.isoformat(),
        toDate.isoformat() if toDate else None,
        interval,
    )