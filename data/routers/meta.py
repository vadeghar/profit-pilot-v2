"""
Metadata Router
Endpoints: /meta/expiries, /meta/holidays
"""
from fastapi import APIRouter, Query

from ..providers.meta_provider import get_expiries, get_holidays

router = APIRouter(tags=["Metadata"])


@router.get("/meta/expiries")
def get_expiries_endpoint(upcoming_only: bool = Query(False, description="Filter for expiries >= today")):
    """
    Fetch expiry dates from expiry_calendar.
    """
    return get_expiries(upcoming_only)


@router.get("/meta/holidays")
def get_holidays_endpoint():
    """
    Fetch NSE trading holidays from holiday_calendar.
    """
    return get_holidays()
