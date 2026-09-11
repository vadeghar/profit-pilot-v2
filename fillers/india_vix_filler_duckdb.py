#!/usr/bin/env python3
"""
india_vix_filler_duckdb.py
--------------------------
Production filler for India VIX 1-minute candles into DuckDB.

- Source       : ICICI Direct Breeze API (get_historical_data_v2, INDVIX @ NSE)
- Destination  : `india_vix` table inside market_data.duckdb (created if absent)
- Holidays     : dates present in the DuckDB `holiday_calendar` are skipped
                 entirely, as are weekends.
- Idempotent   : a trading day that already has candles in `india_vix` is
                 skipped - existing data is NEVER overwritten or duplicated.
                 Within a fetched day, timestamps already stored are filtered
                 out with an anti-join before insert.
- Timezone     : timestamps are stored as naive IST, matching the existing
                 `india_vix` / `nifty_spot` tables.

USAGE
-----
    # dry-run (no API calls, no writes) - default range: 2026-09-04 -> today
    python india_vix_filler_duckdb.py

    # execute a range
    python india_vix_filler_duckdb.py --execute --from-date 2026-03-04 --to-date 2026-09-03

    # refetch missing candles inside days that already exist (only missing ts)
    python india_vix_filler_duckdb.py --execute --include-partial

Environment (.env): BREEZE_API_KEY, BREEZE_API_SECRET, BREEZE_SESSION_TOKEN
"""

import argparse
import logging
import os
import queue
import sys
import threading
import time
from datetime import date, datetime, time as dtime, timedelta

import duckdb
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
DUCK_PATH = os.path.join(BASE_DIR, "market_data.duckdb")
TABLE = "india_vix"
SOURCE_TAG = "breeze:INDVIX:NSE:1minute"

IST = "Asia/Kolkata"
SESSION_OPEN = dtime(9, 15)
SESSION_LAST = dtime(15, 29)
EXPECTED_PER_DAY = 375          # 09:15..15:29 inclusive
API_SLEEP_SECONDS = 0.7         # polite rate-limit between Breeze calls
MAX_RETRIES = 3
DEFAULT_FROM = date(2026, 9, 4)

log = logging.getLogger("vix_filler")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def timeout_call(fn, kwargs=None, timeout=60, label="API"):
    """Run fn in a worker thread with a hard timeout (Breeze calls can hang)."""
    q: "queue.Queue[tuple[str, object]]" = queue.Queue(1)
    kwargs = kwargs or {}

    def runner():
        try:
            q.put(("ok", fn(**kwargs)))
        except BaseException as e:  # noqa: BLE001 - propagated to caller
            q.put(("err", e))

    threading.Thread(target=runner, daemon=True).start()
    try:
        kind, val = q.get(timeout=timeout)
    except queue.Empty as e:
        raise TimeoutError(f"{label} timed out after {timeout}s") from e
    if kind == "err":
        raise val
    return val


