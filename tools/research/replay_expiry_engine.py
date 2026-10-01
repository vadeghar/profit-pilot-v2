"""Replay the downloaded 1-minute NIFTY expiry days through the real ExpiryTrendBreakout engine.

Cross-checks the live implementation against the research harness (expiry_patterns.py): each
1-minute bar becomes a path of pseudo-ticks open -> low -> high -> close (high before low on a down
bar), interpolated so stops and targets trigger near their level. There are no quotes, so the engine fills at
LTP +/- ``no_quote_slippage`` (Rs 0.5), harsher than the research's 0.5%.

    python -m tools.research.replay_expiry_engine [--capital 50000]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from market_data.tick_store import Instrument, Tick
from strategies.scalping import ExpiryTrendBreakout
from utils.timezone import IST

ROOT = Path("data/research/expiry/NIFTY")
STEPS = 6  # interpolated ticks per leg of the open -> extreme -> extreme -> close path


def day_ticks(path: Path):
    d = pd.read_csv(path, parse_dates=["ts"])
    d = d[(d.ts.dt.strftime("%H:%M") >= "09:15") & (d.ts.dt.strftime("%H:%M") <= "15:29")]
    insts = {"IDX": Instrument("IDX", "NIFTY", "IDX", exchange="NSE")}
    for kind, k in d[d.kind != "IDX"][["kind", "strike"]].drop_duplicates().itertuples(index=False):
        insts[f"{k}{kind}"] = Instrument(f"{k}{kind}", f"NIFTY{k}{kind}", kind, float(k), 65, path.stem)
    ticks, cum = [], {}
    for r in d.itertuples(index=False):
        tok = "IDX" if r.kind == "IDX" else f"{r.strike}{r.kind}"
        ts = r.ts.to_pydatetime().replace(tzinfo=IST)
        legs = (r.open, r.low, r.high, r.close) if r.close >= r.open else (r.open, r.high, r.low, r.close)
        path_px = [legs[i] + (legs[i + 1] - legs[i]) * j / STEPS for i in range(3) for j in range(STEPS)] + [legs[3]]
        for n, px in enumerate(path_px):
            cum[tok] = cum.get(tok, 0) + int(r.volume // len(path_px))
            ticks.append(Tick(ts + timedelta(seconds=1 + n * 57 / (len(path_px) - 1)), tok, round(float(px) * 20) / 20,
                              int(r.volume // len(path_px)), cum[tok], float(r.oi)))
    ticks.sort(key=lambda t: t.ts)
    return insts, ticks


def main(argv: list) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=50_000)
    a = ap.parse_args(argv)
    eng = ExpiryTrendBreakout({}, capital=a.capital)
    files = sorted(ROOT.glob("2*.csv"))
    for f in files:
        insts, ticks = day_ticks(f)
        eng.set_instruments(insts)
        for t in ticks:
            eng.on_tick(t)
        eng.finish()
    rets = [t.net / (t.entry * t.qty) for t in eng.trades]
    wins = [r for r in rets if r > 0]
    streak = worst = 0
    for r in rets:
        streak = streak + 1 if r <= 0 else 0
        worst = max(worst, streak)
    peak, dd, bal = a.capital, 0.0, a.capital
    for t in eng.trades:
        bal += t.net
        peak = max(peak, bal)
        dd = min(dd, bal / peak - 1)
    print(f"{len(files)} expiry days, {len(eng.trades)} trades on {len({t.date for t in eng.trades})} days")
    print(f"win rate {len(wins) / len(rets):.0%}, avg return per trade {sum(rets) / len(rets):+.1%}, "
          f"doubled {sum(r >= 0.9 for r in rets) / len(rets):.0%}, longest losing streak {worst}")
    print(f"balance Rs {a.capital:,.0f} -> Rs {eng.balance:,.0f} ({eng.balance / a.capital - 1:+.1%}) at "
          f"{eng.cfg.deploy_pct:.0%} of the balance per trade, max drawdown {dd:.1%}")
    print(pd.Series([t.reason for t in eng.trades]).value_counts().to_dict())
    for t in eng.trades:
        print(f"  {t.date} {t.entry_time[11:16]}-{t.exit_time[11:16]} {t.symbol[5:]:8s} x{t.lots:2d} {t.entry:7.2f} -> {t.exit:7.2f} "
              f"{t.reason:10s} {t.net:+9,.0f}")


if __name__ == "__main__":
    main(sys.argv[1:])
