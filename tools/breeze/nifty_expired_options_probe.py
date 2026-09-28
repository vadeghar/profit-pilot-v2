"""Auditable Breeze probes for expired NIFTY options.

Requests are logged as contract/date metadata only; credentials and response
payloads are never written. Diagnostics bypass broad provider chunking.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from market_data.breeze_data_provider import BreezeHistoricalDataProvider
from market_data.option_symbol import get_option_symbol
from strategies.nifty_no_brainer import CalendarEngine

ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = ROOT / "logs" / "breeze_nifty_probe.jsonl"
GOLDEN_PATH = ROOT / "logs" / "breeze_nifty_golden_request.json"


def _shape(response: Any) -> dict[str, Any]:
    result = {"type": type(response).__name__}
    if isinstance(response, dict):
        success = response.get("Success")
        rows = success if isinstance(success, list) else []
        result.update({"status": response.get("Status"),
                       "error": str(response.get("Error"))[:240] if response.get("Error") else None,
                       "row_count": len(rows)})
        if rows:
            result["first_timestamp"] = str(rows[0].get("datetime")) if isinstance(rows[0], dict) else str(rows[0][0])
            result["last_timestamp"] = str(rows[-1].get("datetime")) if isinstance(rows[-1], dict) else str(rows[-1][0])
            result["row_keys"] = sorted(str(key) for key in rows[0]) if isinstance(rows[0], dict) else ["array"]
            result["has_1516"] = any(str(r.get("datetime", "")).endswith("15:16:00") for r in rows if isinstance(r, dict))
    return result


def _request(expiry: str, strike: int, day: str, start: str = "09:40:00",
             end: str = "10:00:00", *, expiry_value: str | None = None,
             right: str = "CE", timestamp_mode: str = "Z") -> dict[str, Any]:
    contract = get_option_symbol("breeze", "NIFTY", expiry, strike, right)
    if expiry_value is not None:
        contract["expiry_date"] = expiry_value
    if timestamp_mode == "IST":
        from_date, to_date = f"{day}T{start}+05:30", f"{day}T{end}+05:30"
    elif timestamp_mode == "DATE":
        from_date, to_date = day, day
    elif timestamp_mode == "T00Z":
        from_date, to_date = f"{day}T00:00:00.000Z", f"{day}T23:59:59.000Z"
    else:
        from_date, to_date = f"{day}T{start}.000Z", f"{day}T{end}.000Z"
    return {"interval": "1minute", "from_date": from_date, "to_date": to_date,
            "exchange_code": "NFO", "product_type": "options", **contract}


def _diff(golden: dict[str, Any], actual: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {key: {"golden": golden.get(key), "actual": actual.get(key)}
            for key in sorted(set(golden) | set(actual)) if golden.get(key) != actual.get(key)}


def _request_log(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible redacted request shape used by unit tests."""
    return {"request": {key: str(value) for key, value in kwargs.items()}}


def _write(event: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"time_utc": datetime.now(timezone.utc).isoformat(), **event}, sort_keys=True) + "\n")


def _call(provider: BreezeHistoricalDataProvider, label: str, kwargs: dict[str, Any],
          golden: dict[str, Any] | None = None) -> dict[str, Any]:
    _write({"event": "request", "label": label, "kwargs": kwargs,
            "diff_from_golden": _diff(golden, kwargs) if golden else {}})
    try:
        shape = _shape(provider.client.get_historical_data_v2(**kwargs))
    except Exception as exc:
        shape = {"error_type": type(exc).__name__, "error": str(exc)[:300]}
    _write({"event": "response", "label": label, **shape})
    print(json.dumps({"label": label, "kwargs": kwargs, **shape}, sort_keys=True))
    return shape


def _golden() -> dict[str, Any]:
    return _request("2024-02-29", 22100, "2024-01-25")


def main() -> int:
    provider = BreezeHistoricalDataProvider()
    try:
        provider.ensure_authenticated()
    except Exception as exc:
        _write({"event": "auth_blocked", "error_type": type(exc).__name__})
        print("LIVE_PROBES_BLOCKED: Breeze session verification failed; redacted log written.")
        return 2

    golden = _golden()
    GOLDEN_PATH.write_text(json.dumps(golden, indent=2, sort_keys=True), encoding="utf-8")
    _call(provider, "golden_22100_20240125", golden)
    for strike in (26500, 27500, 22000, 22200, 22500, 23000):
        _call(provider, f"strike_{strike}_20240125", _request("2024-02-29", strike, "2024-01-25"), golden)
    _call(provider, "wrong_holiday_day_22100", _request("2024-02-29", 22100, "2024-01-26"), golden)

    variants = {
        "window_full_day": _request("2024-02-29", 22100, "2024-01-25", "09:15:00", "15:30:00"),
        "window_1510": _request("2024-02-29", 22100, "2024-01-25", "15:10:00", "15:20:00"),
        "window_two_days": {**golden, "to_date": "2024-01-26T10:00:00.000Z"},
        "timestamp_ist": _request("2024-02-29", 22100, "2024-01-25", timestamp_mode="IST"),
        "timestamp_t00z": _request("2024-02-29", 22100, "2024-01-25", timestamp_mode="T00Z"),
        "expiry_t07": _request("2024-02-29", 22100, "2024-01-25", expiry_value="2024-02-29T07:00:00.000Z"),
        "right_put": _request("2024-02-29", 22100, "2024-01-25", right="PE"),
        "date_only": _request("2024-02-29", 22100, "2024-01-25", timestamp_mode="DATE"),
    }
    for label, request in variants.items():
        _call(provider, f"bisect_{label}", request, golden)

    calendar = CalendarEngine()
    print(json.dumps({"calendar_entry_2024_01": str(calendar.get_entry_date(2024, 1)),
                      "calendar_expiry_2024_02": str(calendar.get_monthly_expiry(2024, 2)),
                      "calendar_expiry_2025_09": str(calendar.get_monthly_expiry(2025, 9))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())