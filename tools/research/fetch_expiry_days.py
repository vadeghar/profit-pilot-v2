"""Download 1-minute expiry-day data (spot + every strike the day traded through) from ICICI Breeze.

Research input for expiry-day option-buying patterns. For each weekly expiry
day of NIFTY (Tuesday) and SENSEX (Thursday) since Sep-2025 it stores the index
and all CE/PE strikes from below the day's low to above its high:

    data/research/expiry/<NIFTY|SENSEX>/<YYYY-MM-DD>.csv
    ts,kind,strike,open,high,low,close,volume,oi        (kind = IDX | CE | PE)

    python -m tools.research.fetch_expiry_days [--underlying NIFTY SENSEX] [--start 2025-09-01] [--end 2026-09-30]

Resumable (existing days are skipped). ~25 Breeze requests per day.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time as _t
from datetime import date, datetime, time, timedelta
from pathlib import Path

from market_data.option_symbol import get_option_symbol
from market_data.rate_limiter import get_limiter
from market_data.trading_days import TradingCalendar

OUT = Path("data/research/expiry")
UNDERLYINGS = {
    # weekday: 0=Mon. Regime since Sep-2025: NIFTY Tuesday, SENSEX Thursday.
    "NIFTY": dict(weekday=1, step=50, pad=150, spot=dict(stock_code="NIFTY", exchange_code="NSE", product_type="cash"),
                  opt="NIFTY"),
    "SENSEX": dict(weekday=3, step=100, pad=500, spot=dict(stock_code="BSESEN", exchange_code="BSE", product_type="cash"),
                   opt="BSE:SENSEX"),
}


def _rows(client, day: date, **req) -> list[dict]:
    lo, hi = datetime.combine(day, time(9, 15)), datetime.combine(day, time(15, 30))
    for attempt in range(4):
        try:
            with get_limiter("breeze"):
                r = client.get_historical_data_v2(interval="1minute", from_date=f"{lo:%Y-%m-%dT%H:%M:%S}.000Z",
                                                  to_date=f"{hi:%Y-%m-%dT%H:%M:%S}.000Z", **req)
            if r and r.get("Status") == 200:
                return r.get("Success") or []
            if r and "limit" in str(r.get("Error", "")).lower():
                raise SystemExit(f"Breeze limit reached: {r.get('Error')}")
        except SystemExit:
            raise
        except Exception as e:
            print(f"    retry {attempt + 1}: {e}", flush=True)
        _t.sleep(2 + 3 * attempt)
    return []


def expiry_days(weekday: int, start: date, end: date, cal: TradingCalendar) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() == weekday:
            e = cal.previous_trading_day(d)
            if start <= e <= end and e not in out:
                out.append(e)
        d += timedelta(days=1)
    return out


def fetch_day(client, name: str, day: date) -> str:
    u = UNDERLYINGS[name]
    path = OUT / name / f"{day}.csv"
    if path.exists():
        return "cached"
    spot = _rows(client, day, **u["spot"])
    if len(spot) < 300:
        return f"no spot data ({len(spot)} rows)"
    lo, hi = min(float(b["low"]) for b in spot), max(float(b["high"]) for b in spot)
    step = u["step"]
    k_lo, k_hi = int((lo - u["pad"]) // step * step), int(-(-(hi + u["pad"]) // step) * step)
    out = [(b["datetime"], "IDX", 0, b["open"], b["high"], b["low"], b["close"], 0, 0) for b in spot]
    empty = 0
    for k in range(k_lo, k_hi + step, step):
        for kind in ("CE", "PE"):
            rows = _rows(client, day, **get_option_symbol("breeze", u["opt"], day, k, kind))
            empty += not rows
            out += [(b["datetime"], kind, k, b["open"], b["high"], b["low"], b["close"], b.get("volume") or 0,
                     b.get("open_interest") or 0) for b in rows]
    n_contracts = 2 * ((k_hi - k_lo) // step + 1)
    if empty > n_contracts // 2:
        return f"not an expiry day? {empty}/{n_contracts} contracts empty"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "kind", "strike", "open", "high", "low", "close", "volume", "oi"])
        w.writerows(sorted(out))
    tmp.replace(path)
    return f"{len(out):,} rows, strikes {k_lo}-{k_hi}, {empty} empty contracts"


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--underlying", nargs="+", default=list(UNDERLYINGS))
    ap.add_argument("--start", default="2025-09-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--limit", type=int, default=0, help="most recent N expiry days per underlying")
    a = ap.parse_args(argv)
    from tools.scalping.import_breeze_1s import _provider
    client = _provider().client
    cal = TradingCalendar()
    for name in a.underlying:
        days = expiry_days(UNDERLYINGS[name]["weekday"], date.fromisoformat(a.start), date.fromisoformat(a.end), cal)
        for day in reversed(days[-a.limit:] if a.limit else days):
            print(f"{datetime.now():%H:%M:%S} {name} {day}: {fetch_day(client, name, day)}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
