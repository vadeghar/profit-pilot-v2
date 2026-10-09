"""Daily market-regime panel: is the day a genuine trend or chop? Logged to build the labelled
dataset that a future trend-day gate for S3 (and others) will be calibrated and validated on.

Everything here is computed from ticks we already record (NIFTY spot + near future); India VIX and a
richer PCR reading can be added once the VIX token is wired into the recorder. Each run appends one row
to ``logs/regime_panel.csv`` and prints it, with S3's realised P&L for the day as the outcome label.

    python -m scalp_strategies.tools.regime_report --date 2026-10-08

deploy/linux/regime_report.sh runs it from cron after the close.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

import platform_config
from market_data.tick_store import best_source, load_instruments, read_ticks
from utils.timezone import IST

CSV_PATH = Path(platform_config.PROJECT_ROOT) / "logs" / "regime_panel.csv"
COLS = ["date", "source", "open", "high", "low", "close", "range_pts", "net_pts", "net_over_range",
        "or_break", "or_hold_pct", "vwap_side_pct", "vwap_ext_bp", "adx14_5m", "trend_score", "s3_net", "s3_trades"]


def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> float:
    """Wilder ADX on bar arrays; returns the last value (0 if too few bars)."""
    if len(close) < 2 * n + 1:
        return 0.0
    up, dn = high[1:] - high[:-1], low[:-1] - low[1:]
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum.reduce([high[1:] - low[1:], np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])])

    def rma(x):
        out = np.zeros_like(x, dtype=float)
        out[n - 1] = x[:n].mean()
        for i in range(n, len(x)):
            out[i] = (out[i - 1] * (n - 1) + x[i]) / n
        return out
    atr = rma(tr)
    pdi = 100 * rma(plus_dm) / np.where(atr == 0, np.nan, atr)
    mdi = 100 * rma(minus_dm) / np.where(atr == 0, np.nan, atr)
    dx = 100 * np.abs(pdi - mdi) / np.where((pdi + mdi) == 0, np.nan, pdi + mdi)
    dx = np.nan_to_num(dx)
    return float(rma(dx)[-1])


def _bars_5m(stamps: list[datetime], px: list[float]) -> tuple:
    """5-minute OHLC from a tick stream."""
    buckets: dict = {}
    for ts, p in zip(stamps, px):
        key = ts.replace(minute=(ts.minute // 5) * 5, second=0, microsecond=0)
        b = buckets.get(key)
        if b is None:
            buckets[key] = [p, p, p, p]
        else:
            b[1], b[2], b[3] = max(b[1], p), min(b[2], p), p
    o = [buckets[k] for k in sorted(buckets)]
    return (np.array([b[1] for b in o]), np.array([b[2] for b in o]), np.array([b[3] for b in o]))


def _s3_day(day: date) -> tuple:
    path = Path(platform_config.FORWARD_TEST_DIR) / "scalping" / "scalp_pcr_velocity.json"
    if not path.exists():
        return (None, 0)
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return (None, 0)
    tr = [t for t in st.get("trades", []) if t.get("date") == day.isoformat()]
    return (round(sum(t["net"] for t in tr), 2) if tr else 0.0, len(tr))


def compute(day: date, source: str | None = None) -> dict:
    source = source or best_source(day)
    if not source:
        raise SystemExit(f"no recorded ticks for {day}")
    insts, _ = load_instruments(source, day)
    idx = next(t for t, i in insts.items() if i.kind == "IDX")
    fut = next((t for t, i in insts.items() if i.kind == "FUT"), None)
    istamp, ipx, fstamp, fpx, fv = [], [], [], [], []
    for t in read_ticks(source, day):
        if t.token == idx and t.ltp > 0:
            istamp.append(t.ts); ipx.append(t.ltp)
        elif t.token == fut and t.ltp > 0:
            fstamp.append(t.ts); fpx.append(t.ltp); fv.append(t.ltq or 0)
    px = np.array(ipx)
    o, c, hi, lo = px[0], px[-1], px.max(), px.min()
    rng = hi - lo
    t0 = istamp[0]
    or_end = t0 + timedelta(minutes=30)
    orr = [p for ts, p in zip(istamp, ipx) if ts <= or_end]
    orh, orl = max(orr), min(orr)
    after = [p for ts, p in zip(istamp, ipx) if ts > or_end]
    brk = "UP" if c > orh else "DN" if c < orl else "in"
    hold = (np.mean([p > orh for p in after]) if brk == "UP" else
            np.mean([p < orl for p in after]) if brk == "DN" else 0.0) if after else 0.0
    # futures VWAP side and extension
    cum_pv = cum_v = 0.0
    above = ext = 0.0
    n_above = n = 0
    for p, v in zip(fpx, fv):
        cum_pv += p * v; cum_v += v
        vw = cum_pv / cum_v if cum_v else p
        n += 1; n_above += (p > vw)
        ext = max(ext, abs(p - vw) / vw)
    vwap_side = (n_above / n) if n else 0.0
    H, L, C = _bars_5m(istamp, ipx)
    adx = round(_adx(H, L, C), 1)
    net_over_rng = round(float(abs(c - o) / rng), 2) if rng else 0.0
    hold_pct, side_pct, ext_bp = round(hold * 100), round(vwap_side * 100), round(ext * 1e4)
    # Provisional "how trend-like" score, 0-5 - a count of trend-ish conditions, NOT a calibrated gate.
    # Kept continuous so the activation threshold can be fitted later from the accumulated log.
    trend_score = int((adx >= 22) + (hold_pct >= 60) + (ext_bp >= 70) + (net_over_rng >= 0.5)
                      + (abs(side_pct - 50) >= 35))
    s3_net, s3_n = _s3_day(day)
    return {"date": day.isoformat(), "source": source, "open": round(o, 1), "high": round(hi, 1),
            "low": round(lo, 1), "close": round(c, 1), "range_pts": round(rng, 1), "net_pts": round(c - o, 1),
            "net_over_range": net_over_rng, "or_break": brk, "or_hold_pct": hold_pct,
            "vwap_side_pct": side_pct, "vwap_ext_bp": ext_bp, "adx14_5m": adx,
            "trend_score": trend_score, "s3_net": "" if s3_net is None else s3_net, "s3_trades": s3_n}


def append_csv(row: dict) -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if CSV_PATH.exists():
        existing = [r for r in csv.DictReader(CSV_PATH.open(encoding="utf-8")) if r.get("date") != row["date"]]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in existing:
            w.writerow(r)
        w.writerow(row)


def main(argv: list) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--source")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    row = compute(date.fromisoformat(a.date), a.source)
    if not a.no_write:
        append_csv(row)
    print("regime panel:", {k: row[k] for k in ("date", "trend_score", "adx14_5m", "or_break", "or_hold_pct",
                                                "vwap_side_pct", "vwap_ext_bp", "net_over_range", "s3_net", "s3_trades")})


if __name__ == "__main__":
    main(sys.argv[1:])
