"""
Options Data Router
Endpoints: /options/ticks
"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from datetime import date

from ..providers.options_ticks_provider import get_options_ticks

router = APIRouter(tags=["Options Data"])


@router.get("/options/ticks")
def get_options_ticks_endpoint(
    trade_date: date = Query(..., description="Trading date (YYYY-MM-DD)"),
    expiry_date: Optional[date] = Query(None, description="Contract expiry date (YYYY-MM-DD)"),
    strike_price: Optional[int] = Query(None, description="Strike price (e.g., 24000)"),
    option_type: Optional[str] = Query(None, description="Option type (CE, PE, or FUT)")
):
    """
    Fetch option ticks filtered by trade_date, expiry, strike, and type.
    """
    try:
        return get_options_ticks(trade_date, expiry_date, strike_price, option_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
