"""Run the live NIFTY Breeze backtest with structured, secret-free output."""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest.nifty_no_brainer_breeze import (
    jan_2026_gap_up_check, resolve_unconfirmed_expiries, run_monthly_backtest,
)
from market_data.breeze_data_provider import BreezeHistoricalDataProvider


def main():
    log_path = Path("logs/nifty_live_run.jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps({"phase": "started"}) + "\n", encoding="utf-8")
    provider = BreezeHistoricalDataProvider(persist_cache=False)
    provider.ensure_authenticated()
    metadata = resolve_unconfirmed_expiries(provider)
    probe_result = {"phase": "expiry_probe", "statuses": {
        k: v.get("status") for k, v in metadata["monthly_expiries"].items()
    }}
    print(json.dumps(probe_result), flush=True)
    log_path.write_text(json.dumps(probe_result) + "\n", encoding="utf-8")
    january = run_monthly_backtest(provider, start=date(2026, 1, 1), end=date(2026, 1, 31),
                                    trace_month="2026-01")
    january_result = {"phase": "january_2026", "report": january,
                      "gap_up_check": jan_2026_gap_up_check(january)}
    print(json.dumps(january_result, default=str), flush=True)
    log_path.write_text(json.dumps(january_result, default=str) + "\n", encoding="utf-8")
    row = january["months"][0] if january["months"] else {}
    if row.get("decision") == "TRADE" and row.get("exit_time", "").startswith("2026-02-03"):
        full = run_monthly_backtest(provider, start=date(2025, 1, 1), end=date(2026, 8, 31))
        full_result = {"phase": "full_range", "report": full}
        print(json.dumps(full_result, default=str), flush=True)
        log_path.write_text(json.dumps(full_result, default=str) + "\n", encoding="utf-8")
    else:
        gate_result = {"phase": "full_range", "status": "NOT_RUN",
                       "reason": "January sanity gate did not pass"}
        print(json.dumps(gate_result), flush=True)
        log_path.write_text(json.dumps(gate_result) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
