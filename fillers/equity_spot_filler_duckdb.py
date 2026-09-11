#!/usr/bin/env python3
"""
equity_spot_filler_duckdb.py
-----------------------------
Fetch 1-minute candle data for 12 key equities, with Angel One as primary
and Breeze as fallback for missing data. Stored in `equity_spot` DuckDB table.

Universe: HDFCBANK, ICICIBANK, RELIANCE, BHARTIARTL, LT, SBIN, INFY,
          AXISBANK, KOTAKBANK, M&M, BAJFINANCE, ITC

Schema: symbol, trade_time, open, high, low, close, volume

Data range: 2025-08-01 to today (configurable via --from-date/--to-date)
Session: 09:15-15:29 IST = 375 candles/day (project convention, matches nifty_spot)

python fillers/equity_spot_filler_duckdb.py                                    # dry-run (default from 2025-08-01)
python fillers/equity_spot_filler_duckdb.py --execute                          # fetch + store
python fillers/equity_spot_filler_duckdb.py --execute --include-partial        # top up partial days
python fillers/equity_spot_filler_duckdb.py --execute --from-date 2025-08-01 --to-date 2025-08-31

Environment (.env): ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PASSWORD_OR_MPIN,
                    ANGEL_TOTP_SECRET, BREEZE_API_KEY, BREEZE_SECRET_KEY,
                    BREEZE_SESSION_TOKEN
"""

import argparse
import logging
import os
import queue
import sys
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import duckdb
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
DUCK_PATH = os.path.join(BASE_DIR, "market_data.duckdb")
TABLE = "equity_spot"

IST = "Asia/Kolkata"
SESSION_OPEN = dtime(9, 15)
SESSION_LAST = dtime(15, 29)
EXPECTED_PER_DAY = 375  # 09:15..15:29 inclusive

EQUITY_SYMBOLS = [
    "HDFCBANK", "ICICIBANK", "RELIANCE", "BHARTIARTL", "LT", "SBIN",
    "INFY", "AXISBANK", "KOTAKBANK", "M&M", "BAJFINANCE", "ITC",
]

