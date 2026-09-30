"""Import past NIFTY sessions from ICICI Breeze 1-second bars as pseudo-ticks.

Angel One has no tick history, so days before live recording started can be
rebuilt from Breeze's 1-second bars (OHLC + per-second volume + open interest)
for NIFTY spot, the nearest future and nearest-weekly-expiry options ATM +/- N.
Each 1-second bar becomes one tick in the standard tick schema (source
``breeze_1s``): ltp = bar close, ltq = that second's volume, volume = cumulative,
oi = open interest, and NO bid/ask (quotes are not available historically).

    python -m tools.scalping.import_breeze_1s --date 2026-09-29 [--date ...] [--strikes 5]

Cost: ~23 Breeze requests per instrument per day (1,000 bars per request);
with the default +/-5 strikes that is ~510 requests (~6 min) per day. Breeze
allows ~5,000 requests/day, so import a handful of days at a time.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta

from backtest.four_indicator_backtest import resolve_weekly_expiry
from market_data.option_symbol import get_option_symbol
from market_data.rate_limiter import get_limiter
from market_data.tick_store import Instrument, Tick, TickWriter, day_dir
from market_data.trading_days import TradingCalendar
from strategies.nifty_no_brainer_reference import monthly_expiry_from_calendar
from utils.timezone import IST

STEP = 50
WINDOW = timedelta(minutes=15)   # 900 one-second bars < Breeze's 1,000-row cap
SESSION = (time(9, 15), time(15, 30))


def _provider():
    from tools.strategy_audit.common import ENV_FILE
    from market_data.breeze_data_provider import BreezeHistoricalDataProvider
    p = BreezeHistoricalDataProvider(env_path=str(ENV_FILE), persist_cache=False)
    p.verify_once = True
    p.ensure_authenticated()
    return p


def _fut_expiry(day: date, cal: TradingCalendar) -> date:
    exp = monthly_expiry_from_calendar(day.year, day.month, cal.holidays)
    if day > exp:
        nxt = date(day.year + (day.month == 12), 1 if day.month == 12 else day.month + 1, 1)
        exp = monthly_expiry_from_calendar(nxt.year, nxt.month, cal.holidays)
    return exp


def _bars(client, day: date, **req) -> list[dict]:
    rows, cur = [], datetime.combine(day, SESSION[0])
    end = datetime.combine(day, SESSION[1])
    while cur < end:
        nxt = min(cur + WINDOW, end)
        for attempt in range(4):
            try:
                with get_limiter("breeze"):
                    r = client.get_historical_data_v2(interval="1second", from_date=f"{cur:%Y-%m-%dT%H:%M:%S}.000Z",
                                                      to_date=f"{nxt:%Y-%m-%dT%H:%M:%S}.000Z", **req)
                if r and r.get("Status") == 200:
                    rows += r.get("Success") or []
                    break
            except Exception as e:  # throttling / network: back off and retry
                print(f"    retry {attempt + 1}: {e}", flush=True)
            import time as _t
            _t.sleep(2 + 3 * attempt)
        cur = nxt
    seen, out = set(), []
    for b in rows:  # windows share a boundary second
        if b.get("datetime") not in seen:
            seen.add(b.get("datetime"))
            out.append(b)
    return sorted(out, key=lambda b: b["datetime"])


def import_day(day: date, strikes_each_side: int = 5, client=None, root=None) -> dict:
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
    for tok, inst in insts.items():
        bars = spot_bars if tok == "NIFTY-IDX" else _bars(client, day, **requests[tok])
        counts[tok] = len(bars)
        cum = 0
        for b in bars:
            v = int(float(b.get("volume") or 0))
            cum += v
            ts = datetime.strptime(b["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
            ticks.append(Tick(ts=ts, token=tok, ltp=float(b["close"]), ltq=v, volume=cum,
                              oi=float(b.get("open_interest") or 0)))
        print(f"  {day} {tok:18s} {len(bars):6d} bars", flush=True)
    ticks.sort(key=lambda t: (t.ts, t.token))
    for t in ticks:
        writer.write(t)
    writer.close(compress=True)
    return {"date": day.isoformat(), "expiry": expiry.isoformat(), "atm": atm, "instruments": len(insts),
            "ticks": len(ticks), "empty": [t for t, n in counts.items() if n == 0]}


def _lot(day: date) -> int:
    import json
    from pathlib import Path
    meta = json.loads(Path("nifty_expiries.json").read_text(encoding="utf-8")) if Path("nifty_expiries.json").exists() else {}
    rows = [r for r in meta.get("lot_size_effective", []) if r["effective_from"] <= day.isoformat()]
    return int(rows[-1]["lot_size"]) if rows else 65


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", action="append", required=True, help="YYYY-MM-DD (repeatable)")
    ap.add_argument("--strikes", type=int, default=5, help="strikes each side of the opening ATM (default 5)")
    args = ap.parse_args(argv)
    client = _provider().client
    for d in args.date:
        print(f"importing {d} ...", flush=True)
        print(import_day(date.fromisoformat(d), args.strikes, client=client), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
