"""Timezone regression tests: everything broker-facing must be IST."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.timezone import (
    EQUITY_CLOSE_MIN, EQUITY_OPEN_MIN, IST, MCX_CLOSE_MIN, MCX_OPEN_MIN,
    angel_request_str, breeze_utc_window_for_ist_day_chunk, ensure_ist,
    ist_minutes, now_ist, parse_broker_timestamp,
)


def test_angel_aware_iso_stays_ist():
    ts = parse_broker_timestamp("2024-10-17T00:00:00+05:30")
    assert ts.tzinfo is not None
    assert ts.utcoffset().total_seconds() == 5.5 * 3600
    assert (ts.hour, ts.minute) == (0, 0)


def test_angel_naive_is_ist_not_utc():
    ts = parse_broker_timestamp("2024-10-17 09:15:00")
    assert ts.utcoffset().total_seconds() == 5.5 * 3600
    assert (ts.hour, ts.minute) == (9, 15)


def test_breeze_z_suffix_is_ist_wall_clock():
    # Breeze stamps IST wall-clock with a bogus Z suffix.
    for raw in ("2026-09-14 09:15:00.000Z", "2026-09-14T09:15:00.000Z"):
        ts = parse_broker_timestamp(raw)
        assert (ts.hour, ts.minute) == (9, 15), raw
        assert ts.utcoffset().total_seconds() == 5.5 * 3600


def test_angel_request_format_is_ist():
    ts = parse_broker_timestamp("2024-10-17T00:00:00+05:30")
    assert angel_request_str(ts) == "2024-10-17 00:00"


def test_equity_session_bounds():
    assert EQUITY_OPEN_MIN == 9 * 60 + 15
    assert EQUITY_CLOSE_MIN == 15 * 60 + 30
    assert ist_minutes(ensure_ist(datetime(2026, 9, 17, 9, 15))) == EQUITY_OPEN_MIN
    assert ist_minutes(ensure_ist(datetime(2026, 9, 17, 15, 30))) == EQUITY_CLOSE_MIN


def test_mcx_session_bounds():
    assert MCX_OPEN_MIN == 9 * 60
    assert MCX_CLOSE_MIN == 23 * 60 + 30
    assert ist_minutes(ensure_ist(datetime(2026, 9, 17, 23, 30))) == MCX_CLOSE_MIN


def test_breeze_window_covers_full_ist_day():
    s = ensure_ist(datetime(2026, 9, 14, 9, 15))
    e = ensure_ist(datetime(2026, 9, 14, 15, 30))
    fro, to = breeze_utc_window_for_ist_day_chunk(s, e)
    # full IST day 00:00 -> 23:59:59 IST == prev-day 18:30Z -> 18:29:59Z
    assert fro.startswith("2026-09-13T18:30:00")
    assert to.startswith("2026-09-14T18:29:59")
    assert fro.endswith("Z") and to.endswith("Z")


def test_now_ist_is_aware_ist():
    n = now_ist()
    assert n.tzinfo is not None
    assert n.utcoffset().total_seconds() == 5.5 * 3600


def test_strategy_cutoffs_use_ist():
    import inspect
    from strategies import index_oi_momentum as m
    src = inspect.getsource(m)
    assert "ist_minutes" in src
    # expiry flat must be 15:15 IST, session cutoffs 14:45 / 15:05 IST
    assert "15 * 60 + 15" in src
    assert "14 * 60 + 45" in src
    assert "15 * 60 + 5" in src