ANGEL_DELAY = 1.0
BREEZE_DELAY = 0.7
MAX_RETRIES = 3
API_TIMEOUT = 60
SCRIP_MASTER_URL = (
    "https://margincalculator.angelbroking.com/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)

log = logging.getLogger("equity_spot_filler")


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
        except BaseException as e:
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


def to_ist_ts(value: Any) -> Optional[pd.Timestamp]:
    """Coerce a broker timestamp to a naive IST candle-start datetime."""
    try:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize(IST)
        else:
            ts = ts.tz_convert(IST)
        return ts.tz_localize(None)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Angel One SmartAPI
# --------------------------------------------------------------------------- #

def angel_login():
    """Log into Angel One SmartAPI using TOTP."""
    import pyotp
    from SmartApi import SmartConnect
    client = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
    totp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now()
    client.generateSession(
        os.environ["ANGEL_CLIENT_CODE"],
        os.environ["ANGEL_PASSWORD_OR_MPIN"],
        totp,
    )
    log.info("Angel One login OK")
    return client


def load_scrip_master() -> List[Dict]:
    """Download the Angel OpenAPI scrip master (cached per run)."""
    log.info("Downloading Angel scrip master ...")
    r = requests.get(SCRIP_MASTER_URL, timeout=60)
    r.raise_for_status()
    return r.json()


def resolve_angel_token(symbol: str, scrip_master: List[Dict]) -> Optional[str]:
    """Resolve a trading symbol to its Angel One NSE equity instrument token.

    For NSE equities, the scrip master uses:
      name    = "HDFCBANK"   (company name)
      symbol  = "HDFCBANK-EQ" (trading symbol)
      strike  = "-1.000000"  (distinguishes from options)
    """
    sym = symbol.upper()
    for row in scrip_master:
        if (row.get("exch_seg") == "NSE"
                and row.get("name", "").upper() == sym
                and row.get("strike") == "-1.000000"):
            return row["token"]
    return None


def angel_fetch_day(client, symbol: str, token: str, day: date) -> List[Dict]:
    """Fetch one day of 1-min candles from Angel One for an equity."""
    sd = datetime.combine(day, SESSION_OPEN)
    ed = datetime.combine(day, dtime(15, 30))  # request 15:30 to include 15:29 candle
    params = {
        "exchange": "NSE",
        "symboltoken": token,
        "interval": "ONE_MINUTE",
        "fromdate": sd.strftime("%Y-%m-%d %H:%M"),
        "todate": ed.strftime("%Y-%m-%d %H:%M"),
    }
    raw = timeout_call(
        client.getCandleData,
        args=(params,),
        timeout=API_TIMEOUT,
        label=f"angel:{symbol}:{day}",
    )
    return _normalize_angel(raw, symbol, day)


def _normalize_angel(raw: Dict, symbol: str, day: date) -> List[Dict]:
    """Normalize Angel One getCandleData response to our row format."""
    out: Dict[datetime, Dict] = {}
    for row in (raw.get("data") or []):
        if len(row) < 6:
            continue
        ts = to_ist_ts(row[0])
        if ts is None:
            continue
        if ts.time() < SESSION_OPEN or ts.time() > SESSION_LAST:
            continue
        if ts.second or ts.microsecond:
            continue
        try:
            o, h, l, c, v = float(row[1]), float(row[2]), float(row[3]), float(row[4]), int(row[5])
        except (TypeError, ValueError):
            continue
        out[ts.to_pydatetime()] = {
            "symbol": symbol,
            "trade_time": ts.to_pydatetime(),
            "open": o, "high": h, "low": l, "close": c, "volume": v,
        }
    return list(out.values())


# --------------------------------------------------------------------------- #
# Breeze Connect (fallback)
# --------------------------------------------------------------------------- #

def breeze_login():
    """Log into Breeze Connect."""
    from breeze_connect import BreezeConnect
    required = ["BREEZE_API_KEY", "BREEZE_API_SECRET", "BREEZE_SESSION_TOKEN"]
    for k in required:
        if not os.environ.get(k):
            raise RuntimeError(f"Missing env var {k}")
    client = BreezeConnect(api_key=os.environ["BREEZE_API_KEY"])
    client.generate_session(
        api_secret=os.environ["BREEZE_API_SECRET"],
        session_token=os.environ["BREEZE_SESSION_TOKEN"],
    )
    log.info("Breeze login OK")
    return client


def breeze_fetch_day(client, symbol: str, day: date) -> List[Dict]:
    """Fetch one day of 1-min candles from Breeze for an equity."""
    sd = datetime.combine(day, SESSION_OPEN)
    ed = datetime.combine(day, dtime(15, 30))
    raw = timeout_call(
        client.get_historical_data_v2,
        kwargs={
            "interval": "1minute",
            "from_date": sd.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "to_date": ed.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "stock_code": symbol,
            "exchange_code": "NSE",
            "product_type": "cash",
        },
        timeout=API_TIMEOUT,
        label=f"breeze:{symbol}:{day}",
    )
    if not isinstance(raw, dict) or raw.get("Status") != 200:
        log.warning("Breeze returned non-OK status for %s %s: %s", symbol, day, raw)
        return []
    return _normalize_breeze(raw, symbol, day)


def _normalize_breeze(raw: Dict, symbol: str, day: date) -> List[Dict]:
    """Normalize Breeze get_historical_data_v2 response to our row format."""
    out: Dict[datetime, Dict] = {}
    for rec in (raw.get("Success") or []):
        ts = to_ist_ts(rec.get("datetime"))
        if ts is None:
            continue
        if ts.time() < SESSION_OPEN or ts.time() > SESSION_LAST:
            continue
        if ts.second or ts.microsecond:
            continue
        try:
            o = float(rec.get("open", 0))
            h = float(rec.get("high", 0))
            l = float(rec.get("low", 0))
            c = float(rec.get("close", 0))
            v = int(rec.get("volume", 0))
        except (TypeError, ValueError):
            continue
        out[ts.to_pydatetime()] = {
            "symbol": symbol,
            "trade_time": ts.to_pydatetime(),
            "open": o, "high": h, "low": l, "close": c, "volume": v,
        }
    return list(out.values())


# --------------------------------------------------------------------------- #
# DuckDB
# --------------------------------------------------------------------------- #

DDL = """
CREATE TABLE IF NOT EXISTS equity_spot (
    symbol      VARCHAR,
    trade_time  TIMESTAMP,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    volume      BIGINT
)
"""


def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DDL)


def existing_ts(con: duckdb.DuckDBPyConnection, symbol: str, day: date) -> set:
    """Return set of trade_time already stored for a symbol on a given day."""
    rows = con.execute(
        f"SELECT trade_time FROM {TABLE} WHERE symbol=? AND CAST(trade_time AS DATE)=?",
        [symbol, day],
    ).fetchall()
    return {r[0] for r in rows}


