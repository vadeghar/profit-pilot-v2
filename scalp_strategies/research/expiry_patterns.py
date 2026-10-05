"""Expiry-day option-buying pattern research on 1-minute Breeze data (see fetch_expiry_days.py).

Three families of rules, each defined only by time, strike-by-premium and price action:

  BRK    after T, when spot closes beyond its high/low of the last N minutes, buy the CE (PE)
         whose premium is nearest P; one trade per direction per day.
  STRGL  at T, buy the CE and the PE nearest premium P (a long strangle), each leg managed alone.
  MOMO   after T, buy an option that was <= P and closes above its own high of the last N minutes
         while spot moves the same way.

Fills: signal on a 1-minute close, entry at the next minute's open plus slippage; stop and target
are checked on each later bar's low/high (stop first when both are touched - the conservative
order); everything is closed at EXIT. Costs: slippage per side + round-trip charges (COSTS).

    python -m scalp_strategies.research.expiry_patterns            # full grid, both indices
"""
from __future__ import annotations

import itertools
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("data/research/expiry")
EXIT = "15:20"
# per-side slippage = max(abs, pct x price); charges = round-trip % of entry value (brokerage, STT, exchange, GST, stamp)
COSTS = {"NIFTY": dict(abs=0.10, pct=0.005, charges=0.005, step=50, pscale=1.0),
         "SENSEX": dict(abs=0.30, pct=0.0075, charges=0.005, step=100, pscale=3.3)}


@dataclass
class Day:
    name: str
    date: str
    t: np.ndarray        # "HH:MM" per minute
    spot: np.ndarray
    strikes: np.ndarray
    O: dict              # kind -> [strike, minute]
    H: dict
    L: dict
    C: dict
    V: dict

    def idx(self, hhmm: str) -> int:
        return int(np.searchsorted(self.t, hhmm))


def load(name: str) -> list[Day]:
    days = []
    for f in sorted((ROOT / name).glob("*.csv")):
        d = pd.read_csv(f, parse_dates=["ts"])
        d["m"] = d.ts.dt.strftime("%H:%M")
        d = d[(d.m >= "09:15") & (d.m <= "15:29")]
        t = np.array(sorted(d.m.unique()))
        spot = d[d.kind == "IDX"].set_index("m").close.reindex(t).ffill().bfill().to_numpy()
        strikes = np.array(sorted(d[d.kind != "IDX"].strike.unique()))
        arr = {c: {} for c in "OHLCV"}
        for kind in ("CE", "PE"):
            k = d[d.kind == kind]
            for c, col in zip("OHLCV", ("open", "high", "low", "close", "volume")):
                p = k.pivot_table(index="strike", columns="m", values=col, aggfunc="last").reindex(index=strikes, columns=t)
                arr[c][kind] = (p.fillna(0) if c == "V" else p.ffill(axis=1).bfill(axis=1)).to_numpy()
        days.append(Day(name, f.stem, t, spot, strikes, arr["O"], arr["H"], arr["L"], arr["C"], arr["V"]))
    return days


def pick(day: Day, kind: str, i: int, premium: float):
    """Strike row whose premium at minute i is nearest ``premium`` (within -50%/+60%)."""
    px = day.C[kind][:, i]
    if not np.isfinite(px).any():
        return None
    j = int(np.nanargmin(np.abs(px - premium)))
    return j if 0.5 * premium <= px[j] <= 1.6 * premium else None


def trade(day: Day, kind: str, j: int, i: int, sl: float, tgt: float, trail: float = 0.0):
    """Enter at minute i+1's open; returns the trade or None when there is no time left."""
    c = COSTS[day.name]
    e, x = i + 1, day.idx(EXIT)
    if e >= x:
        return None
    raw = day.O[kind][j, e]
    if not np.isfinite(raw) or raw <= 0.5:
        return None
    entry = raw + max(c["abs"], c["pct"] * raw)
    stop, target, peak, out, why = raw * (1 - sl), raw * (1 + tgt), raw, None, "TIME"
    for b in range(e, x + 1):
        o, h, l = day.O[kind][j, b], day.H[kind][j, b], day.L[kind][j, b]
        if b > e and o <= stop:
            out, why = o, "STOP"
            break
        if l <= stop:
            out, why = stop, "STOP" if stop < raw else "TRAIL"
            break
        if h >= target:
            out, why = (max(target, o) if b > e else target), "TARGET"
            break
        peak = max(peak, h)
        if trail and peak >= raw * (1 + trail):
            stop = max(stop, raw, peak * (1 - trail))
    if out is None:
        out = day.C[kind][j, x]
    exit_px = max(out - max(c["abs"], c["pct"] * out), 0.05)
    ret = exit_px / entry - 1 - c["charges"]
    return dict(date=day.date, kind=kind, strike=int(day.strikes[j]), t_in=day.t[e], entry=round(entry, 2),
                exit=round(exit_px, 2), why=why, ret=ret)


