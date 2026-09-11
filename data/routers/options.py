"""Options Data Router
Endpoints: /options - Aggregated option OHLCV candles (strike / expiry / type)
"""
from fastapi import APIRouter, Query
from typing import Optional
from datetime import date

from ..providers.candle_aggregator import CandleInterval
from ..providers.ohlcv_service import get_options_candles

router = APIRouter(tags=["Options Data"])


@router.get("/options")
def options_candles(
    strike: int = Query(..., description="Strike price (e.g., 24000)"),
    expiry: date = Query(..., description="Contract expiry date (YYYY-MM-DD)"),
    optionType: Optional[str] = Query(None, description="Option type: CE, PE or FUT (default CE & PE)"),
    fromDate: date = Query(..., description="Start date (YYYY-MM-DD)"),
    toDate: Optional[date] = Query(None, description="End date (YYYY-MM-DD, optional; defaults to fromDate)"),
    interval: CandleInterval = Query(..., description=(
        "Aggregation interval. One of: ONE_MINUTE, THREE_MINUTE, FIVE_MINUTE, "
        "TEN_MINUTE, FIFTEEN_MINUTE, THIRTY_MINUTE, ONE_HOUR, ONE_DAY, WEEK, MONTH."
    )),
):
    """
    Fetch aggregated option OHLCV candles for a contract, capped at 800 buckets.
    """
    return get_options_candles(
        strike,
        expiry.isoformat(),
        optionType,
        fromDate.isoformat(),
        toDate.isoformat() if toDate else None,
        interval,
    )