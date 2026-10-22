"""Import past NIFTY sessions from ICICI Breeze 1-second bars as pseudo-ticks.

Angel One has no tick history, so days before live recording started can be
rebuilt from Breeze's 1-second bars (OHLC + per-second volume + open interest)
for NIFTY spot, the nearest future and nearest-weekly-expiry options ATM +/- N.
Each 1-second bar becomes one tick in the standard tick schema (source
``breeze_1s``): ltp = bar close, ltq = that second's volume, volume = cumulative,
oi = open interest, and NO bid/ask (quotes are not available historically).

    python -m scalp_strategies.tools.import_breeze_1s --date 2026-09-29 [--date ...] [--strikes 5]
    python -m scalp_strategies.tools.import_breeze_1s --from 2026-06-01 --to 2026-09-30 --strikes 8         --workers 4 --root data/ticks_breeze_history --newest-first

Cost: ~23 Breeze requests per instrument per day (1,000 bars per request); +/-5 strikes is ~510 requests
and +/-8 ~830 per day. Breeze documents ~5,000 requests/day, but nothing here throttles: every request has
a 30-second timeout and is retried with back-off, and the run stops only when Breeze returns a rate-limit
or session error (exit code 2). A day is written only when every contract came back, and days already
imported are skipped, so re-running the same command resumes where it stopped.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, as_completed
from datetime import date, datetime, time, timedelta
from typing import Optional

from market_data.expiries import monthly_expiry_from_calendar, nifty_lot_size, resolve_weekly_expiry
from market_data.option_symbol import get_option_symbol
from market_data.tick_store import Instrument, Tick, TickWriter, day_dir
from market_data.trading_days import TradingCalendar
from utils.timezone import IST

STEP = 50
WINDOW = timedelta(minutes=15)   # 900 one-second bars < Breeze's 1,000-row cap
SESSION = (time(9, 15), time(15, 30))


def _provider():
    from market_data.breeze_data_provider import BreezeHistoricalDataProvider
    from platform_config import resolve_env_file
    p = BreezeHistoricalDataProvider(env_path=str(resolve_env_file()), persist_cache=False)
    p.verify_once = True
    p.ensure_authenticated()
    return p


def _fut_expiry(day: date, cal: TradingCalendar) -> date:
    exp = monthly_expiry_from_calendar(day.year, day.month, cal.holidays)
    if day > exp:
        nxt = date(day.year + (day.month == 12), 1 if day.month == 12 else day.month + 1, 1)
        exp = monthly_expiry_from_calendar(nxt.year, nxt.month, cal.holidays)
    return exp


REQUEST_TIMEOUT = 30        # seconds; Breeze calls have no timeout of their own (one hung for 40+ min on 2026-10-21)
MAX_ATTEMPTS = 20           # per window for errors that are neither a rate limit nor "no data" (~15 min of back-off)
_CALLS = ThreadPoolExecutor(max_workers=32, thread_name_prefix="breeze-call")
_STOP = threading.Event()   # set on a rate-limit / session error: every parallel download stops at its next window


class RateLimited(RuntimeError):
    """Breeze answered with a rate-limit / quota error: stop, the run resumes after it resets."""


class SessionExpired(RuntimeError):
    """Breeze rejected the session: a fresh login is needed."""


def _classify(text: str) -> Optional[type]:
    t = text.lower()
    if any(k in t for k in ("limit", "too many", "exceed", "429", "throttl", "quota")):
        return RateLimited
    if any(k in t for k in ("session", "unauthor", "invalid key", "api key", "login", "token")):
        return SessionExpired
    return None


def _call(client, **kw) -> dict:
    """One history request on a worker thread, abandoned after REQUEST_TIMEOUT."""
    try:
        return _CALLS.submit(client.get_historical_data_v2, **kw).result(timeout=REQUEST_TIMEOUT) or {}
    except FutureTimeout:
        raise TimeoutError(f"no answer in {REQUEST_TIMEOUT}s") from None


def _bars(client, day: date, **req) -> list[dict]:
    """All 1-second bars for one contract and day. Retries everything except a real rate-limit or session error,
    and never skips a window silently: after MAX_ATTEMPTS it raises, so a day is either complete or not saved."""
    rows, cur = [], datetime.combine(day, SESSION[0])
    end = datetime.combine(day, SESSION[1])
    while cur < end:
        if _STOP.is_set():
            raise RuntimeError("stopped: another download hit a rate-limit or session error")
        nxt = min(cur + WINDOW, end)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                r = _call(client, interval="1second", from_date=f"{cur:%Y-%m-%dT%H:%M:%S}.000Z",
                          to_date=f"{nxt:%Y-%m-%dT%H:%M:%S}.000Z", **req)
                if r.get("Status") == 200:
                    rows += r.get("Success") or []
                    break
                err = str(r.get("Error") or r)
                if "no data" in err.lower():          # an illiquid strike with no trades in this window
                    break
                kind = _classify(err)
            except (RateLimited, SessionExpired):
                raise
            except Exception as e:                    # timeout / network / parsing: back off and retry
                err, kind = f"{type(e).__name__}: {e}", _classify(str(e))
            if kind:
                _STOP.set()
                raise kind(f"{req.get('stock_code')} {req.get('strike_price', '')}{req.get('right', '')} {cur:%H:%M}: {err}")
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(f"gave up after {attempt} attempts at {cur:%H:%M}: {err}")
            print(f"    retry {attempt} at {cur:%H:%M} ({err[:120]})", flush=True)
            _time.sleep(min(2 ** attempt, 60))
        cur = nxt
    seen, out = set(), []
    for b in rows:  # windows share a boundary second
        if b.get("datetime") not in seen:
            seen.add(b.get("datetime"))
            out.append(b)
    return sorted(out, key=lambda b: b["datetime"])


def import_day(day: date, strikes_each_side: int = 5, client=None, root=None, workers: int = 1) -> dict:
    """Download one day (contracts ``workers`` at a time) and save it only when every contract came back."""
    cal = TradingCalendar()
    if not cal.is_trading_day(day):
        raise ValueError(f"{day} is not a trading day")
    client = client or _provider().client
    spot_req = dict(stock_code="NIFTY", exchange_code="NSE", product_type="cash")
    spot_bars = _bars(client, day, **spot_req)
    if not spot_bars:
        raise RuntimeError(f"Breeze returned no NIFTY 1-second bars for {day}")
    open_px = float(spot_bars[0]["open"])
    atm = round(open_px / STEP) * STEP
    expiry, fut_exp = resolve_weekly_expiry(day, cal), _fut_expiry(day, cal)
    insts = {"NIFTY-IDX": Instrument("NIFTY-IDX", "NIFTY", "IDX", exchange="NSE"),
             "NIFTY-FUT": Instrument("NIFTY-FUT", f"NIFTY{fut_exp:%d%b%y}FUT".upper(), "FUT", lot=_lot(day),
                                     expiry=fut_exp.isoformat())}
    requests = {"NIFTY-FUT": dict(stock_code="NIFTY", exchange_code="NFO", product_type="futures",
                                  expiry_date=f"{fut_exp:%Y-%m-%d}T06:00:00.000Z")}
    for i in range(-strikes_each_side, strikes_each_side + 1):
        k = atm + i * STEP
        for kind in ("CE", "PE"):
            tok = f"NIFTY-{k}-{kind}"
            insts[tok] = Instrument(tok, f"NIFTY{expiry:%d%b%y}{k}{kind}".upper(), kind, float(k), _lot(day),
                                    expiry.isoformat())
            requests[tok] = get_option_symbol("breeze", "NIFTY", expiry, k, kind)
    fetch = {tok: req for tok, req in requests.items()}
    got = {"NIFTY-IDX": spot_bars}
    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="breeze-contract") as pool:
        futures = {pool.submit(_bars, client, day, **req): tok for tok, req in fetch.items()}
        try:
            for fut in as_completed(futures):
                tok = futures[fut]
                got[tok] = fut.result()
                print(f"  {day} {tok:18s} {len(got[tok]):6d} bars", flush=True)
        except BaseException:
            for f in futures:
                f.cancel()
            raise
    folder = day_dir("breeze_1s", day, root)
    for f in ("ticks.csv", "ticks.csv.gz"):
        if (folder / f).exists():
            (folder / f).unlink()
    writer = TickWriter("breeze_1s", day, root)
    writer.write_instruments(insts, {"source": "breeze_1s", "underlying": "NIFTY", "expiry": expiry.isoformat(),
                                     "atm_at_open": atm, "note": "1-second bars as pseudo-ticks: no bid/ask; "
                                     "ltq is the second's total volume"})
    ticks: list[Tick] = []
    counts = {}
    for tok in insts:
        bars = got[tok]
        counts[tok] = len(bars)
        cum = 0
        for b in bars:
            v = int(float(b.get("volume") or 0))
            cum += v
            ts = datetime.strptime(b["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
            ticks.append(Tick(ts=ts, token=tok, ltp=float(b["close"]), ltq=v, volume=cum,
                              oi=float(b.get("open_interest") or 0)))
    ticks.sort(key=lambda t: (t.ts, t.token))
    for t in ticks:
        writer.write(t)
    writer.close(compress=True)
    return {"date": day.isoformat(), "expiry": expiry.isoformat(), "atm": atm, "instruments": len(insts),
            "ticks": len(ticks), "empty": [t for t, n in counts.items() if n == 0]}


def _lot(day: date) -> int:
    return nifty_lot_size(day)


def trading_days(start: date, end: date) -> list[date]:
    cal = TradingCalendar()
    return [start + timedelta(days=i) for i in range((end - start).days + 1) if cal.is_trading_day(start + timedelta(days=i))]


def is_done(day: date, root=None) -> bool:
    folder = day_dir("breeze_1s", day, root)
    return (folder / "ticks.csv.gz").exists() and (folder / "instruments.json").exists()


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", action="append", default=[], help="YYYY-MM-DD (repeatable)")
    ap.add_argument("--from", dest="start", help="first day of a range (YYYY-MM-DD); trading days only")
    ap.add_argument("--to", dest="end", help="last day of the range (default: --from)")
    ap.add_argument("--strikes", type=int, default=5, help="strikes each side of the opening ATM (default 5)")
    ap.add_argument("--workers", type=int, default=1, help="contracts downloaded in parallel (default 1)")
    ap.add_argument("--root", help="tick-store root to write into (default data/ticks)")
    ap.add_argument("--newest-first", action="store_true", help="import the range from its last day backwards")
    args = ap.parse_args(argv)
    days = [date.fromisoformat(d) for d in args.date]
    if args.start:
        days += trading_days(date.fromisoformat(args.start), date.fromisoformat(args.end or args.start))
    days = sorted(set(days), reverse=args.newest_first)
    if not days:
        ap.error("give --date or --from")
    client = _provider().client
    for d in days:
        if is_done(d, args.root):   # a day is saved only when complete, so a re-run resumes where it stopped
            print(f"{d} already imported - skipping", flush=True)
            continue
        print(f"importing {d} ...", flush=True)
        try:
            print(import_day(d, args.strikes, client=client, root=args.root, workers=args.workers), flush=True)
        except Exception as e:
            if isinstance(e, (RateLimited, SessionExpired)) or _STOP.is_set():
                print(f"STOPPED at {d}: {type(e).__name__}: {e}", flush=True)
                raise SystemExit(2)
            print(f"FAILED {d} (not saved, retried on the next run): {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
