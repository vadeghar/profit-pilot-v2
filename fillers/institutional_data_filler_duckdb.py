#!/usr/bin/env python3
"""
institutional_data_filler_duckdb.py

Fetches FII/DII institutional data (cash + derivatives) from StockMojo
and stores it in the `institutional_data` table in DuckDB.

Aggregation per category (FII / DII):
    total_buy  = cash_buy  + derivative_buy
    total_sell = cash_sell + derivative_sell
    net        = total_buy - total_sell

Source API (POST, JSON body, no auth):
    https://user.stockmojo.in/v1/fiidii/summary
    body: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}

NOTE: the source API caps each request at ~41 calendar days.
      This script chunks the requested range into 40-day windows.

Two rows are written per trading date: one FII, one DII.

Override semantics:
    Re-running replaces (DELETEs then INSERTs) every row whose trade_date
    falls inside the requested range, so the range is always idempotent.

Usage:
    python institutional_data_filler_duckdb.py --execute --from-date 2026-01-01 --to-date 2026-09-09
    python institutional_data_filler_duckdb.py --execute            # default range
    python institutional_data_filler_duckdb.py                     # dry-run
"""

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import os
import duckdb
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
DB_PATH = os.path.join(BASE_DIR, "market_data.duckdb")
TABLE_NAME = "institutional_data"
API_URL = "https://user.stockmojo.in/v1/fiidii/summary"

# The source API rejects windows longer than ~41 calendar days (returns
# success:false with an empty payload). Stay safely under that ceiling.
CHUNK_DAYS = 40
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0
RATE_LIMIT_SEC = 1.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("institutional_data")

# The StockMojo endpoint expects these browser-like headers but no auth token.
DEFAULT_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9,te;q=0.8",
    "content-type": "application/json",
    "dnt": "1",
    "origin": "https://stockmojo.in",
    "priority": "u=1, i",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0.0.0 Safari/537.36"
    ),
}

DDL = """
CREATE TABLE IF NOT EXISTS institutional_data (
    trade_date      DATE,
    category        VARCHAR,
    cash_buy        DOUBLE,
    cash_sell       DOUBLE,
    derivative_buy  DOUBLE,
    derivative_sell DOUBLE,
    total_buy       DOUBLE,
    total_sell      DOUBLE,
    net             DOUBLE
)
"""

# --------------------------------------------------------------------------- #
# API layer
# --------------------------------------------------------------------------- #

def _to_float(v: Any) -> float:
    """Safely coerce a (possibly string-ish) API value to float; None -> 0.0."""
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _aggregate_derivatives(fii_deriv: Dict[str, Any]) -> Tuple[float, float]:
    """Sum every buy / sell amount inside the fiiDerivatives block."""
    buy_keys = (
        "idx_fut_buy_amt",
        "idx_opt_buy_amt",
        "stk_fut_buy_amt",
        "stk_opt_buy_amt",
    )
    sell_keys = (
        "idx_fut_sell_amt",
        "idx_opt_sell_amt",
        "stk_fut_sell_amt",
        "stk_opt_sell_amt",
    )
    buy = sum(_to_float(fii_deriv.get(k)) for k in buy_keys)
    sell = sum(_to_float(fii_deriv.get(k)) for k in sell_keys)
    return buy, sell


