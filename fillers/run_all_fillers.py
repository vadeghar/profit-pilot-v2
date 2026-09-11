"""Run the market-data fillers in one consistent, sequential workflow.

The workflow order is intentionally fixed:

1. nifty_spot_filler_duckdb.py
2. nifty_option_filler_duckdb.py
3. india_vix_filler_duckdb.py
4. equity_spot_filler_duckdb.py
5. institutional_data_filler_duckdb.py
6. duckdb_analytics.py

By default the date range is today through today. Use ``--from-date`` and
``--to-date`` to override it. Fillers remain dry-run unless ``--execute`` is
provided. Analytics is always run last and is read-only.

Examples:
    python fillers/run_all_fillers.py --execute
    python fillers/run_all_fillers.py --execute --from-date 2026-09-01 \
        --to-date 2026-09-11 --include-partial
    python fillers/run_all_fillers.py --from-date 2026-09-11
    python fillers/run_all_fillers.py --execute --continue-on-error

The orchestrator starts each program as a separate process, waits for it to
finish, and stops immediately if a filler fails. This avoids concurrent writes
to DuckDB and makes the dependency order explicit.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "data", "market_data.duckdb")

FILLER_ORDER = (
    ("NIFTY spot", "nifty_spot_filler_duckdb.py", True),
    ("NIFTY options", "nifty_option_filler_duckdb.py", True),
    ("India VIX", "india_vix_filler_duckdb.py", True),
    ("Equity spot", "equity_spot_filler_duckdb.py", True),
    ("Institutional data", "institutional_data_filler_duckdb.py", False),
    ("DuckDB analytics", "duckdb_analytics.py", False),
)


def parse_args() -> argparse.Namespace:
    today = date.today()
    parser = argparse.ArgumentParser(
        description="Run all DuckDB fillers sequentially, then print analytics."
    )
    parser.add_argument("--from-date", type=date.fromisoformat, default=today,
                        help=f"Start date YYYY-MM-DD (default: {today})")
    parser.add_argument("--to-date", type=date.fromisoformat, default=today,
                        help=f"End date YYYY-MM-DD (default: {today})")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"DuckDB path (default: {DEFAULT_DB})")
    parser.add_argument("--execute", action="store_true",
                        help="Actually fetch and write data; otherwise dry-run.")
    parser.add_argument("--include-partial", action="store_true",
                        help="Ask supported fillers to repair partial days.")
    parser.add_argument("--verbose", action="store_true",
                        help="Pass verbose logging to every supported program.")
    parser.add_argument("--continue-on-error", action="store_true",
                        help="Continue with later fillers after a failure; the "
                             "workflow still exits non-zero if anything failed.")
    return parser.parse_args()


def command_for(script: str, supports_partial: bool, args: argparse.Namespace) -> list[str]:
    command = [sys.executable, os.path.join(ROOT, "fillers", script),
               "--from-date", args.from_date.isoformat(),
               "--to-date", args.to_date.isoformat(), "--db", args.db]
    if script == "duckdb_analytics.py":
        return [sys.executable, os.path.join(ROOT, "fillers", script), "--db", args.db]
    if args.execute:
        command.append("--execute")
    if args.include_partial and supports_partial:
        command.append("--include-partial")
    if args.verbose:
        command.append("--verbose")
    return command


def main() -> int:
    args = parse_args()
    if args.to_date < args.from_date:
        raise SystemExit("--to-date cannot be earlier than --from-date")

    print(f"Workflow range: {args.from_date} -> {args.to_date}")
    print(f"Mode: {'EXECUTE' if args.execute else 'DRY-RUN'}")
    print(f"Database: {args.db}\n")

    failures: list[tuple[str, int]] = []
    for position, (label, script, supports_partial) in enumerate(FILLER_ORDER, 1):
        command = command_for(script, supports_partial, args)
        print(f"[{position}/{len(FILLER_ORDER)}] Starting {label}: {script}", flush=True)
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode != 0:
            print(f"[{position}/{len(FILLER_ORDER)}] FAILED: {script} "
                  f"(exit code {result.returncode})", flush=True)
            failures.append((script, result.returncode))
            if not args.continue_on_error:
                return result.returncode
            print("Continuing because --continue-on-error was supplied.\n",
                  flush=True)
            continue
        print(f"[{position}/{len(FILLER_ORDER)}] Completed: {script}\n", flush=True)

    if failures:
        print("Workflow completed with failures:")
        for script, code in failures:
            print(f"  - {script}: exit code {code}")
        return failures[0][1]
    print("All fillers and analytics completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