def day_count(con: duckdb.DuckDBPyConnection, symbol: str, day: date) -> int:
    return con.execute(
        f"SELECT COUNT(*) FROM {TABLE} WHERE symbol=? AND CAST(trade_time AS DATE)=?",
        [symbol, day],
    ).fetchone()[0]


def write_rows(con: duckdb.DuckDBPyConnection, rows: List[Dict]) -> int:
    """Insert rows, skipping any (symbol, trade_time) already present.
    Returns the actual number of rows inserted."""
    if not rows:
        return 0
    ensure_table(con)
    before = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    vals = []
    for r in rows:
        # Escape single quotes in symbol just in case
        sym = r["symbol"].replace("'", "''")
        vals.append(
            f"('{sym}','{r['trade_time'].strftime('%Y-%m-%d %H:%M:%S')}',"
            f"{r['open']},{r['high']},{r['low']},{r['close']},{r['volume']})"
        )
    values_sql = ",".join(vals)
    con.execute(
        f"""
        INSERT INTO equity_spot (symbol, trade_time, open, high, low, close, volume)
        SELECT symbol, trade_time, open, high, low, close, volume
        FROM (VALUES {values_sql}) AS t(symbol, trade_time, open, high, low, close, volume)
        WHERE NOT EXISTS (
            SELECT 1 FROM equity_spot e
            WHERE e.symbol = t.symbol AND e.trade_time = t.trade_time
        )
        """
    )
    after = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    return after - before


def print_gap_report(con: duckdb.DuckDBPyConnection, start: date, end: date) -> None:
    """Print missing candles per symbol per trading day for the range."""
    print("\n" + "=" * 78)
    print(f"MISSING CANDLES REPORT  ({start} -> {end}, session 09:15-15:29)")
    print("=" * 78)

    rows = con.execute(
        """
        WITH days AS (
            SELECT UNNEST(generate_series(CAST(? AS DATE), CAST(? AS DATE),
                                                  INTERVAL 1 DAY)::DATE[]) AS td
        ),
        grid AS (SELECT UNNEST(generate_series(0, 374)) AS i),
        exp AS (
            SELECT s.symbol, d.td AS trade_date,
                   CAST(d.td AS TIMESTAMP) + to_minutes(555 + g.i) AS ts
            FROM days d CROSS JOIN grid g
            CROSS JOIN (SELECT DISTINCT symbol FROM equity_spot) s
        ),
        holidays AS (
            SELECT holiday_date FROM holiday_calendar
            WHERE holiday_date BETWEEN ? AND ?
        ),
        have AS (
            SELECT symbol, trade_time FROM equity_spot
            WHERE trade_date(trade_time) BETWEEN ? AND ?
        )
        SELECT e.symbol, e.trade_date, e.ts AS missing_ts
        FROM exp e
        ANTI JOIN have h ON h.symbol = e.symbol AND h.trade_time = e.ts
        WHERE extract(dow from e.trade_date) BETWEEN 1 AND 5
          AND e.trade_date NOT IN (SELECT holiday_date FROM holidays)
        ORDER BY 1, 2, 3
        """,
        [start, end, start, end, start, end],
    ).fetchall()

    if not rows:
        print("No missing candles - all trading days are complete (375/375).")
        return

    from collections import defaultdict
    by_symbol_day: Dict[Tuple[str, date], list] = defaultdict(list)
    for symbol, trade_date, ts in rows:
        by_symbol_day[(symbol, trade_date)].append(ts.time())

    total = 0
    for (symbol, trade_date) in sorted(by_symbol_day):
        times = by_symbol_day[(symbol, trade_date)]
        total += len(times)
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
        print(f"  {symbol:12s} {trade_date}: missing {len(times):3d}/375 -> {span_txt}")
    print(f"  TOTAL missing: {total} candles across {len(by_symbol_day)} symbol-day(s)")
    print("  (Provider history does not include these minutes; no synthetic candles are created.)")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def fetch_with_retry(fetch_fn, *args, max_retries=MAX_RETRIES):
    """Fetch with exponential backoff retry."""
    for attempt in range(max_retries):
        try:
            return fetch_fn(*args)
        except Exception as e:
            if attempt < max_retries - 1:
                delay = 2 ** attempt
                log.warning("Retry %d/%d for %s: %s (sleep %ds)",
                            attempt + 1, max_retries, args, e, delay)
                time.sleep(delay)
            else:
                raise


