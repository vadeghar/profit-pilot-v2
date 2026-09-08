"""
breeze_fetcher.py -- Production NIFTY spot & options data fetcher (Breeze API)
==============================================================================

Fetches NIFTY 1-minute spot candles and options data from the ICICI Breeze
API and upserts them into DuckDB:

  * nifty_spot      -- 1-minute OHLCV index candles
  * options_ticks   -- option ticks (CE/PE)

Usage
-----
Daily incremental fetch (default: last 5 days, spot only):

    python src/breeze_fetcher.py

Spot + options for specific strikes and expiries:

    python src/breeze_fetcher.py --from 2026-01-01 --to 2026-01-31 \
        --strikes 24000,24100 --expiries 2026-01-27

As a library / scheduled job:

    from src.breeze_fetcher import run_daily_fetch
    run_daily_fetch(days_back=5, strikes=[24000])

Features
--------
* Idempotent: per-instrument dedup keys, re-runs never duplicate rows.
* Retry + exponential backoff on Breeze calls; per-chunk error isolation.
* Session token from .env (BREEZE_SESSION_TOKEN); fails fast with a clear
  message when the token has expired.
* Structured logging; chunked date ranges respecting Breeze's 30-day limit.
"""

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

import duckdb
from dotenv import load_dotenv

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
_db_raw = os.getenv("DUCKDB_PATH", os.path.join("data", "market_data.duckdb"))
# Relative paths are resolved against the project root, not the CWD,
# so the scripts work from any working directory.
DB_PATH = _db_raw if os.path.isabs(_db_raw) or os.path.splitdrive(_db_raw)[0] \
    else os.path.join(PROJECT_ROOT, _db_raw)

STOCK_CODE = "NIFTY"           # Breeze instrument code for NIFTY 50
EXCHANGE_INDEX = "NSE"         # cash/index segment
EXCHANGE_DERIV = "NFO"         # derivatives segment

BREEZE_MAX_DAYS_PER_CALL = 30  # Breeze hard limit per historical call
BREEZE_1MIN_SOFT_DAYS = 5      # conservative chunk for 1-minute data
RETRY_ATTEMPTS = 4
RETRY_BASE_DELAY = 5           # seconds; doubles per attempt
REQUEST_GAP = 0.5              # seconds between Breeze calls (rate limiting)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("breeze_fetcher")


# --------------------------------------------------------------------------
# Breeze session
# --------------------------------------------------------------------------
def build_session():
    """Authenticate a BreezeConnect session from .env credentials.

    The session token (BREEZE_SESSION_TOKEN) comes from the iDirect TOTP
    login flow and must be regenerated whenever Breeze expires it.
    """
    from breeze_connect import BreezeConnect

    api_key = os.getenv("BREEZE_API_KEY")
    api_secret = os.getenv("BREEZE_API_SECRET")
    session_token = os.getenv("BREEZE_SESSION_TOKEN", "").split("#")[0].strip()

    if not (api_key and api_secret and session_token):
        raise RuntimeError(
            "Missing Breeze credentials in .env -- need BREEZE_API_KEY, "
            "BREEZE_API_SECRET and BREEZE_SESSION_TOKEN."
        )

    bc = BreezeConnect(api_key=api_key)
    bc.generate_session(api_secret=api_secret, session_token=session_token)
    log.info("Breeze session authenticated.")
    return bc


def breeze_call(bc, method: str, **kwargs) -> Any:
    """Invoke a Breeze API method with retry + exponential backoff."""
    last_err: Optional[Exception] = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = getattr(bc, method)(**kwargs)
            if isinstance(resp, dict) and resp.get("Status") == 400:
                raise RuntimeError(f"Breeze error: {resp.get('Error')}")
            return resp
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            log.warning("Breeze %s failed (attempt %d/%d): %s -- retry in %.0fs",
                        method, attempt, RETRY_ATTEMPTS, exc, delay)
            if attempt < RETRY_ATTEMPTS:
                time.sleep(delay)
    raise RuntimeError(f"Breeze {method} failed after {RETRY_ATTEMPTS} attempts: {last_err}")


def extract_rows(resp: Any) -> List[Dict[str, Any]]:
    """Normalise a Breeze historical-data response to a list of row dicts.

    Breeze returns rows under 'Success' (observed live) and some endpoints
    use 'Result'/'Data'; all three are handled.
    """
    if not isinstance(resp, dict):
        return []
    for key in ("Success", "Result"):
        result = resp.get(key)
        if isinstance(result, list):
            return [r for r in result if isinstance(r, dict)]
        if isinstance(result, dict):
            return [r for r in (result.get("Data") or []) if isinstance(r, dict)]
    return []