def brk(day: Day, T: str, N: int, P: float, sl: float, tgt: float, trail: float, end: str = "15:05") -> list:
    out, done = [], set()
    P = P * COSTS[day.name]["pscale"]
    for i in range(max(day.idx(T), N), day.idx(end)):
        w = day.spot[i - N:i]
        kind = "CE" if day.spot[i] > w.max() else "PE" if day.spot[i] < w.min() else None
        if not kind or kind in done:
            continue
        j = pick(day, kind, i, P)
        if j is None:
            continue
        tr = trade(day, kind, j, i, sl, tgt, trail)
        if tr:
            done.add(kind)
            out.append(tr)
    return out


def strangle(day: Day, T: str, P: float, sl: float, tgt: float, trail: float) -> list:
    i, out = day.idx(T), []
    P = P * COSTS[day.name]["pscale"]
    for kind in ("CE", "PE"):
        j = pick(day, kind, i, P)
        tr = trade(day, kind, j, i, sl, tgt, trail) if j is not None else None
        if tr:
            out.append(tr)
    return out


def momo(day: Day, T: str, N: int, P: float, sl: float, tgt: float, trail: float, end: str = "15:05") -> list:
    out, done = [], set()
    P = P * COSTS[day.name]["pscale"]
    for i in range(max(day.idx(T), N + 1), day.idx(end)):
        for kind, sg in (("CE", 1), ("PE", -1)):
            if kind in done or sg * (day.spot[i] - day.spot[i - 5]) <= 0:
                continue
            C, H = day.C[kind], day.H[kind]
            was_cheap = C[:, i - 1] <= P
            broke = C[:, i] > H[:, i - N:i].max(axis=1)
            cand = np.where(was_cheap & broke & (C[:, i] >= 0.4 * P) & (day.V[kind][:, i] > 0))[0]
            if not len(cand):
                continue
            j = cand[np.argmax(C[cand, i])]  # the most expensive qualifying strike = nearest the money
            tr = trade(day, kind, j, i, sl, tgt, trail)
            if tr:
                done.add(kind)
                out.append(tr)
    return out


def stats(trades: list) -> dict:
    if not trades:
        return dict(n=0)
    r = np.array([t["ret"] for t in trades])
    by_day = pd.Series(r, index=[t["date"] for t in trades]).groupby(level=0).mean()  # capital split across the day's legs
    eq = np.cumprod(1 + 0.25 * by_day.to_numpy())  # 25% of capital per expiry day
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return dict(n=len(r), days=len(by_day), win=(r > 0).mean(), avg=r.mean(), med=np.median(r),
                p2x=(r >= 0.9).mean(), best=r.max(), pf=r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9),
                day_avg=by_day.mean(), day_win=(by_day > 0).mean(), eq25=eq[-1] - 1, dd25=dd)


FAMILIES = {"BRK": brk, "MOMO": momo, "STRGL": strangle}


def specs() -> list:
    out = []
    for T, N, P, sl, tgt, trail in itertools.product(("09:30", "11:00", "13:00", "13:45", "14:15", "14:40"), (15, 30, 60),
                                                     (5, 10, 20, 40, 80), (0.3, 0.5), (1.0, 2.0, 4.0), (0.0, 0.3)):
        out.append(("BRK", dict(T=T, N=N, P=P, sl=sl, tgt=tgt, trail=trail)))
        out.append(("MOMO", dict(T=T, N=N, P=P, sl=sl, tgt=tgt, trail=trail)))
    for T, P, sl, tgt, trail in itertools.product(("09:20", "11:00", "13:00", "13:45", "14:15", "14:40", "14:55"),
                                                  (5, 10, 20, 40, 80), (0.3, 0.5, 0.99), (1.0, 2.0, 4.0), (0.0, 0.3)):
        out.append(("STRGL", dict(T=T, P=P, sl=sl, tgt=tgt, trail=trail)))
    return out


def grid(days: list) -> pd.DataFrame:
    half = len(days) // 2
    rows = []
    for fam, kw in specs():
        per_day = [FAMILIES[fam](d, **kw) for d in days]
        a = stats([t for x in per_day for t in x])
        if not a["n"]:
            continue
        h1 = stats([t for x in per_day[:half] for t in x])
        h2 = stats([t for x in per_day[half:] for t in x])
        rows.append(dict(fam=fam, **kw, **a, avg_h1=h1.get("avg", np.nan), avg_h2=h2.get("avg", np.nan)))
    return pd.DataFrame(rows)


def main(argv: list) -> None:
    pd.set_option("display.width", 250, "display.max_columns", 40, "display.float_format", lambda x: f"{x:.3f}")
    for name in argv or ["NIFTY", "SENSEX"]:
        days = load(name)
        print(f"\n===== {name}: {len(days)} expiry days {days[0].date} .. {days[-1].date}")
        g = grid(days)
        g.to_csv(ROOT / f"grid_{name}.csv", index=False)
        ok = g[(g.n >= max(10, len(days) // 3)) & (g.avg_h1 > 0) & (g.avg_h2 > 0)]
        print(f"{len(g)} rule sets, {len(ok)} profitable in both halves")
        print(ok.sort_values("avg", ascending=False).head(25).to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1:])
