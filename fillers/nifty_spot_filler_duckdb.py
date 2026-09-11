#!/usr/bin/env python3
"""
nifty_spot_filler_duckdb.py
---------------------------
Production filler for NIFTY 50 spot index 1-minute candles into DuckDB,
using the Angel One SmartAPI (getCandleData).

- Source       : Angel One SmartAPI, NSE index token 99926000 ("NIFTY 50")
- Destination  : `nifty_spot` table inside market_data.duckdb (created if absent)
- Holidays     : dates present in the DuckDB `holiday_calendar` are skipped
                 entirely, as are weekends.
- Idempotent   : a trading day that already has candles in `nifty_spot` is
                 skipped - existing data is NEVER overwritten or duplicated.
                 Within a fetched day, timestamps already stored are filtered
                 out with an anti-join before insert.
- Timezone     : timestamps are stored as naive IST, matching the existing
                 `nifty_spot` table. Angel timestamps are candle START times.
- Session      : 09:15 - 15:29 IST = 375 candles/day (project convention).

USAGE
-----
    # dry-run (no API calls, no writes) - default range: latest day -> today
    python nifty_spot_filler_duckdb.py

    # execute a range
    python nifty_spot_filler_duckdb.py --execute --from-date 2024-01-01 --to-date 2026-09-08

    # also top up days that exist but are incomplete (only missing ts inserted)
    python nifty_spot_filler_duckdb.py --execute --include-partial

Environment (.env): ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PASSWORD_OR_MPIN,
ANGEL_TOTP_SECRET
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
TABLE = "nifty_spot"
SOURCE_TAG = "angel:NIFTY50:NSE:99926000:1minute"

IST = "Asia/Kolkata"
SESSION_OPEN = dtime(9, 15)
SESSION_LAST = dtime(15, 29)
EXPECTED_PER_DAY = 375          # 09:15..15:29 inclusive
API_SLEEP_SECONDS = 1.0         # SmartAPI pacing (1 sec between getCandleData)
MAX_RETRIES = 3
NIFTY_TOKEN = "99926000"        # Angel One NIFTY 50 index token (project fallback)

log = logging.getLogger("nifty_filler")
log = logging.getLogger("nifty_filler")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def timeout_call(fn, args=(), kwargs=None, timeout=60, label="API"):
    """Run fn in a worker thread with a hard timeout (SmartAPI can hang)."""
    q: "queue.Queue[tuple[str, object]]" = queue.Queue(1)
    kwargs = kwargs or {}

    def runner():
        try:
            q.put(("ok", fn(*args, **kwargs)))
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
    source_file    VARCHAR NOT NULL
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
        SELECT trade_date, COUNT(DISTINCT trade_time) AS n_ts
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
    df["source_file"] = SOURCE_TAG
    df = df[["trade_time", "trade_date", "open", "high", "low", "close",
             "volume", "source_file"]]

    con.register("nifty_incoming", df)
    try:
        before = con.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE trade_date = ?", [d]
        ).fetchone()[0]
        con.execute(
            f"""
            INSERT INTO {TABLE}
            SELECT i.* FROM nifty_incoming i
            ANTI JOIN {TABLE} t ON t.trade_time = i.trade_time
            """
        )
        after = con.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE trade_date = ?", [d]
        ).fetchone()[0]
        return after - before
    finally:
        con.unregister("nifty_incoming")



# --------------------------------------------------------------------------- #
# Angel One SmartAPI
# --------------------------------------------------------------------------- #
def angel_login():
    try:
        import pyotp
        from SmartApi import SmartConnect
    except ImportError as exc:
        raise RuntimeError(
            "Angel dependencies are not installed. Run: pip install smartapi-python pyotp"
        ) from exc

    required = ["ANGEL_API_KEY", "ANGEL_CLIENT_CODE",
                "ANGEL_PASSWORD_OR_MPIN", "ANGEL_TOTP_SECRET"]
    missing = [x for x in required if not os.getenv(x)]
    if missing:
        raise RuntimeError("Missing Angel environment variables: " + ", ".join(missing))

    log.info("Creating SmartAPI client...")
    client = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
    totp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now()
    resp = timeout_call(
        client.generateSession,
        args=(os.environ["ANGEL_CLIENT_CODE"],
              os.environ["ANGEL_PASSWORD_OR_MPIN"],
              totp),
        timeout=30,
        label="Angel login",
    )
    if not resp or not resp.get("status", True):
        raise RuntimeError(f"SmartAPI login failed: {resp}")
    log.info("SmartAPI login successful")
    return client


def angel_fetch_day(client, d: date) -> list[dict]:
    """Fetch one day of NIFTY 50 1-minute candles from Angel One, normalized."""
    params = {
        "exchange": "NSE",
        "symboltoken": NIFTY_TOKEN,
        "interval": "ONE_MINUTE",
        "fromdate": f"{d.isoformat()} 09:15",
        "todate": f"{d.isoformat()} 15:30",
    }
    resp = timeout_call(client.getCandleData, args=(params,),
                        timeout=60, label=f"Angel NIFTY {d}")
    if not isinstance(resp, dict):
        raise RuntimeError(f"SmartAPI unexpected response for {d}: {resp!r}")
    if not resp.get("status"):
        # Angel returns status=false when a day has no data.
        log.debug("SmartAPI no data for %s: %s", d, resp)
        return []
    raw = resp.get("data") or []

    s = pd.Timestamp(datetime.combine(d, SESSION_OPEN))
    e = pd.Timestamp(datetime.combine(d, dtime(15, 30)))
    out: dict[pd.Timestamp, dict] = {}
    for row in raw:
        # SmartAPI list layout: [datetime, open, high, low, close, volume]
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            ts = pd.Timestamp(row[0])
            ts = ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)
        except Exception:
            continue
        ts = ts.tz_convert(IST).tz_localize(None)
        if ts < s or ts >= e or ts.second or ts.microsecond:
            continue
        o, h, l, c = row[1], row[2], row[3], row[4]
        v = row[5]
        if any(x is None for x in (o, h, l, c)):
            continue
        out[ts] = {
            "trade_time": ts.to_pydatetime(),
            "open": float(o), "high": float(h),
            "low": float(l), "close": float(c),
            "volume": int(float(v)) if v not in (None, "") else None,
        }
    return list(out.values())


def fetch_day_with_retry(client, d: date) -> list[dict]:
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return angel_fetch_day(client, d)
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
    p = argparse.ArgumentParser(description="Fill NIFTY 50 spot 1-min candles into DuckDB via Angel One")
    p.add_argument("--from-date", type=date.fromisoformat, default=None,
                   help="Start date YYYY-MM-DD (default: latest day in nifty_spot + 1)")
    p.add_argument("--to-date", type=date.fromisoformat,
                   default=date.today(),
                   help="End date (default: today)")
    p.add_argument("--execute", action="store_true",
                   help="Actually call SmartAPI and write to DuckDB (default: dry-run)")
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
    con = duckdb.connect(args.db)
    try:
        con.execute(DDL)

        start = args.from_date
        if start is None:
            row = con.execute(f"SELECT MAX(trade_date) FROM {TABLE}").fetchone()
            start = (row[0] + timedelta(days=1)) if row and row[0] else date(2024, 1, 1)
            if isinstance(start, datetime):
                start = start.date()
        if end < start:
            log.info("Nothing to do: range %s -> %s is empty.", start, end)
            return 0

        plan = plan_days(con, start, end, args.include_partial)
        counts: dict[str, int] = {}
        for item in plan:
            counts[item["action"]] = counts.get(item["action"], 0) + 1

        log.info("=" * 78)
        log.info("NIFTY 50 SPOT FILLER (duckdb) range=%s -> %s mode=%s",
                 start, end, "EXECUTE" if args.execute else "DRY-RUN")
        log.info("=" * 78)
        log.info("plan: %s", counts)
        for item in plan:
            if item["action"] in ("fetch", "fetch-partial", "partial-skip"):
                log.info("  %s: %s%s", item["date"], item["action"],
                         f" (have {item['have']}/{EXPECTED_PER_DAY})"
                         if "have" in item else "")

        print_gap_report(con, start, end)

        todo = [i for i in plan if i["action"].startswith("fetch")]
        if not todo:
            log.info("Nothing to fetch - database already up to date.")
            return 0
        if not args.execute:
            log.info("DRY-RUN: no API calls or writes. Re-run with --execute.")
            return 0

        client = angel_login()
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
        log.info("days_ok=%d days_partial=%d days_failed=%d rows_inserted=%d",
                 ok, partial, failed, total_inserted)
        log.info("nifty_spot total rows: %d",
                 con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0])

        return 0 if failed == 0 else 2
    finally:
        con.close()


def print_gap_report(con: duckdb.DuckDBPyConnection, start: date, end: date) -> None:
    """Print missing candles (09:15-15:29 grid) per trading day for the range."""
    print("\n" + "=" * 78)
    print(f"MISSING CANDLES REPORT  ({start} -> {end}, session 09:15-15:29)")
    print("=" * 78)

    rows = con.execute(
        f"""
        WITH days AS (
            SELECT UNNEST(generate_series(CAST(? AS DATE), CAST(? AS DATE),
                                          INTERVAL 1 DAY)::DATE[]) AS td
        ),
        grid AS (SELECT UNNEST(generate_series(0, {EXPECTED_PER_DAY - 1})) AS i),
        exp AS (
            SELECT d.td AS trade_date,
                   CAST(d.td AS TIMESTAMP) + to_minutes(555 + g.i) AS ts
            FROM days d CROSS JOIN grid g
        ),
        holidays AS (
            SELECT holiday_date FROM holiday_calendar
            WHERE holiday_date BETWEEN ? AND ?
        ),
        have AS (
            SELECT trade_time FROM {TABLE}
            WHERE trade_date BETWEEN ? AND ?
        )
        SELECT e.trade_date, e.ts AS missing_ts
        FROM exp e
        ANTI JOIN have h ON h.trade_time = e.ts
        WHERE extract(dow from e.trade_date) BETWEEN 1 AND 5
          AND e.trade_date NOT IN (SELECT holiday_date FROM holidays)
        ORDER BY 1, 2
        """,
        [start, end, start, end, start, end],
    ).fetchall()

    if not rows:
        print("No missing candles - all trading days are complete (375/375).")
        return

    from collections import defaultdict
    by_day: dict[date, list] = defaultdict(list)
    for trade_date, ts in rows:
        by_day[trade_date].append(ts.time())

    total = 0
    for trade_date in sorted(by_day):
        times = by_day[trade_date]
        total += len(times)
        # compress consecutive minutes into ranges
        spans = []
        run_start = prev = times[0]
        for t in times[1:]:
            if (t.hour * 60 + t.minute) == (prev.hour * 60 + prev.minute) + 1:
                prev = t
                continue
            spans.append((run_start, prev))
            run_start = prev = t
        spans.append((run_start, prev))
        span_txt = ", ".join(
            f"{a.strftime('%H:%M')}" if a == b else f"{a.strftime('%H:%M')}-{b.strftime('%H:%M')}"
            for a, b in spans
        )
        print(f"  {trade_date}: missing {len(times):3d}/{EXPECTED_PER_DAY} -> {span_txt}")
    print(f"  TOTAL missing: {total} candles across {len(by_day)} trading day(s)")
    print("  (Provider history does not include these minutes; no synthetic "
          "candles are created.)")



if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.error("Interrupted by user.")
        sys.exit(130)

TCS