def chunk_dates(start: date, end: date, max_days: int) -> Iterable[tuple[date, date]]:
    """Split [start, end] into inclusive chunks of at most max_days days."""
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=max_days - 1), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


# --------------------------------------------------------------------------
# DuckDB tables + idempotent upserts
# --------------------------------------------------------------------------
def ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Create tables if absent. options_ticks may already exist from the zip
    ETL (no PK) -- we only create when absent and dedupe on insert."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS nifty_spot (
            trade_time  TIMESTAMP NOT NULL,
            trade_date  DATE       NOT NULL,
            open        DOUBLE,
            high        DOUBLE,
            low         DOUBLE,
            close       DOUBLE,
            volume      BIGINT,
            source_file VARCHAR,
            CONSTRAINT nifty_spot_pk PRIMARY KEY (trade_time)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS options_ticks (
            trade_time     TIMESTAMP,
            trade_date     DATE,
            expiry_date    DATE,
            strike_price   INTEGER,
            option_type    VARCHAR,
            price          DOUBLE,
            volume         BIGINT,
            open_interest  BIGINT,
            iv             DOUBLE,
            delta          DOUBLE,
            gamma          DOUBLE,
            theta          DOUBLE,
            vega           DOUBLE
        )
    """)


def _parse_ts(raw) -> Optional[datetime]:
    if not raw:
        return None
    raw = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%d-%b-%Y %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    log.warning("Unparseable datetime: %r", raw)
    return None


def _parse_date(raw) -> Optional[date]:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(str(raw).strip().split(" ")[0], fmt).date()
        except ValueError:
            continue
    return None


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Idempotent inserts
# --------------------------------------------------------------------------
def insert_spot(con, rows: List[Dict[str, Any]]) -> int:
    """Insert spot candles; skips trade_times already present."""
    con.execute("BEGIN TRANSACTION")
    try:
        inserted = 0
        for r in rows:
            ts = _parse_ts(r.get("datetime"))
            if ts is None:
                continue
            if con.execute("SELECT 1 FROM nifty_spot WHERE trade_time = ?", [ts]).fetchone():
                continue
            con.execute(
                """INSERT INTO nifty_spot
                       (trade_time, trade_date, open, high, low, close, volume, source_file)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'BREEZE:1minute')""",
                [ts, ts.date(), _f(r.get("open")), _f(r.get("high")),
                 _f(r.get("low")), _f(r.get("close")), _i(r.get("volume"))],
            )
            inserted += 1
        con.execute("COMMIT")
        return inserted
    except Exception:
        con.execute("ROLLBACK")
        raise


def insert_options(con, rows: List[Dict[str, Any]], expiry: Optional[date]) -> int:
    """Insert option ticks; dedupe key = (trade_time, type, strike, expiry)."""
    con.execute("BEGIN TRANSACTION")
    try:
        inserted = 0
        for r in rows:
            ts = _parse_ts(r.get("datetime"))
            if ts is None:
                continue
            exp = _parse_date(r.get("expiry_date")) or expiry
            strike = _i(r.get("strike_price"))
            otype = {"CALL": "CE", "PUT": "PE", "OTHERS": "FUT"}.get(
                (r.get("right") or "").upper(), (r.get("right") or "").upper())
            if not otype:
                continue
            exists = con.execute(
                """SELECT 1 FROM options_ticks
                   WHERE trade_time = ? AND option_type = ?
                     AND COALESCE(strike_price, -1) = COALESCE(?, -1)
                     AND COALESCE(expiry_date, DATE '1900-01-01')
                         = COALESCE(?, DATE '1900-01-01')""",
                [ts, otype, strike, exp],
            ).fetchone()
            if exists:
                continue
            con.execute(
                """INSERT INTO options_ticks
                       (trade_time, trade_date, expiry_date, strike_price,
                        option_type, price, volume, open_interest)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [ts, ts.date(), exp, strike, otype,
                 _f(r.get("close")), _i(r.get("volume")), _i(r.get("open_interest"))],
            )
            inserted += 1
        con.execute("COMMIT")
        return inserted
    except Exception:
        con.execute("ROLLBACK")
        raise


# --------------------------------------------------------------------------
# Fetch orchestration
# --------------------------------------------------------------------------
def fetch_spot(con, bc, start: date, end: date) -> int:
    """Fetch 1-minute NIFTY index candles for [start, end]."""
    total = 0
    for d1, d2 in chunk_dates(start, end, BREEZE_1MIN_SOFT_DAYS):
        resp = breeze_call(
            bc, "get_historical_data",
            interval="1minute",
            from_date=f"{d1}T06:00:00.000Z",
            to_date=f"{d2}T10:00:00.000Z",
            stock_code=STOCK_CODE,
            exchange_code=EXCHANGE_INDEX,
            product_type="index",
        )
        rows = extract_rows(resp)
        n = insert_spot(con, rows)
        total += n
        log.info("Spot %s..%s: %d fetched, %d inserted", d1, d2, len(rows), n)
        time.sleep(REQUEST_GAP)
    return total