def day_bounds(d: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    s = pd.Timestamp(datetime.combine(d, SESSION_OPEN), tz=IST)
    e = pd.Timestamp(datetime.combine(d, dtime(15, 30)), tz=IST)
    return s, e


def all_days(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


# --------------------------------------------------------------------------- #
# DuckDB
# --------------------------------------------------------------------------- #
DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    trade_time     TIMESTAMP NOT NULL,
    trade_date     DATE NOT NULL,
    open           DOUBLE NOT NULL,
    high           DOUBLE NOT NULL,
    low            DOUBLE NOT NULL,
    close          DOUBLE NOT NULL,
    volume         BIGINT,
    open_interest  BIGINT,
    source         VARCHAR NOT NULL
)
"""


def plan_days(con: duckdb.DuckDBPyConnection, start: date, end: date,
              include_partial: bool) -> list[dict]:
    """Classify each calendar day: holiday / weekend / complete / partial / fetch."""
    holidays = {r[0] for r in con.execute(
        "SELECT holiday_date FROM holiday_calendar WHERE holiday_date BETWEEN ? AND ?",
        [start, end],
    ).fetchall()}

    have = con.execute(
        f"""
        SELECT trade_date, COUNT(*) AS n, COUNT(DISTINCT trade_time) AS n_ts
        FROM {TABLE}
        WHERE trade_date BETWEEN ? AND ?
        GROUP BY trade_date
        """,
        [start, end],
    ).fetch_df()
    have_map = {r.trade_date.date() if hasattr(r.trade_date, "date") else r.trade_date:
                int(r.n_ts) for r in have.itertuples(index=False)}

    plan = []
    for d in all_days(start, end):
        if d in holidays:
            plan.append({"date": d, "action": "holiday"})
            continue
        if d.weekday() >= 5:
            plan.append({"date": d, "action": "weekend"})
            continue
        n_ts = have_map.get(d, 0)
        if n_ts >= EXPECTED_PER_DAY:
            plan.append({"date": d, "action": "complete"})
        elif n_ts == 0:
            plan.append({"date": d, "action": "fetch"})
        else:
            plan.append({
                "date": d,
                "action": "fetch-partial" if include_partial else "partial-skip",
                "have": n_ts,
            })
    return plan


def insert_candles(con: duckdb.DuckDBPyConnection, rows: list[dict], d: date) -> int:
    """Insert rows for one day, skipping timestamps already stored (no override)."""
    if not rows:
        return 0
    df = pd.DataFrame(rows)
    df["trade_date"] = d
    df["source"] = SOURCE_TAG
    df = df[["trade_time", "trade_date", "open", "high", "low", "close",
             "volume", "open_interest", "source"]]

    con.register("vix_incoming", df)
    try:
        before = con.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE trade_date = ?", [d]
        ).fetchone()[0]
        con.execute(
            f"""
            INSERT INTO {TABLE}
            SELECT i.* FROM vix_incoming i
            ANTI JOIN {TABLE} t ON t.trade_time = i.trade_time
            """
        )
        after = con.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE trade_date = ?", [d]
        ).fetchone()[0]
        return after - before
    finally:
        con.unregister("vix_incoming")


# --------------------------------------------------------------------------- #
# Breeze API
# --------------------------------------------------------------------------- #
def breeze_login():
    from breeze_connect import BreezeConnect

    api_key = os.environ.get("BREEZE_API_KEY")
    api_secret = os.environ.get("BREEZE_API_SECRET") or os.environ.get("BREEZE_SECRET_KEY")
    session_token = os.environ.get("BREEZE_SESSION_TOKEN")
    if not all([api_key, api_secret, session_token]):
        raise RuntimeError(
            "Breeze credentials missing. Need BREEZE_API_KEY, BREEZE_API_SECRET "
            "(or BREEZE_SECRET_KEY) and BREEZE_SESSION_TOKEN in .env"
        )
    client = BreezeConnect(api_key=api_key)
    client.generate_session(api_secret=api_secret, session_token=session_token)
    log.info("Breeze login successful")
    return client


def breeze_fetch(client, d: date) -> list[dict]:
    """Fetch one day of India VIX 1-minute candles from Breeze, normalized.

    NOTE: Breeze interprets from_date/to_date as IST even though the API
    documents them with a 'Z' (UTC) suffix. Verified live: sending
    '09:15:00.000Z'..'15:30:00.000Z' returns the full session (09:15-15:30 IST),
    whereas converting to real UTC truncates the day at 10:00 IST.
    """
    params = {
        "interval": "1minute",
        "from_date": f"{d.isoformat()}T{SESSION_OPEN.strftime('%H:%M:%S')}.000Z",
        "to_date": f"{d.isoformat()}T{dtime(15, 30).strftime('%H:%M:%S')}.000Z",
        "stock_code": "INDVIX",
        "exchange_code": "NSE",
        "product_type": "cash",
    }
    resp = timeout_call(client.get_historical_data_v2, kwargs=params,
                        timeout=60, label=f"Breeze VIX {d}")
    if not isinstance(resp, dict) or resp.get("Status") != 200:
        raise RuntimeError(f"Breeze response: {resp}")
    raw = resp.get("Success") or []

    s = pd.Timestamp(datetime.combine(d, SESSION_OPEN))
    e = pd.Timestamp(datetime.combine(d, dtime(15, 30)))
    out: dict[pd.Timestamp, dict] = {}
    for r in raw:
        if not isinstance(r, dict):
            continue
        ts_v = r.get("datetime") or r.get("date") or r.get("timestamp")
        try:
            ts = pd.Timestamp(ts_v)
            ts = ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)
        except Exception:
            continue
        ts = ts.tz_convert(IST).tz_localize(None)
        if ts.second or ts.microsecond or not (s <= ts <= e):
            continue

        def num(*keys):
            for k in keys:
                v = r.get(k)
                if v not in (None, ""):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
            return None

        o, h, l, c = num("open", "Open"), num("high", "High"), num("low", "Low"), num("close", "Close")
        v = num("volume", "Volume")
        if None in (o, h, l, c):
            continue
        out[ts] = {
            "trade_time": ts.to_pydatetime(),
            "open": o, "high": h, "low": l, "close": c,
            "volume": int(v) if v is not None else None,
            "open_interest": None,
        }
    return list(out.values())


def fetch_day_with_retry(client, d: date) -> list[dict]:
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return breeze_fetch(client, d)
        except Exception as e:  # noqa: BLE001
            last_exc = e
            wait = 2 ** attempt
            log.warning("Fetch failed for %s (attempt %d/%d): %s - retrying in %ds",
                        d, attempt, MAX_RETRIES, e, wait)
            time.sleep(wait)
    raise RuntimeError(f"All {MAX_RETRIES} attempts failed for {d}") from last_exc


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fill India VIX 1-min candles into DuckDB via Breeze")
    p.add_argument("--from-date", type=date.fromisoformat, default=DEFAULT_FROM,
                   help=f"Start date (default {DEFAULT_FROM})")
    p.add_argument("--to-date", type=date.fromisoformat,
                   default=date.today(),
                   help="End date (default: today)")
    p.add_argument("--execute", action="store_true",
                   help="Actually call Breeze and write to DuckDB (default: dry-run)")
    p.add_argument("--include-partial", action="store_true",
                   help="Also fetch days that exist but are incomplete "
                        "(only missing timestamps are inserted)")
    p.add_argument("--db", default=DUCK_PATH, help="Path to duckdb file")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    end = min(args.to_date, date.today())
    start = args.from_date
    if end < start:
        log.error("Invalid range: %s -> %s", start, end)
        return 1

    con = duckdb.connect(args.db)
    try:
        con.execute(DDL)
        plan = plan_days(con, start, end, args.include_partial)

        counts: dict[str, int] = {}
        for item in plan:
            counts[item["action"]] = counts.get(item["action"], 0) + 1

        log.info("=" * 78)
        log.info("INDIA VIX FILLER (duckdb) range=%s -> %s mode=%s",
                 start, end, "EXECUTE" if args.execute else "DRY-RUN")
        log.info("=" * 78)
        log.info("plan: %s", counts)
        for item in plan:
            if item["action"] in ("fetch", "fetch-partial", "partial-skip"):
                log.info("  %s: %s%s", item["date"], item["action"],
                         f" (have {item['have']}/{EXPECTED_PER_DAY})"
                         if "have" in item else "")

        todo = [i for i in plan if i["action"].startswith("fetch")]
        if not todo:
            log.info("Nothing to fetch - database already up to date.")
            return 0
        if not args.execute:
            log.info("DRY-RUN: no API calls or writes. Re-run with --execute.")
            return 0

        client = breeze_login()
        ok = partial = failed = 0
        total_inserted = 0
        for i, item in enumerate(todo, 1):
            d = item["date"]
            try:
                rows = fetch_day_with_retry(client, d)
                inserted = insert_candles(con, rows, d)
                total_inserted += inserted
                n_now = con.execute(
                    f"SELECT COUNT(DISTINCT trade_time) FROM {TABLE} WHERE trade_date = ?",
                    [d],
                ).fetchone()[0]
                status = "OK" if n_now >= EXPECTED_PER_DAY else "PARTIAL"
                ok += status == "OK"
                partial += status == "PARTIAL"
                log.info("[%d/%d] %s: provider=%d inserted=%d coverage=%d/%d %s",
                         i, len(todo), d, len(rows), inserted, n_now,
                         EXPECTED_PER_DAY, status)
            except Exception as e:  # noqa: BLE001
                failed += 1
                log.exception("[%d/%d] %s: FAILED %s: %s", i, len(todo), d,
                              type(e).__name__, e)
            time.sleep(API_SLEEP_SECONDS)

        log.info("=" * 78)
        log.info("SUMMARY")
        log.info("=" * 78)
        log.info("days_fetched_ok=%d days_partial=%d days_failed=%d rows_inserted=%d",
                 ok, partial, failed, total_inserted)
        log.info("india_vix total rows: %d",
                 con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0])
        return 0 if failed == 0 else 2
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
