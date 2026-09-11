#!/usr/bin/env python3
"""
nifty_option_filler_duckdb.py
-----------------------------
Fill NIFTY index OPTION (CE/PE) 1-minute candles into the existing DuckDB
`options_ticks` table.

Active contracts are identified from DuckDB itself: every distinct
(expiry_date, strike_price, option_type) CE/PE combination whose expiry falls
on/after the requested --from-date is "active" for this run. Providers:

    Angel One (primary): symbol + token resolved from the OpenAPI scrip master
                (exch_seg=NFO, name=NIFTY, instrumenttype=OPTIDX), then
                getCandleData(exchange=NFO, symboltoken=token, ONE_MINUTE).
    Breeze (fallback): identified by parameters, no symbol/token needed
                (stock_code=NIFTY, exchange_code=NFO, product_type=options,
                 expiry_date=YYYY-MM-DDT07:00:00.000Z, right=call/put,
                 strike_price=<int>).

--broker auto (default): Angel first with incremental retries; anything still
missing afterwards is queued for the Breeze fallback (which also covers
EXPIRED contracts absent from the Angel scrip master). Fallback applies ONLY
to missing candle data - candles already in DuckDB are never re-fetched or
overwritten (INSERT ... ANTI JOIN on contract identity + trade_time).

Table safety:
    - Columns are never altered except the additive ensure_columns() guard
      which adds optional open/high/low DOUBLE columns if missing.
    - `close` stores the candle CLOSE; open/high/low are stored too when the
      provider returns them. Historical candle endpoints usually do not return
      greeks; optional IV/greek fields are preserved when Breeze provides them.

Session convention (NIFTY F&O, matches existing data):
    09:15-15:29 (375 candles) before 2026-08-03; 09:15-15:39 (385) after.

python nifty_option_filler_duckdb.py --execute --from-date 2026-09-09 --to-date 2026-09-09
# or daily default (latest date -> today)
python nifty_option_filler_duckdb.py --execute
# optional refinements still work
--expiry 2026-09-15   --max-contracts N   --broker auto|angel|breeze   --include-partial
`

"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import sys
import threading
import time
from collections import defaultdict
from datetime import date, datetime, time as dtime, timedelta

import duckdb
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
DUCK_PATH = os.path.join(BASE_DIR, "market_data.duckdb")
TABLE = "options_ticks"
IST = "Asia/Kolkata"
FNO_START = dtime(9, 15)
SESSION_CHANGE_DATE = date(2026, 8, 3)
SESSION_LAST_PRE = dtime(15, 29)     # last candle before 2026-08-03 (375/day)
SESSION_LAST_POST = dtime(15, 39)    # last candle on/after 2026-08-03 (385/day)

SCRIP_MASTER_URL = ("https://margincalculator.angelbroking.com/"
                    "OpenAPI_File/files/OpenAPIScripMaster.json")
ANGEL_SLEEP_SECONDS = 0.6
BREEZE_SLEEP_SECONDS = 0.7
ANGEL_RETRIES = 3
BREEZE_RETRIES = 3

# ---- ATM strike cap (NIFTY options) ---------------------------------
# At each snapshot time, read the NIFTY spot close and fetch CE & PE for the
# strikes within ATM +/- ATM_RANGE (step ATM_STRIKE_STEP). This caps the
# number of contracts per day instead of grabbing the full ~1.6k chain.
ATM_RANGE = 800
ATM_STRIKE_STEP = 50
SNAPSHOT_TIMES = (dtime(9, 30), dtime(11, 30), dtime(13, 30))
SPOT_SEARCH_WINDOW_MIN = 30   # if the exact minute is missing, scan forward this far

log = logging.getLogger("nifty_opt_filler")


def timeout_call(fn, args=(), kwargs=None, timeout=90, label="API"):
    """Run fn in a worker thread with a hard timeout (providers can hang)."""
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


def session_last_candle(d: date) -> dtime:
    return SESSION_LAST_POST if d >= SESSION_CHANGE_DATE else SESSION_LAST_PRE


def expected_minute_count(d: date) -> int:
    return 385 if d >= SESSION_CHANGE_DATE else 375


def contract_label(c: dict) -> str:
    return f"{c['expiry_date']} {c['strike_price']}{c['option_type']}"


# --------------------------------------------------------------------------- #
# ATM strike selection (NIFTY spot based)
# --------------------------------------------------------------------------- #
def nifty_spot_close_near(con: duckdb.DuckDBPyConnection, d: date,
                          t: dtime) -> tuple[dtime | None, float | None]:
    """NIFTY spot close at time t (or the next available minute)."""
    base = datetime.combine(d, t)
    for off in range(0, SPOT_SEARCH_WINDOW_MIN + 1):
        ts = base + timedelta(minutes=off)
        row = con.execute("SELECT close FROM nifty_spot WHERE trade_time = ?",
                          [ts]).fetchone()
        if row and row[0] is not None:
            return ts.time(), float(row[0])
    return None, None


def atm_strike_bucket(close: float) -> int:
    """Round a spot close to the nearest strike bucket (ATM)."""
    return int(round(close / ATM_STRIKE_STEP) * ATM_STRIKE_STEP)


def at_money_strikes(close: float) -> list[int]:
    """Strikes in [ATM-ATM_RANGE, ATM+ATM_RANGE] step 50."""
    atm = atm_strike_bucket(close)
    lo = max(0, atm - ATM_RANGE)
    hi = atm + ATM_RANGE
    return [s for s in range(lo, hi + 1, ATM_STRIKE_STEP)], atm


def build_atm_contracts(con: duckdb.DuckDBPyConnection, dates: list[date],
                        master: dict[tuple, dict]) -> tuple[list[dict], list[str]]:
    """For each trading date, use the 3 snapshot NIFTY-spot closes to derive
    the ATM +/- range strikes' contracts (CE & PE) on the nearest active
    expiry. Returns (contracts, notes) where notes logs any missing spot minute.
    """
    expiries = sorted({e for (e, _s, _o) in master})
    if not expiries:
        return [], ["No expiries in scrip master to build ATM contracts from."]

    merged: dict[tuple, dict] = {}
    notes: list[str] = []
    for d in dates:
        active_expiry = next((e for e in expiries if e > d), None)
        if active_expiry is None:
            notes.append(f"{d}: no active expiry after trading date - skipped")
            continue
        for t in SNAPSHOT_TIMES:
            actual_t, close = nifty_spot_close_near(con, d, t)
            if close is None:
                notes.append(f"{d} {t}: nifty_spot missing within "
                             f"{SPOT_SEARCH_WINDOW_MIN} min - snapshot skipped")
                continue
            if actual_t != t:
                notes.append(f"{d} {t}: exact minute missing, used nifty_spot "
                             f"{actual_t} (close={close})")
            strikes, atm = at_money_strikes(close)
            log.debug("%s %s: ATM=%d range=%d..%d strikes=%d",
                      d, t, atm, strikes[0], strikes[-1], len(strikes))
            for strike in strikes:
                for otype in ("CE", "PE"):
                    meta = master.get((active_expiry, strike, otype))
                    if meta:
                        merged[(active_expiry, strike, otype)] = {
                            "expiry_date": active_expiry,
                            "strike_price": strike,
                            "option_type": otype,
                            "token": meta["token"],
                            "symbol": meta["symbol"],
                        }
    contracts = list(merged.values())
    contracts.sort(key=lambda c: (c["expiry_date"], c["strike_price"],
                                  c["option_type"]))
    return contracts, notes


# --------------------------------------------------------------------------- #
# DuckDB: active contracts, per contract-day plan, insertion
# --------------------------------------------------------------------------- #
COLUMNS = ("trade_time", "trade_date", "expiry_date", "strike_price",
           "option_type", "close", "volume", "open_interest",
           "iv", "delta", "gamma", "theta", "vega",
           "open", "high", "low")


def ensure_columns(con: duckdb.DuckDBPyConnection) -> None:
    """Ensure the optional OHLC columns exist (idempotent, additive only)."""
    for col in ("open", "high", "low"):
        con.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {col} DOUBLE")


def active_contracts(con: duckdb.DuckDBPyConnection, from_date: date,
                     expiries: set[date] | None) -> list[dict]:
    """Distinct CE/PE contracts already tracked in options_ticks, alive in range."""
    sql = f"""
        SELECT DISTINCT expiry_date, strike_price, option_type
        FROM {TABLE}
        WHERE option_type IN ('CE', 'PE')
          AND expiry_date >= ?
    """
    params: list = [from_date]
    if expiries:
        sql += " AND expiry_date IN ({})".format(", ".join("?" for _ in expiries))
        params.extend(sorted(expiries))
    sql += " ORDER BY expiry_date, strike_price, option_type"
    rows = con.execute(sql, params).fetchall()
    return [{"expiry_date": r[0], "strike_price": int(r[1]),
             "option_type": r[2]} for r in rows]


def discover_contracts(con: duckdb.DuckDBPyConnection, from_date: date,
                       expiries: set[date] | None) -> list[dict]:
    """Merge table-tracked contracts with the currently-active chain from the
    Angel scrip master.

    This lets new/upcoming expiries (never ingested into options_ticks, so
    absent from the table) be filled for the requested trading date. Contracts
    discovered from the scrip master carry a token; table-only contracts do not
    (they fall back to Breeze).
    """
    merged: dict[tuple, dict] = {}
    for c in active_contracts(con, from_date, expiries):
        merged[(c["expiry_date"], c["strike_price"], c["option_type"])] = c

    try:
        master = angel_option_master()
    except Exception as e:  # noqa: BLE001
        log.warning("Could not load Angel scrip master (%s); using table contracts only.", e)
        master = {}

    for (expiry, strike, otype), meta in master.items():
        if expiries and expiry not in expiries:
            continue
        if expiry < from_date:
            continue
        merged[(expiry, strike, otype)] = {
            "expiry_date": expiry, "strike_price": strike,
            "option_type": otype, "token": meta["token"], "symbol": meta["symbol"],
        }

    contracts = list(merged.values())
    contracts.sort(key=lambda c: (c["expiry_date"], c["strike_price"], c["option_type"]))
    tracked = sum(1 for c in contracts if "token" not in c)
    discovered = len(contracts) - tracked
    log.info("discovered contracts: %d from scrip master / %d table-tracked = %d total",
             discovered, tracked, len(contracts))
    return contracts


def plan_contract_days(con: duckdb.DuckDBPyConnection,
                       contracts: list[dict], dates: list[date],
                       include_partial: bool) -> tuple[list[dict], dict]:
    """Classify each (contract, date): complete / fetch / fetch-partial / skip."""
    todo: list[dict] = []
    stats: dict[str, int] = defaultdict(int)
    for d in dates:
        expected = expected_minute_count(d)
        for c in contracts:
            # A contract is only tradable on/ before its expiry day.
            if d > c["expiry_date"]:
                stats["expired"] += 1
                continue
            have = con.execute(
                f"""SELECT COUNT(DISTINCT trade_time) FROM {TABLE}
                    WHERE trade_date = ? AND expiry_date = ?
                      AND strike_price = ? AND option_type = ?""",
                [d, c["expiry_date"], c["strike_price"], c["option_type"]],
            ).fetchone()[0]
            if have >= expected:
                stats["complete"] += 1
                continue
            item = {"date": d, "contract": c, "have": have, "expected": expected}
            if have == 0:
                stats["fetch"] += 1
                item["action"] = "fetch"
            elif include_partial:
                stats["fetch-partial"] += 1
                item["action"] = "fetch-partial"
            else:
                stats["partial-skip"] += 1
                item["action"] = "partial-skip"
            if item["action"].startswith("fetch"):
                todo.append(item)
    return todo, dict(stats)


def insert_candles(con: duckdb.DuckDBPyConnection, rows: list[dict],
                   d: date, c: dict) -> int:
    """Insert one contract-day, skipping timestamps already stored (no override)."""
    if not rows:
        return 0
    df = pd.DataFrame(rows)
    df["trade_date"] = d
    df["expiry_date"] = c["expiry_date"]
    df["strike_price"] = c["strike_price"]
    df["option_type"] = c["option_type"]
    for g in ("iv", "delta", "gamma", "theta", "vega"):
        if g not in df:
            df[g] = None
    df["volume"] = df["volume"].astype("Int64")
    df["open_interest"] = df["open_interest"].astype("Int64")
    df = df[list(COLUMNS)]

    col_list = ", ".join(COLUMNS)
    con.register("opt_incoming", df)
    try:
        before = con.execute(
            f"""SELECT COUNT(*) FROM {TABLE}
                WHERE trade_date = ? AND expiry_date = ?
                  AND strike_price = ? AND option_type = ?""",
            [d, c["expiry_date"], c["strike_price"], c["option_type"]],
        ).fetchone()[0]
        con.execute(
            f"""
            INSERT INTO {TABLE} ({col_list})
            SELECT {col_list} FROM opt_incoming i
            ANTI JOIN {TABLE} t
              ON  t.trade_time    =  i.trade_time
              AND t.expiry_date   =  i.expiry_date
              AND t.strike_price  =  i.strike_price
              AND t.option_type   =  i.option_type
            """
        )
        after = con.execute(
            f"""SELECT COUNT(*) FROM {TABLE}
                WHERE trade_date = ? AND expiry_date = ?
                  AND strike_price = ? AND option_type = ?""",
            [d, c["expiry_date"], c["strike_price"], c["option_type"]],
        ).fetchone()[0]
        return after - before
    finally:
        con.unregister("opt_incoming")


# --------------------------------------------------------------------------- #
# Angel One (primary)
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
    if any(not os.getenv(x) for x in required):
        raise RuntimeError("Missing Angel env vars: " + ", ".join(required))
    client = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
    totp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now()
    resp = timeout_call(client.generateSession,
                        args=(os.environ["ANGEL_CLIENT_CODE"],
                              os.environ["ANGEL_PASSWORD_OR_MPIN"], totp),
                        timeout=30, label="Angel login")
    if not resp or not resp.get("status", True):
        raise RuntimeError(f"SmartAPI login failed: {resp}")
    log.info("SmartAPI login successful")
    return client


def angel_option_master() -> dict[tuple, dict]:
    """Map (expiry, strike, CE/PE) -> {symbol, token} from the scrip master.

    Only currently LISTED contracts appear here; expired contracts are served
    by the Breeze fallback instead.
    """
    import requests

    log.info("Loading Angel OpenAPI scrip master...")
    r = requests.get(SCRIP_MASTER_URL, timeout=60)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    opts = df[(df["exch_seg"].astype(str).str.upper() == "NFO")
              & (df["name"].astype(str).str.upper() == "NIFTY")
              & (df["instrumenttype"].astype(str).str.upper() == "OPTIDX")].copy()
    log.info("Scrip master: %d NIFTY OPTIDX contracts listed", len(opts))
    out: dict[tuple, dict] = {}
    for row in opts.itertuples(index=False):
        try:
            exp = pd.to_datetime(row.expiry, errors="coerce")
            if pd.isna(exp):
                continue
            # Angel scrip master stores strike in paise (×100).
            strike = int(round(float(row.strike) / 100.0))
            key = (exp.date(), strike,
                   str(row.symbol).strip()[-2:].upper())
        except Exception:
            continue
        if key[2] in ("CE", "PE"):
            out[key] = {"symbol": str(row.symbol), "token": str(row.token)}
    log.info("Scrip master: %d usable (expiry, strike, type) keys", len(out))
    return out


def angel_fetch_day(client, token: str, d: date) -> list[dict]:
    """Fetch one contract-day via SmartAPI; normalized rows with OHLC."""
    last = session_last_candle(d)
    params = {
        "exchange": "NFO",
        "symboltoken": token,
        "interval": "ONE_MINUTE",
        "fromdate": f"{d.isoformat()} 09:15",
        "todate": f"{d.isoformat()} {last.strftime('%H:%M')}",
    }
    resp = timeout_call(client.getCandleData, args=(params,),
                        timeout=60, label=f"Angel {d} {token}")
    if not isinstance(resp, dict):
        raise RuntimeError(f"SmartAPI unexpected response: {resp!r}")
    if not resp.get("status"):
        return []  # no data for that day
    s = pd.Timestamp(datetime.combine(d, FNO_START))
    e = pd.Timestamp(datetime.combine(d, last))
    out: dict[pd.Timestamp, dict] = {}
    for row in resp.get("data") or []:
        # SmartAPI layout: [datetime, open, high, low, close, volume(, oi)]
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            ts = pd.Timestamp(row[0])
            ts = ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)
        except Exception:
            continue
        ts = ts.tz_convert(IST).tz_localize(None)
        if ts < s or ts > e or ts.second or ts.microsecond:
            continue
        o, h, l, c = row[1], row[2], row[3], row[4]
        if any(x is None for x in (o, h, l, c)):
            continue
        oi = row[6] if len(row) > 6 else None
        out[ts] = {
            "trade_time": ts.to_pydatetime(),
            "open": float(o), "high": float(h), "low": float(l),
            "close": float(c),  # candle close
            "volume": int(float(row[5])) if row[5] not in (None, "") else None,
            "open_interest": int(float(oi)) if oi not in (None, "") else None,
        }
    return list(out.values())


def fetch_angel_with_retry(client, token: str, d: date) -> list[dict]:
    last_exc = None
    for attempt in range(1, ANGEL_RETRIES + 1):
        try:
            return angel_fetch_day(client, token, d)
        except Exception as e:  # noqa: BLE001
            last_exc = e
            wait = attempt  # incremental: 1s, 2s, 3s
            log.warning("Angel fetch failed %s (attempt %d/%d): %s - retry in %ds",
                        d, attempt, ANGEL_RETRIES, e, wait)
            time.sleep(wait)
    raise RuntimeError(f"Angel failed after {ANGEL_RETRIES} attempts") from last_exc


# --------------------------------------------------------------------------- #
# Breeze (fallback) - options identified by parameters, no symbol/token needed
# --------------------------------------------------------------------------- #
def breeze_login():
    from breeze_connect import BreezeConnect

    required = ["BREEZE_API_KEY", "BREEZE_API_SECRET", "BREEZE_SESSION_TOKEN"]
    if any(not os.getenv(x) for x in required):
        raise RuntimeError("Missing Breeze env vars: " + ", ".join(required))
    client = BreezeConnect(api_key=os.environ["BREEZE_API_KEY"])
    client.generate_session(api_secret=os.environ["BREEZE_API_SECRET"],
                            session_token=os.environ["BREEZE_SESSION_TOKEN"])
    log.info("Breeze login successful")
    return client


def breeze_fetch_day(client, d: date, c: dict) -> list[dict]:
    """Fetch one contract-day from Breeze options history; OHLC normalized."""
    last = session_last_candle(d)
    params = {
        "interval": "1minute",
        "from_date": d.strftime("%Y-%m-%dT09:15:00.000Z"),
        "to_date": d.strftime("%Y-%m-%dT")
                   + dtime(last.hour, last.minute + 1).strftime("%H:%M:%S") + ".000Z",
        "stock_code": "NIFTY",
        "exchange_code": "NFO",
        "product_type": "options",
        "expiry_date": c["expiry_date"].strftime("%Y-%m-%dT07:00:00.000Z"),
        "right": "call" if c["option_type"] == "CE" else "put",
        "strike_price": str(c["strike_price"]),
    }
    resp = timeout_call(client.get_historical_data_v2, kwargs=params,
                        timeout=90, label=f"Breeze {d} {contract_label(c)}")
    if not isinstance(resp, dict) or resp.get("Status") != 200:
        raise RuntimeError(f"Breeze response: {resp}")
    s = pd.Timestamp(datetime.combine(d, FNO_START))
    e = pd.Timestamp(datetime.combine(d, last))
    out: dict[pd.Timestamp, dict] = {}
    for item in resp.get("Success") or []:
        if not isinstance(item, dict):
            continue
        try:
            ts = pd.Timestamp(item.get("datetime"))
            ts = ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)
        except Exception:
            continue
        ts = ts.tz_convert(IST).tz_localize(None)
        if ts < s or ts > e or ts.second or ts.microsecond:
            continue
        close = item.get("close")
        if close in (None, ""):
            continue
        vol, oi = item.get("volume"), item.get("open_interest")
        o, h, l = item.get("open"), item.get("high"), item.get("low")
        out[ts] = {
            "trade_time": ts.to_pydatetime(),
            "open": float(o) if o not in (None, "") else None,
            "high": float(h) if h not in (None, "") else None,
            "low": float(l) if l not in (None, "") else None,
            "close": float(close),
            "volume": int(float(vol)) if vol not in (None, "") else None,
            "open_interest": int(float(oi)) if oi not in (None, "") else None,
        }
        for key in ("iv", "delta", "gamma", "theta", "vega"):
            value = item.get(key)
            if key == "iv" and value in (None, ""):
                value = item.get("implied_volatility")
            if value not in (None, ""):
                out[ts][key] = float(value)
    return list(out.values())


def fetch_breeze_with_retry(client, d: date, c: dict) -> list[dict]:
    last_exc = None
    for attempt in range(1, BREEZE_RETRIES + 1):
        try:
            return breeze_fetch_day(client, d, c)
        except Exception as e:  # noqa: BLE001
            last_exc = e
            wait = attempt  # incremental: 1s, 2s, 3s
            log.warning("Breeze fetch failed %s %s (attempt %d/%d): %s - retry in %ds",
                        d, contract_label(c), attempt, BREEZE_RETRIES, e, wait)
            time.sleep(wait)
    raise RuntimeError(f"Breeze failed after {BREEZE_RETRIES} attempts") from last_exc


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_phase(phase_name: str, fetch_fn, todo: list[dict], con, sleep_s: float):
    """Run one provider phase; returns (rows_inserted, items_for_fallback)."""
    inserted = 0
    fallback: list[dict] = []
    for i, item in enumerate(todo, 1):
        c, d = item["contract"], item["date"]
        try:
            rows = fetch_fn(item)
            n = insert_candles(con, rows, d, c)
            inserted += n
            now = con.execute(
                f"""SELECT COUNT(DISTINCT trade_time) FROM {TABLE}
                    WHERE trade_date = ? AND expiry_date = ?
                      AND strike_price = ? AND option_type = ?""",
                [d, c["expiry_date"], c["strike_price"], c["option_type"]],
            ).fetchone()[0]
            status = "OK" if now >= item["expected"] else "PARTIAL"
            log.info("[%s %d/%d] %s %s: rows=%d inserted=%d coverage=%d/%d %s",
                     phase_name, i, len(todo), d, contract_label(c),
                     len(rows), n, now, item["expected"], status)
            if now < item["expected"]:
                # Still incomplete after this provider -> route to fallback,
                # tracking why so the final summary can explain it.
                fallback.append({**item, "phase": phase_name,
                                 "provider_rows": len(rows),
                                 "reason": "provider_no_data"
                                 if not rows else "incomplete"})
        except Exception as e:  # noqa: BLE001
            log.warning("[%s %d/%d] %s %s: FAILED %s: %s",
                        phase_name, i, len(todo), d, contract_label(c),
                        type(e).__name__, e)
            fallback.append({**item, "phase": phase_name,
                             "provider_rows": 0,
                             "reason": f"error:{type(e).__name__}:{e}"})
        time.sleep(sleep_s)
    return inserted, fallback


def print_gap_report(con, start: date, end: date, n_contracts: int) -> None:
    """Aggregate missing candles per trading day across all active contracts."""
    print("\n" + "=" * 78)
    print(f"GAP SUMMARY ({start} -> {end}, {n_contracts} active contracts)")
    print("=" * 78)
    for d in all_days(start, end):
        if d.weekday() >= 5:
            continue
        holiday = con.execute(
            "SELECT COUNT(*) FROM holiday_calendar WHERE holiday_date = ?", [d]
        ).fetchone()[0]
        if holiday:
            print(f"  {d}: HOLIDAY - skipped")
            continue
        expected = expected_minute_count(d)
        stats = con.execute(
            f"""
            WITH per_contract AS (
                SELECT expiry_date, strike_price, option_type,
                       COUNT(DISTINCT trade_time) AS n
                FROM {TABLE}
                WHERE trade_date = ? AND option_type IN ('CE','PE')
                  AND expiry_date >= ?
                GROUP BY ALL
            )
            SELECT COUNT(*) AS contract_days,
                   SUM(CASE WHEN n >= {expected} THEN 1 ELSE 0 END) AS complete,
                   SUM(CASE WHEN n <  {expected} THEN 1 ELSE 0 END) AS incomplete,
                   COALESCE(SUM({expected} - n), 0) AS missing_candles
            FROM per_contract
            """,
            [d, start],
        ).fetchone()
        if not stats or not stats[0]:
            print(f"  {d}: no tracked contracts")
            continue
        cd, complete, incomplete, missing = stats
        print(f"  {d}: contract_days={cd} complete={complete} "
              f"incomplete={incomplete} missing_candles={missing} "
              f"(expected {expected}/contract)")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fill NIFTY option (CE/PE) 1-min candles into DuckDB "
                    "(Angel primary, Breeze fallback)")
    p.add_argument("--from-date", type=date.fromisoformat, default=None,
                   help="Start date (default: latest date in options_ticks + 1)")
    p.add_argument("--to-date", type=date.fromisoformat, default=date.today(),
                   help="End date (default: today)")
    p.add_argument("--expiry", type=date.fromisoformat, action="append",
                   help="Restrict to this expiry (repeatable)")
    p.add_argument("--max-contracts", type=int, default=None,
                   help="Cap number of contracts (for controlled runs)")
    p.add_argument("--broker", choices=("auto", "angel", "breeze"), default="auto",
                   help="auto = Angel first, Breeze fallback for the rest")
    p.add_argument("--execute", action="store_true",
                   help="Call APIs and write (default: dry-run)")
    p.add_argument("--include-partial", action="store_true",
                   help="Also top up contract-days that exist but are incomplete")
    p.add_argument("--db", default=DUCK_PATH)
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
        ensure_columns(con)

        start = args.from_date
        if start is None:
            row = con.execute(f"SELECT MAX(trade_date) FROM {TABLE}").fetchone()
            start = (row[0] + timedelta(days=1)) if row and row[0] else date(2024, 1, 1)
            if isinstance(start, datetime):
                start = start.date()
        if end < start:
            log.info("Nothing to do: range %s -> %s is empty.", start, end)
            return 0

        expiries = set(args.expiry) if args.expiry else None
        dates = [d for d in all_days(start, end)
                 if d.weekday() < 5
                 and not con.execute("SELECT COUNT(*) FROM holiday_calendar "
                                     "WHERE holiday_date = ?", [d]).fetchone()[0]]

        # ---- Build the capped contract set from NIFTY-spot ATM snapshots ----
        notes: list[str] = []
        try:
            master = angel_option_master()
        except Exception as e:  # noqa: BLE001
            log.warning("Could not load Angel scrip master (%s); "
                        "falling back to table-tracked contracts.", e)
            master = {}
        if master:
            contracts, notes = build_atm_contracts(con, dates, master)
        else:
            contracts = active_contracts(con, start, expiries)
        if expiries:
            contracts = [c for c in contracts if c["expiry_date"] in expiries]
        if args.max_contracts:
            contracts = contracts[: args.max_contracts]
        for note in notes:
            log.info("   [spot] %s", note)
        if not contracts or not dates:
            log.info("No contracts to fill for range %s -> %s.", start, end)
            return 0
        todo, stats = plan_contract_days(con, contracts, dates, args.include_partial)

        log.info("=" * 78)
        log.info("NIFTY OPTION FILLER (duckdb) range=%s -> %s broker=%s mode=%s",
                 start, end, args.broker, "EXECUTE" if args.execute else "DRY-RUN")
        log.info("=" * 78)
        log.info("active_contracts=%d contract_days=%s", len(contracts), stats)

        if not todo:
            log.info("Nothing to fetch - all tracked contract-days are up to date.")
            print_gap_report(con, start, end, len(contracts))
            return 0
        if not args.execute:
            log.info("DRY-RUN: no API calls or writes. Re-run with --execute.")
            print_gap_report(con, start, end, len(contracts))
            return 0

        by_source = {"angel": 0, "breeze": 0}
        remaining = list(todo)

        # ---- Phase 1: Angel One (primary) ----------------------------------
        if args.broker in ("auto", "angel"):
            client = angel_login()

            angel_todo, unresolved = [], []
            for item in remaining:
                if item["contract"].get("token"):
                    item["token"] = item["contract"]["token"]
                    angel_todo.append(item)
                else:
                    unresolved.append(item)
            if unresolved:
                log.info("%d contract-days have no Angel token (expired contracts) "
                         "-> routed to Breeze fallback", len(unresolved))

            def do_angel(item):
                return fetch_angel_with_retry(client, item["token"], item["date"])

            inserted, fallback = run_phase("ANGEL", do_angel, angel_todo,
                                           con, ANGEL_SLEEP_SECONDS)
            by_source["angel"] = inserted
            remaining = unresolved + fallback
            log.info("Angel phase done: inserted=%d -> %d contract-days still missing",
                     inserted, len(remaining))

        # ---- Phase 2: Breeze fallback --------------------------------------
        if remaining and args.broker in ("auto", "breeze"):
            breeze_client = breeze_login()

            def do_breeze(item):
                return fetch_breeze_with_retry(breeze_client, item["date"],
                                               item["contract"])

            inserted, still_failed = run_phase("BREEZE", do_breeze, remaining,
                                               con, BREEZE_SLEEP_SECONDS)
            by_source["breeze"] = inserted
            remaining = still_failed
            log.info("Breeze phase done: inserted=%d -> %d contract-days still missing",
                     inserted, len(remaining))

        # ---- Summary ---------------------------------------------------------
        log.info("=" * 78)
        log.info("SUMMARY")
        log.info("=" * 78)
        log.info("rows_inserted: angel=%d breeze=%d total=%d",
                 by_source["angel"], by_source["breeze"],
                 by_source["angel"] + by_source["breeze"])
        log.info("contract-days still incomplete after fallback: %d", len(remaining))
        for item in remaining[:20]:
            c = item["contract"]
            have_now = con.execute(
                f"""SELECT COUNT(DISTINCT trade_time) FROM {TABLE}
                    WHERE trade_date = ? AND expiry_date = ?
                      AND strike_price = ? AND option_type = ?""",
                [item["date"], c["expiry_date"], c["strike_price"],
                 c["option_type"]],
            ).fetchone()[0]
            missing = item["expected"] - have_now
            reason = item.get("reason", "incomplete")
            phase = item.get("phase", "-")
            provider_rows = item.get("provider_rows", "-")
            if reason == "provider_no_data":
                explain = (f"provider returned 0 candles (~ {missing} missing) - "
                           "likely illiquid / no trades this day")
            elif reason.startswith("error:"):
                explain = f"provider call failed: {reason[6:]}"
            else:
                explain = (f"provider gave partial data; {missing} minute(s) "
                           "not published by either broker")
            log.info("  UNRESOLVED %s %s: have %d/%d (missing %d) "
                     "provider=%s rows=%s - %s",
                     item["date"], contract_label(c), have_now,
                     item["expected"], missing, phase, provider_rows, explain)
        log.info("options_ticks total rows: %d",
                 con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0])

        print_gap_report(con, start, end, len(contracts))
        return 0 if not remaining else 2
    finally:
        con.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.error("Interrupted by user.")
        sys.exit(130)