def _date_to_rows(rec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Expand one API record into FII + DII rows with aggregated totals."""
    trade_date = rec["date"]
    cash = rec.get("fiiDiiCash") or {}
    fii_deriv = rec.get("fiiDerivatives") or {}
    deriv_buy, deriv_sell = _aggregate_derivatives(fii_deriv)

    rows: List[Dict[str, Any]] = []

    # FII row: cash + derivatives
    fii_cash_buy = _to_float(cash.get("fii_buy"))
    fii_cash_sell = _to_float(cash.get("fii_sell"))
    fii_total_buy = fii_cash_buy + deriv_buy
    fii_total_sell = fii_cash_sell + deriv_sell
    rows.append({
        "trade_date": trade_date,
        "category": "FII",
        "cash_buy": fii_cash_buy,
        "cash_sell": fii_cash_sell,
        "derivative_buy": deriv_buy,
        "derivative_sell": deriv_sell,
        "total_buy": fii_total_buy,
        "total_sell": fii_total_sell,
        "net": fii_total_buy - fii_total_sell,
    })

    # DII row: cash only (source does not break out DII derivatives)
    dii_cash_buy = _to_float(cash.get("dii_buy"))
    dii_cash_sell = _to_float(cash.get("dii_sell"))
    rows.append({
        "trade_date": trade_date,
        "category": "DII",
        "cash_buy": dii_cash_buy,
        "cash_sell": dii_cash_sell,
        "derivative_buy": None,
        "derivative_sell": None,
        "total_buy": dii_cash_buy,
        "total_sell": dii_cash_sell,
        "net": dii_cash_buy - dii_cash_sell,
    })

    return rows


def fetch_chunk(
    session: requests.Session, start: date, end: date
) -> List[Dict[str, Any]]:
    """Fetch and flatten one date-window from the StockMojo API (with retries)."""
    body = {"start": start.isoformat(), "end": end.isoformat()}
    last_exc: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.post(
                API_URL, json=body, headers=DEFAULT_HEADERS, timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            payload = resp.json()

            if not payload.get("success"):
                raise RuntimeError(
                    f"API success=false for {start}..{end}: {json.dumps(payload)[:200]}"
                )

            data = payload.get("data") or []
            rows: List[Dict[str, Any]] = []
            for rec in data:
                rows.extend(_date_to_rows(rec))
            return rows

        except Exception as exc:
            last_exc = exc
            if attempt == MAX_RETRIES:
                break
            wait = RETRY_BACKOFF ** attempt
            log.warning(
                "chunk %s..%s attempt %d/%d failed: %s (sleep %.1fs)",
                start, end, attempt, MAX_RETRIES, exc, wait,
            )
            time.sleep(wait)

    raise RuntimeError(
        f"chunk {start}..{end} failed after {MAX_RETRIES} attempts: {last_exc}"
    )


# --------------------------------------------------------------------------- #
# Range chunking
# --------------------------------------------------------------------------- #

def date_chunks(start: date, end: date, window: int) -> List[Tuple[date, date]]:
    """Split [start, end] into consecutive windows of `window` days."""
    chunks: List[Tuple[date, date]] = []
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=window - 1), end)
        chunks.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    return chunks


# --------------------------------------------------------------------------- #
# DuckDB write (override = DELETE existing range + INSERT)
# --------------------------------------------------------------------------- #

def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    # Always start from a clean table so the schema matches the current
    # aggregation (cash buy/sell + derivative buy/sell + totals). The source
    # data is re-fetched in full on every run, so dropping is safe.
    con.execute("DROP TABLE IF EXISTS institutional_data")
    con.execute(DDL)


def write_rows(
    con: duckdb.DuckDBPyConnection,
    rows: List[Dict[str, Any]],
    start: date,
    end: date,
) -> int:
    """Override (DELETE + INSERT) every row in [start, end]; returns rows inserted."""
    ensure_table(con)

    if not rows:
        con.execute(
            "DELETE FROM institutional_data WHERE trade_date BETWEEN ? AND ?",
            [start, end],
        )
        return 0

    col_order = (
        "trade_date", "category", "cash_buy", "cash_sell",
        "derivative_buy", "derivative_sell", "total_buy", "total_sell", "net",
    )

    vals = []
    for r in rows:
        parts = []
        for c in col_order:
            v = r[c]
            if v is None:
                parts.append("NULL")
            elif c in ("trade_date", "category"):
                parts.append(f"'{v}'")
            else:
                parts.append(str(float(v)))
        vals.append("(" + ",".join(parts) + ")")
    values_sql = ",".join(vals)

    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            "DELETE FROM institutional_data WHERE trade_date BETWEEN ? AND ?",
            [start, end],
        )
        con.execute(
            f"INSERT INTO institutional_data ({','.join(col_order)}) "
            f"SELECT {','.join(col_order)} FROM (VALUES {values_sql}) "
            f"AS t({','.join(col_order)})"
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    return len(rows)


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #

def print_summary(
    con: duckdb.DuckDBPyConnection, start: date, end: date
) -> None:
    stats = con.execute(
        """
        SELECT
            COUNT(*)                   AS rows,
            COUNT(DISTINCT trade_date) AS dates,
            MIN(trade_date)            AS lo,
            MAX(trade_date)            AS hi,
            COUNT(DISTINCT category)   AS categories
        FROM institutional_data
        WHERE trade_date BETWEEN ? AND ?
        """,
        [start, end],
    ).fetchone()

    log.info(
        "stored: %s rows | %s dates | %s .. %s | categories=%s",
        stats[0], stats[1], stats[2], stats[3], stats[4],
    )

    for row in con.execute(
        """
        SELECT category,
               COUNT(*)                AS dates,
               ROUND(SUM(total_buy), 2)  AS total_buy,
               ROUND(SUM(total_sell), 2) AS total_sell,
               ROUND(SUM(net), 2)        AS net
        FROM institutional_data
        WHERE trade_date BETWEEN ? AND ?
        GROUP BY category
        ORDER BY category
        """,
        [start, end],
    ).fetchall():
        log.info(
            "  %-4s  dates=%-3d  total_buy=%14.2f  total_sell=%14.2f  net=%12.2f",
            row[0], row[1], row[2], row[3], row[4],
        )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Fetch FII/DII institutional data from StockMojo and store it "
            "in the `institutional_data` DuckDB table."
        )
    )
    p.add_argument(
        "--from-date",
        type=date.fromisoformat,
        default=date(2026, 1, 1),
        help="Start date (YYYY-MM-DD). Default: 2026-01-01",
    )
    p.add_argument(
        "--to-date",
        type=date.fromisoformat,
        default=date.today(),
        help="End date (YYYY-MM-DD). Default: today",
    )
    p.add_argument(
        "--db", default=DB_PATH, help=f"Path to DuckDB file (default: {DB_PATH})"
    )
    p.add_argument(
        "--execute",
        action="store_true",
        help="Actually fetch + write. Default is dry-run.",
    )
    p.add_argument(
        "--chunk-days",
        type=int,
        default=CHUNK_DAYS,
        help=f"API window size in days (source caps ~41; default: {CHUNK_DAYS})",
    )
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    end = min(args.to_date, date.today())
    if args.from_date > end:
        log.error(
            "from-date %s is after to-date %s — nothing to do.",
            args.from_date, end,
        )
        return 1

    chunks = date_chunks(args.from_date, end, args.chunk_days)
    log.info(
        "range=%s -> %s  mode=%s  chunks=%d (window<=%dd)",
        args.from_date, end,
        "EXECUTE" if args.execute else "DRY-RUN",
        len(chunks), args.chunk_days,
    )

    if not args.execute:
        log.info(
            "DRY-RUN: would call the API %d time(s) and upsert ~%d rows.",
            len(chunks),
            len(chunks) * args.chunk_days * 2,
        )
        return 0

    # ---- fetch ----------------------------------------------------------- #
    session = requests.Session()
    all_rows: List[Dict[str, Any]] = []
    for i, (c_start, c_end) in enumerate(chunks, 1):
        rows = fetch_chunk(session, c_start, c_end)
        all_rows.extend(rows)
        log.info(
            "chunk %d/%d  %s..%s  -> %d rows",
            i, len(chunks), c_start, c_end, len(rows),
        )
        if i < len(chunks):
            time.sleep(RATE_LIMIT_SEC)

    if not all_rows:
        log.warning("no rows returned by the API for the whole range.")

    # ---- write ----------------------------------------------------------- #
    con = duckdb.connect(args.db)
    try:
        inserted = write_rows(con, all_rows, args.from_date, end)
    finally:
        con.close()

    log.info("done: %d rows written to %s.%s", inserted, args.db, TABLE_NAME)
    print_summary(duckdb.connect(args.db), args.from_date, end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
