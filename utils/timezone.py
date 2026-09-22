"""Single source of truth for Indian-market time handling (IST = UTC+05:30).

Contract (app-wide):
  * Every ``datetime`` that represents market data (candles, ticks, quotes,
    orders, strategy timestamps) is **timezone-aware in IST**.
  * Broker payloads that arrive naive / UTC / with a bogus 'Z' suffix are
    converted to IST at the ingestion edge (Angel / Breeze / yfinance / CSV).
  * Session logic always compares **IST wall-clock** minutes, never UTC.

Sessions (IST wall-clock):
  * NSE equity / index options : 09:15 -> 15:30
  * MCX commodity              : 09:00 -> 23:30
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

EQUITY_OPEN_MIN = 9 * 60 + 15    # 09:15 IST
EQUITY_CLOSE_MIN = 15 * 60 + 30  # 15:30 IST
MCX_OPEN_MIN = 9 * 60            # 09:00 IST
MCX_CLOSE_MIN = 23 * 60 + 30     # 23:30 IST


def now_ist() -> datetime:
    """Current time as aware IST datetime."""
    return datetime.now(IST)


def to_ist(dt: Optional[datetime]) -> Optional[datetime]:
    """Convert any datetime to aware IST.

    * None -> None
    * naive  -> assumed to already be IST wall-clock, attach IST.
    * aware  -> astimezone(IST).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def ensure_ist(dt: datetime) -> datetime:
    out = to_ist(dt)
    assert out is not None
    return out


def ist_minutes(dt: datetime) -> int:
    """IST wall-clock minutes since midnight (for session comparisons)."""
    d = ensure_ist(dt)
    return d.hour * 60 + d.minute


def ist_day_str(dt: datetime) -> str:
    return ensure_ist(dt).strftime("%Y-%m-%d")


def is_equity_session(dt: datetime) -> bool:
    return EQUITY_OPEN_MIN <= ist_minutes(dt) <= EQUITY_CLOSE_MIN


def is_mcx_session(dt: datetime) -> bool:
    return MCX_OPEN_MIN <= ist_minutes(dt) <= MCX_CLOSE_MIN


def parse_broker_timestamp(value) -> datetime:
    """Parse Angel/Breeze/yfinance/CSV timestamps into aware IST.

    Handles: aware ISO (+05:30 / Z), naive 'YYYY-MM-DD HH:MM:SS' (IST),
    Breeze 'YYYY-MM-DD HH:MM:SS.000Z' where the wall-clock is really IST
    despite the Z suffix, epoch seconds/ms.
    """
    if isinstance(value, datetime):
        # Naive broker datetimes are IST wall-clock already.
        return ensure_ist(value)
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e12:  # epoch ms
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=UTC).astimezone(IST)
    s = str(value).strip()
    # Breeze quirk: stamps IST wall-clock with a literal 'Z'/'.000Z' suffix
    # (e.g. '2026-09-14 09:15:00.000Z' or '2026-09-14T09:15:00.000Z' both mean
    # 09:15 IST, NOT 09:15 UTC). So ANY trailing 'Z' -> strip, parse naive,
    # attach IST. Only explicit numeric offsets (+05:30 / +00:00) are treated
    # as true offsets.
    if s.endswith("Z"):
        s = s[:-1].strip()
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=IST)
            except Exception:
                continue
    try:
        dt = datetime.fromisoformat(s)
        return ensure_ist(dt)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=IST)
        except Exception:
            continue
    # last resort: return now so callers never crash on a bad stamp
    return now_ist()


def angel_request_str(dt: datetime) -> str:
    """Format for Angel SmartAPI getCandleData (expects IST 'YYYY-MM-DD HH:MM')."""
    return ensure_ist(dt).strftime("%Y-%m-%d %H:%M")


def breeze_utc_window_for_ist_day_chunk(start: datetime, end: datetime) -> tuple[str, str]:
    """Map an IST [start, end] chunk to Breeze UTC 'Z' request strings.

    Breeze get_historical_data_v2 wants UTC ISO with 'Z'. We cover the full
    IST calendar days spanned by the chunk (00:00 IST -> 23:59:59 IST)
    converted to UTC, so equity (09:15-15:30) and MCX (09:00-23:30) sessions
    are never truncated by a hardcoded 07:00Z/18:00Z window.
    """
    s = ensure_ist(start)
    e = ensure_ist(end)
    day_start_ist = s.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_ist = e.replace(hour=23, minute=59, second=59, microsecond=0)
    s_utc = day_start_ist.astimezone(UTC)
    e_utc = day_end_ist.astimezone(UTC)
    return (s_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            e_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