def fetch_options(con, bc, start: date, end: date,
                  expiries: List[date], strikes: List[int]) -> int:
    """Fetch 1-minute data for each (expiry, strike, CE/PE) combination."""
    total = 0
    for expiry in expiries:
        for strike in strikes:
            for right, label in (("call", "CE"), ("put", "PE")):
                for d1, d2 in chunk_dates(start, end, BREEZE_MAX_DAYS_PER_CALL):
                    resp = breeze_call(
                        bc, "get_historical_data",
                        interval="1minute",
                        from_date=f"{d1}T06:00:00.000Z",
                        to_date=f"{d2}T10:00:00.000Z",
                        stock_code=STOCK_CODE,
                        exchange_code=EXCHANGE_DERIV,
                        product_type="options",
                        expiry_date=expiry.strftime("%d-%b-%Y"),
                        right=right,
                        strike_price=str(strike),
                    )
                    rows = extract_rows(resp)
                    n = insert_options(con, rows, expiry)
                    total += n
                    log.info("Options %s %s %s %s..%s: %d fetched, %d inserted",
                             expiry, strike, label, d1, d2, len(rows), n)
                    time.sleep(REQUEST_GAP)
    return total


def next_expiries(con, asof: date, limit: int = 3) -> List[date]:
    """Upcoming NIFTY expiries from expiry_calendar (>= asof)."""
    return [r[0] for r in con.execute(
        "SELECT expiry_date FROM expiry_calendar "
        "WHERE expiry_date >= ? ORDER BY expiry_date LIMIT ?",
        [asof, limit],
    ).fetchall()]


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------
def run_daily_fetch(days_back: int = 5, strikes: Optional[List[int]] = None,
                    expiries: Optional[List[date]] = None) -> Dict[str, int]:
    """Incremental catch-up fetch -- safe to schedule daily.

    Spot is always fetched for [today - days_back, today]. Options are fetched
    only when `strikes` is provided (Breeze requires a specific strike per
    call); expiries default to the next 3 from expiry_calendar.
    """
    end = date.today()
    start = end - timedelta(days=days_back)
    summary: Dict[str, int] = {"spot_inserted": 0, "options_inserted": 0}
    con = duckdb.connect(DB_PATH)
    try:
        ensure_tables(con)
        bc = build_session()
        summary["spot_inserted"] = fetch_spot(con, bc, start, end)
        if strikes:
            exps = expiries or next_expiries(con, start)
            summary["options_inserted"] = fetch_options(con, bc, start, end, exps, strikes)
    finally:
        con.close()
    log.info("run_daily_fetch summary: %s", summary)
    return summary


def main() -> None:
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

    parser = argparse.ArgumentParser(description="Breeze -> DuckDB NIFTY fetcher")
    parser.add_argument("--from", dest="from_", type=str, default=None,
                        help="Start date YYYY-MM-DD (default: today - days-back)")
    parser.add_argument("--to", dest="to_", type=str, default=None,
                        help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--days-back", type=int, default=5,
                        help="Days to look back when --from is not given")
    parser.add_argument("--expiries", type=str, default=None,
                        help="Comma-separated expiries YYYY-MM-DD (default: next 3 from calendar)")
    parser.add_argument("--strikes", type=str, default=None,
                        help="Comma-separated strikes for options fetch, e.g. 24000,24100")
    parser.add_argument("--skip-options", action="store_true",
                        help="Fetch spot only")
    args = parser.parse_args()

    end = date.fromisoformat(args.to_) if args.to_ else date.today()
    start = date.fromisoformat(args.from_) if args.from_ else end - timedelta(days=args.days_back)

    con = duckdb.connect(DB_PATH)
    summary: Dict[str, int] = {}
    try:
        ensure_tables(con)
        bc = build_session()
        summary["spot_inserted"] = fetch_spot(con, bc, start, end)
        if not args.skip_options:
            strikes = [int(s) for s in args.strikes.split(",")] if args.strikes else []
            if strikes:
                exps = ([date.fromisoformat(e) for e in args.expiries.split(",")]
                        if args.expiries else next_expiries(con, start))
                summary["options_inserted"] = fetch_options(con, bc, start, end, exps, strikes)
    finally:
        con.close()
    log.info("Summary: %s", summary)


if __name__ == "__main__":
    main()
