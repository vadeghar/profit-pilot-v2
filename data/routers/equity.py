"""Equity Data Router
Endpoints: /equity - Aggregated equity OHLCV candles (per symbol)
"""
from fastapi import APIRouter, Query
from typing import Optional
from datetime import date

from ..providers.candle_aggregator import CandleInterval
from ..providers.ohlcv_service import get_equity_candles

router = APIRouter(tags=["Equity Data"])


@router.get("/equity")
def equity_candles(
    symbol: str = Query(..., description="Stock symbol (e.g., RELIANCE, HDFCBANK)"),
    fromDate: date = Query(..., description="Start date (YYYY-MM-DD)"),
    toDate: Optional[date] = Query(None, description="End date (YYYY-MM-DD, optional; defaults to fromDate)"),
    interval: CandleInterval = Query(..., description=(
        "Aggregation interval. One of: ONE_MINUTE, THREE_MINUTE, FIVE_MINUTE, "
        "TEN_MINUTE, FIFTEEN_MINUTE, THIRTY_MINUTE, ONE_HOUR, ONE_DAY, WEEK, MONTH."
    )),
):
    """
    Fetch aggregated equity OHLCV candles for a symbol, capped at 800 buckets.
    """
    return get_equity_candles(
        symbol,
        fromDate.isoformat(),
        toDate.isoformat() if toDate else None,
        interval,
    )