def main() -> int:
    p = argparse.ArgumentParser(
        description="Fill equity 1-minute candles (12 symbols) into DuckDB."
    )
    p.add_argument("--execute", action="store_true",
                   help="Actually call APIs and write to DuckDB (default: dry-run)")
    p.add_argument("--from-date", type=date.fromisoformat,
                   default=date(2025, 8, 1),
                   help="Start date (default: 2025-08-01)")
    p.add_argument("--to-date", type=date.fromisoformat,
                   default=date.today(),
                   help="End date (default: today)")
    p.add_argument("--include-partial", action="store_true",
                   help="Also top up days that exist but are incomplete")
    p.add_argument("--broker", choices=["angel", "breeze", "auto"], default="auto",
                   help="Primary broker (default: auto = Angel primary, Breeze fallback)")
    p.add_argument("--check-volume", action="store_true", default=True,
                   help="Report volume availability per symbol (default: on)")
    p.add_argument("--no-check-volume", dest="check_volume", action="store_false")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--db", default=DUCK_PATH, help="Path to DuckDB file")

    args = p.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")

    start = args.from_date
    end = min(args.to_date, date.today())

    if start > end:
        log.error("Empty range: %s -> %s", start, end)
        return 1

    # Holiday lookup
    con = duckdb.connect(args.db, read_only=True)
    holidays = set()
    try:
        rows = con.execute(
            "SELECT holiday_date FROM holiday_calendar WHERE holiday_date BETWEEN ? AND ?",
            [start, end],
        ).fetchall()
        holidays = {r[0] for r in rows}
    except Exception:
        log.warning("Could not read holiday_calendar")

    # Trading days in range
    days = [d for d in all_days(start, end)
            if d.weekday() < 5 and d not in holidays]
    con.close()

    log.info("range=%s -> %s trading_days=%d holidays=%d mode=%s broker=%s",
             start, end, len(days), len(holidays),
             "EXECUTE" if args.execute else "DRY-RUN", args.broker)

    if not days:
        log.info("No trading days in range.")
        return 0

    # Resolve Angel tokens if Angel is primary or auto
    angel_client = None
    angel_tokens: Dict[str, str] = {}
    if args.broker in ("angel", "auto"):
        log.info("Logging into Angel One ...")
        angel_client = angel_login()
        log.info("Loading scrip master ...")
        scrip_master = load_scrip_master()
        for sym in EQUITY_SYMBOLS:
            tok = resolve_angel_token(sym, scrip_master)
            if tok:
                angel_tokens[sym] = tok
                log.info("  %s -> Angel token %s", sym, tok)
            else:
                log.warning("  %s -> Angel token NOT FOUND", sym)
        missing = [s for s in EQUITY_SYMBOLS if s not in angel_tokens]
        if missing:
            log.warning("Symbols without Angel tokens (will use Breeze): %s", missing)

    # Plan: for each symbol-day, decide what to do
    # Ensure table exists (needs write access) - create empty if absent so planning queries work
    con_w = duckdb.connect(args.db)
    ensure_table(con_w)
    con_w.close()

    con = duckdb.connect(args.db, read_only=True)
    plan = []  # (symbol, day, status, have, expected)
    for sym in EQUITY_SYMBOLS:
        for d in days:
            have = day_count(con, sym, d)
            if have >= EXPECTED_PER_DAY:
                status = "complete-skip"
            elif have > 0:
                status = "partial-skip" if not args.include_partial else "partial-fill"
            else:
                status = "fetch"
            plan.append((sym, d, status, have, EXPECTED_PER_DAY))

    # Summary counts
    counts = {}
    for _, _, s, _, _ in plan:
        counts[s] = counts.get(s, 0) + 1
    log.info("plan: %s", counts)

    # Volume check (dry-run)
    if args.check_volume:
        log.info("=" * 78)
        log.info("VOLUME AVAILABILITY CHECK")
        log.info("=" * 78)
        vol_rows = con.execute(
            """
            SELECT symbol, COUNT(*) AS rows,
                   SUM(CASE WHEN volume IS NULL OR volume = 0 THEN 1 ELSE 0 END) AS zero_vol
            FROM equity_spot GROUP BY symbol ORDER BY symbol
            """,
        ).fetchall()
        for sym, rows, zero_vol in vol_rows:
            pct = (zero_vol / rows * 100) if rows else 0
            log.info("  %s: %d rows, %d (%.1f%%) with zero/null volume", sym, rows, zero_vol, pct)
        if not vol_rows:
            log.info("  equity_spot table is empty")

    con.close()

    if not args.execute:
        log.info("DRY-RUN: no API calls or writes. Use --execute to fetch and store.")
        return 0

    # Execute
    breeze_client = None
    total_inserted = 0
    ok = partial = failed = 0

    for idx, (sym, d, status, have, expected) in enumerate(plan, 1):
        if status == "complete-skip":
            ok += 1
            continue

        log.info("[%d/%d] %s %s: %s (have %d/%d)",
                 idx, len(plan), sym, d, status, have, expected)

        rows: List[Dict] = []

        # Primary: Angel
        if args.broker in ("angel", "auto") and sym in angel_tokens:
            zero_vol_timestamps = []
            try:
                fetched = fetch_with_retry(
                    angel_fetch_day, angel_client, sym, angel_tokens[sym], d
                )
                log.info("  Angel returned %d candles", len(fetched))
                # Filter out rows with volume=0 or NULL — Breeze will provide these
                rows = []
                for r in fetched:
                    if r.get("volume") is None or r.get("volume") == 0:
                        zero_vol_timestamps.append(r["trade_time"])
                    else:
                        rows.append(r)
                if zero_vol_timestamps:
                    log.info("  Angel had %d candle(s) with zero/null volume — will fallback to Breeze",
                             len(zero_vol_timestamps))
            except Exception as e:
                log.warning("  Angel fetch failed: %s", e)

            # Fallback to Breeze if Angel incomplete OR had zero-volume candles
            if args.broker == "auto" and (len(rows) < EXPECTED_PER_DAY or zero_vol_timestamps):
                reason = []
                if len(rows) < EXPECTED_PER_DAY:
                    reason.append(f"{len(rows)}/{EXPECTED_PER_DAY} count")
                if zero_vol_timestamps:
                    reason.append(f"{len(zero_vol_timestamps)} zero-volume")
                log.info("  Angel needs fallback (%s), trying Breeze ...", ", ".join(reason))
                if breeze_client is None:
                    breeze_client = breeze_login()
                try:
                    breeze_rows = fetch_with_retry(
                        breeze_fetch_day, breeze_client, sym, d
                    )
                    log.info("  Breeze returned %d candles", len(breeze_rows))
                    # Merge: use Angel data, fill gaps from Breeze
                    existing_ts_set = {r["trade_time"] for r in rows}
                    for br in breeze_rows:
                        if br["trade_time"] not in existing_ts_set:
                            rows.append(br)
                            existing_ts_set.add(br["trade_time"])
                except Exception as e:
                    log.warning("  Breeze fallback failed: %s", e)

        # Breeze primary
        elif args.broker == "breeze":
            if breeze_client is None:
                breeze_client = breeze_login()
            try:
                rows = fetch_with_retry(breeze_fetch_day, breeze_client, sym, d)
                log.info("  Breeze returned %d candles", len(rows))
            except Exception as e:
                log.warning("  Breeze fetch failed: %s", e)

        # Write to DuckDB
        new_rows: List[Dict] = []
        if rows:
            new_rows = rows  # rows already deduplicated vs DB in write_rows()

            con_w = duckdb.connect(args.db)
            written = write_rows(con_w, new_rows)
            total_inserted += written
            if written > 0:
                log.info("  Inserted %d new candles", written)
            else:
                log.info("  No new candles to insert")
            con_w.close()

        # Update counts
        new_have = have + len(new_rows)
        if new_have >= EXPECTED_PER_DAY:
            ok += 1
        elif new_have > have:
            partial += 1
        else:
            failed += 1

        time.sleep(ANGEL_DELAY if args.broker == "angel" else BREEZE_DELAY)

    log.info("=" * 78)
    log.info("SUMMARY")
    log.info("=" * 78)
    log.info("total_inserted=%d days_ok=%d days_partial=%d days_failed=%d",
             total_inserted, ok, partial, failed)

    con = duckdb.connect(args.db, read_only=True)
    total_rows = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    log.info("equity_spot total rows: %d", total_rows)

    # Per-symbol counts
    sym_rows = con.execute(
        f"SELECT symbol, COUNT(*) FROM {TABLE} GROUP BY symbol ORDER BY symbol"
    ).fetchall()
    for sym, cnt in sym_rows:
        log.info("  %s: %d", sym, cnt)
    con.close()

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.error("Interrupted by user.")
        sys.exit(130)
