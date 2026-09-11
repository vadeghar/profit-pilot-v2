"""VIX Data Router
Endpoints: /vix - Aggregated VIX OHLCV candles
"""
from fastapi import APIRouter, Depends, Query
from typing import Optional
from datetime import date

from ..providers.candle_aggregator import CandleInterval
from ..providers.ohlcv_service import get_vix_candles
from ._validation import guard_query_params

router = APIRouter(
    tags=["VIX Data"],
    dependencies=[Depends(guard_query_params("fromDate", "toDate", "interval"))],
)


@router.get("/vix")
def vix_candles(
    fromDate: date = Query(..., description="Start date (YYYY-MM-DD)"),
    toDate: Optional[date] = Query(None, description=(
        "End date (YYYY-MM-DD, optional). Optional parameter: enable its "
        "checkbox in the Try-it client for it to be included in the request."
    )),
    interval: CandleInterval = Query(..., description=(
        "Aggregation interval. One of: ONE_MINUTE, THREE_MINUTE, FIVE_MINUTE, "
        "TEN_MINUTE, FIFTEEN_MINUTE, THIRTY_MINUTE, ONE_HOUR, ONE_DAY, WEEK, MONTH."
    )),
):
    """
    Fetch aggregated VIX OHLCV candles, capped at 800 buckets.
    """
    return get_vix_candles(
        fromDate.isoformat(),
        toDate.isoformat() if toDate else None,
        interval,
    )