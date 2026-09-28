"""Live, redacted verification of the NIFTY No Brainer runner request path.

The script saves only request metadata, row counts, and derived trace values.
It never writes candle payloads and uses a non-persisting provider.
"""
from __future__ import annotations

import json
import time as wall_time
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from backtest.nifty_no_brainer_breeze import (
    build_breeze_option_request,
    trace_monthly_option_requests,
)
from market_data.breeze_data_provider import BreezeHistoricalDataProvider
from utils.timezone import IST

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = ROOT / "logs" / "breeze_nifty_golden_request.json"
REPORT_PATH = ROOT / "logs" / "runner_breeze_option_request_verification.json"
TABLE_PATH = ROOT / "logs" / "runner_breeze_option_request_trace.md"


class RecordingClient:
    """Delegate to the authenticated SDK while retaining only historical kwargs."""

    def __init__(self, client: Any):
        self._client = client
        self.history_calls: list[dict[str, Any]] = []

    def get_historical_data_v2(self, **kwargs: Any):
        self.history_calls.append(dict(kwargs))
        try:
            result = self._client.get_historical_data_v2(**kwargs)
        except SystemExit:
            # Some SDK response paths terminate rather than returning an empty
            # Success list.  In this diagnostic, that is evidence of zero rows.
            return {"Status": 200, "Success": []}
        wall_time.sleep(0.5)
        return result

    def __getattr__(self, name: str):
        return getattr(self._client, name)


def _fetch(provider: BreezeHistoricalDataProvider, recorder: RecordingClient,
           expiry: date, strike: int, day: date) -> dict[str, Any]:
    request = build_breeze_option_request(expiry, strike)
    start = datetime.combine(day, time(9, 40), IST)
    end = datetime.combine(day, time(10, 0), IST)
    before = len(recorder.history_calls)
    try:
        rows = provider.get_historical_candles(request, "1m", start, end)
    except RuntimeError as exc:
        if "No Breeze data returned" not in str(exc):
            raise
        rows = []
    calls = recorder.history_calls[before:]
    return {
        "runner_option_request": request,
        "final_kwargs_sent_to_breeze": calls[-1] if calls else None,
        "rows": len(rows),
        "has_0940": any(candle.timestamp == start for candle in rows),
        "has_1000": any(candle.timestamp == end for candle in rows),
    }


def _markdown(trace: list[dict[str, Any]]) -> str:
    lines = [
        "# Runner Breeze Option Request Trace",
        "",
        "No candle payloads are saved. `kwargs` are the final values captured at the Breeze SDK boundary.",
        "",
        "| Entry date | Spot | ATM | Expiry month | Confirmed expiry | Strikes (buy/sell/hedge) | Final kwargs sent to Breeze | Rows (buy/sell/hedge) | 15:16 present | Status |",
        "|---|---:|---:|---|---|---|---|---|---|---|",
    ]
    for row in trace:
        strikes = row.get("strikes", {})
        counts = row.get("rows_returned_per_leg", {})
        present = row.get("candle_1516_present", {})
        final = row.get("final_kwargs_sent_to_breeze", {})
        final_summary = "; ".join(
            f"{name}={json.dumps(final.get(name), separators=(',', ':'))}"
            for name in ("near_buy", "sell", "hedge")
        )
        lines.append(
            "| {entry} | {spot} | {atm} | {expiry_month} | {expiry} | {strikes} | `{final}` | {counts} | {present} | {status} |".format(
                entry=row.get("entry_date", "-"), spot=row.get("spot", "-"), atm=row.get("atm", "-"),
                expiry_month=row["expiry_month_used"], expiry=row.get("confirmed_expiry_date", "-"),
                strikes="/".join(str(strikes.get(key, "-")) for key in ("near_buy", "sell", "hedge")),
                final=final_summary,
                counts="/".join(str(counts.get(key, "-")) for key in ("near_buy", "sell", "hedge")),
                present="/".join(str(present.get(key, "-")) for key in ("near_buy", "sell", "hedge")),
                status=row["status"],
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    provider = BreezeHistoricalDataProvider(persist_cache=False)
    provider.ensure_authenticated()
    recorder = RecordingClient(provider.client)
    provider.client = recorder

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    golden_result = _fetch(provider, recorder, date(2024, 2, 29), 22100, date(2024, 1, 25))
    extras = {
        "jan_2025_to_feb_2025_23500": _fetch(provider, recorder, date(2025, 2, 27), 23500, date(2025, 1, 31)),
        "jan_2026_to_feb_2026_25300": _fetch(provider, recorder, date(2026, 2, 24), 25300, date(2026, 1, 30)),
        "jul_2025_to_sep_2025_25000": _fetch(provider, recorder, date(2025, 9, 30), 25000, date(2025, 7, 25)),
    }
    negatives = {
        "same_month_expiry": _fetch(provider, recorder, date(2024, 1, 31), 22100, date(2024, 1, 25)),
        "holiday_date": _fetch(provider, recorder, date(2024, 2, 29), 22100, date(2024, 1, 26)),
        "never_traded_strike": _fetch(provider, recorder, date(2024, 2, 29), 99999, date(2024, 1, 25)),
        "wrong_expiry_weekday": _fetch(provider, recorder, date(2024, 2, 28), 22100, date(2024, 1, 25)),
    }
    trace = trace_monthly_option_requests(provider, date(2025, 1, 1), date(2026, 8, 31))
    # Attach final SDK kwargs without retaining candle data.  Contract fields are
    # enough to correlate each trace leg with its most recent captured request.
    for row in trace:
        final: dict[str, Any] = {}
        for name, request in row.get("runner_option_requests", {}).items():
            matching = [call for call in recorder.history_calls
                        if all(call.get(key) == value for key, value in request.items())]
            final[name] = matching[-1] if matching else None
        row["final_kwargs_sent_to_breeze"] = final

    report = {
        "golden": golden,
        "golden_result": golden_result,
        "golden_kwargs_identical": golden_result["final_kwargs_sent_to_breeze"] == golden,
        "extra_known_good": extras,
        "negative_tests": negatives,
        "negative_tests_all_zero": all(result["rows"] == 0 for result in negatives.values()),
        "trace": trace,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    TABLE_PATH.write_text(_markdown(trace), encoding="utf-8")
    print(json.dumps({
        "golden_kwargs_identical": report["golden_kwargs_identical"],
        "golden_rows": golden_result["rows"],
        "extras": {key: value["rows"] for key, value in extras.items()},
        "negative_rows": {key: value["rows"] for key, value in negatives.items()},
        "trace_statuses": {row["month"]: row["status"] for row in trace},
        "report": str(REPORT_PATH),
        "table": str(TABLE_PATH),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
