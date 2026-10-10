"""Per-condition diagnostics for the scalping strategies on a recorded day.

Replays the day through each strategy exactly as live/backtest does, and at
every evaluation (each closed 15-second bucket where the strategy may enter)
records which entry conditions held. Shows, per strategy, how often each
condition passed, the closest near-misses (most conditions true at once) and
which condition blocked those near-misses - the input for deciding whether a
threshold is too strict. A final section replays the experimental options
(EXPERIMENTS) the same way, so their evidence accumulates day by day.

    python -m scalp_strategies.tools.condition_report --date 2026-10-01 [--source angel] [--out report.md]

deploy/linux/condition_report.sh runs it from cron after each session.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from datetime import date, timedelta

from market_data.tick_store import best_source, load_instruments, read_ticks
from scalp_strategies import SCALP_STRATEGIES


class Recorder:
    def __init__(self, names: list[str]):
        self.names = names
        self.evals = 0
        self.passed = Counter()
        self.near = Counter()           # number of conditions true -> occurrences
        self.blockers = Counter()       # for the closest near-misses: which condition failed
        self.best = 0
        self.width = 0                  # conditions per evaluation
        self.samples: list[str] = []

    def record(self, ts, conds: dict[str, bool], label: str) -> None:
        self.evals += 1
        k = sum(conds.values())
        self.width = max(self.width, len(conds))
        for n, v in conds.items():
            self.passed[n] += v
        self.near[k] += 1
        if k > self.best:
            self.best, self.blockers = k, Counter()
            self.samples = []
        if k == self.best:
            for n, v in conds.items():
                if not v:
                    self.blockers[n] += 1
            if len(self.samples) < 3:
                self.samples.append(f"{ts:%H:%M:%S} {label}: missing {', '.join(n for n, v in conds.items() if not v) or 'nothing'}")


def _s1(e, ts, spot, atm, rec):
    step = e.cfg.strike_step
    f = e.S(e.fut_tok) if e.fut_tok else None
    for kind, sg in (("CE", 1), ("PE", -1)):
        opp = "PE" if kind == "CE" else "CE"
        walls = [(k, e.tok(k, kind)) for k in (atm + sg * i * step for i in range(4))]
        walls = [(k, t) for k, t in walls if t and sg * (k - spot) >= -10]
        if not walls or not f or not e.tok(atm, kind):
            continue
        k, t = max(walls, key=lambda x: e.S(x[1]).oi)
        w, a = e.S(t), e.S(e.tok(atm, kind))
        o = e.S(e.tok(k, opp)) if e.tok(k, opp) else None
        if not (e.ready(w) and e.ready(a)) or o is None:
            continue
        m3, m5 = a.n(3), a.n(5)
        rec.record(ts, {
            "spot within 20 of wall": abs(k - spot) <= 20, "wall OI -3% in 3m": w.doi(m3) <= -0.03,
            "opposite strike OI up": o.doi(m3) > 0, "ATM volume >= 2.5x": a.vol_ratio(1, 15) >= 2.5,
            "ATM > 5-min high": a.last().c > a.hh(m5, 1), "ATM > VWAP": a.last().c > a.vwap(),
            "futures price with trade": sg * f.dpx(m3) > 0, "futures OI up": f.doi(m3) > 0,
        }, f"{kind} wall {k:.0f}")


def _s2(e, ts, spot, atm, rec):
    sp = e.S(e.spot_tok)
    m15 = sp.n(15)
    box = sp.win(m15 + 1, 1)
    if len(box) < m15:
        return
    bh, bl = max(b.h for b in box), min(b.l for b in box)
    c = sp.last().c
    lim = e.box_limit()
    box_name = f"spot box <= {e.BOX_PTS} pts" if e.cfg.box_rel <= 0 else f"spot box <= {e.cfg.box_rel:g}x median range"
    for kind, sg, brk in (("CE", 1, c > bh), ("PE", -1, c < bl)):
        for k in (atm, atm + sg * e.cfg.strike_step):
            t = e.tok(k, kind)
            if not t or not e.ready(e.S(t)):
                continue
            o = e.S(t)
            m3 = o.n(3)
            rec.record(ts, {
                box_name: lim is not None and bh - bl <= lim, "box breakout": brk,
                f">= {e.MIN_BIG} big prints in 3m": o.big(m3) >= e.MIN_BIG,
                f"volume >= {e.VOL_MULT:g}x": o.vol_ratio(1, 15) >= e.VOL_MULT,
            }, f"{kind} {k:.0f} (box {bh - bl:.0f} pts)")


def _s3(e, ts, spot, atm, rec):
    if not e.fut_tok:
        return
    ks = [atm + i * e.cfg.strike_step for i in range(-2, 3)]

    def d(kind, lag):
        tot = 0.0
        for k in ks:
            t = e.tok(k, kind)
            if not t:
                return None
            L, m3 = e.S(t).buckets, e.S(t).n(3)
            if len(L) <= lag + m3:
                return None
            tot += L[-1 - lag].oi - L[-1 - lag - m3].oi
        return tot
    m3 = e.S(e.spot_tok).n(3)
    ce0, pe0, ce1, pe1 = d("CE", 0), d("PE", 0), d("CE", m3), d("PE", m3)
    if None in (ce0, pe0, ce1, pe1):
        return
    f = e.S(e.fut_tok)
    for kind, sg in (("CE", 1), ("PE", -1)):
        unwind, build = (ce0, pe0) if kind == "CE" else (pe0, ce0)
        t = e.tok(atm, kind)
        if not t or not e.ready(e.S(t)):
            continue
        a = e.S(t)
        rec.record(ts, {
            "this-window OI shift": (ce0 < 0 and pe0 > 0) if kind == "CE" else (pe0 < 0 and ce0 > 0),
            "previous-window OI shift": (ce1 < 0 and pe1 > 0) if kind == "CE" else (pe1 < 0 and ce1 > 0),
            f"unwind >= {e.MIN_UNWIND_RATIO:.0%} of build": unwind < 0 < build and abs(unwind) >= e.MIN_UNWIND_RATIO * build,
            "both OI legs move": min(abs(unwind), abs(build)) >= e.MIN_TWO_SIDED_RATIO * max(abs(unwind), abs(build), 1),
            "ATM volume >= 2x": a.vol_ratio(1, 15) >= 2, "ATM > VWAP": a.last().c > a.vwap(),
            "futures moving with trade": sg * f.dpx(a.n(3)) > 0,
        }, f"{kind} ATM {atm:.0f}")


def _s4(e, ts, spot, atm, rec):
    if not e.fut_tok:
        return
    sp = e.S(e.spot_tok)
    mp, mr = sp.n(e.POKE_MIN), sp.n(e.RANGE_MIN)
    prior, recent = sp.win(mr + mp, mp), sp.win(mp)
    er = e.trend()
    if len(prior) < mr or er is None:
        return
    H, Lo, c = max(b.h for b in prior), min(b.l for b in prior), sp.last().c
    rec.record(ts, {"poked above range and fell back": max(b.h for b in recent) > H and c < H,
                    f"hour falling (trend <= -{e.MIN_ER:g})": -er >= e.MIN_ER}, f"bull trap -> PE (trend {er:+.2f})")
    rec.record(ts, {"poked below range and came back": min(b.l for b in recent) < Lo and c > Lo,
                    f"hour rising (trend >= {e.MIN_ER:g})": er >= e.MIN_ER}, f"bear trap -> CE (trend {er:+.2f})")


def _burst(e, ts, spot, atm, rec):
    step = e.cfg.strike_step
    for kind, sg in (("CE", 1), ("PE", -1)):
        ot = e.tok(atm, "PE" if kind == "CE" else "CE")
        if not ot:
            continue
        opp = e.S(ot)
        m3 = opp.n(3)
        if not opp.has(m3 + 1):
            continue
        for k in (atm - sg * step, atm, atm + sg * step):
            t = e.tok(k, kind)
            if not t:
                continue
            o = e.S(t)
            if not o.has(o.n(20) + o.n(1)):
                continue
            rec.record(ts, {
                f"volume >= {e.VOL_SPIKE:g}x ({e.BASE_MIN}-min base)": o.vol_ratio(1, e.BASE_MIN) >= e.VOL_SPIKE,
                f"burst ({e.BURST_TICKS} big prints/{e.BURST_WINDOW_S}s)":
                    o.big_prints_since(ts - timedelta(seconds=e.BURST_WINDOW_S), e.BURST_LTQ_MULT) >= e.BURST_TICKS,
                "price up over 3m": o.dpx(m3) > 0, "above VWAP": o.last().c > o.vwap(),
                "opposite ATM OI -2% in 3m": opp.doi(m3) <= e.OPP_UNWIND,
            }, f"{kind} {k:.0f}")


def _expiry(e, ts, spot, atm, rec):
    if ts.second >= e.cfg.bucket_sec:  # the rule is evaluated on completed 1-minute closes only
        return
    for kind in ("CE", "PE"):
        rec.record(ts, {
            "expiry day": e.is_expiry_day(), f"day range >= {e.MIN_RANGE:.1%}": e._range >= e.MIN_RANGE,
            "new day high/low this minute": e._break == kind, "no trade yet this direction": kind not in e._done,
            f"an option near Rs {e.PREMIUM:g}": e.pick_strike(kind) is not None,
            "put/call OI agrees": e.oi_agrees(kind, atm),
        }, f"{kind} (range {e._range:.2%}, put/call OI {e.oi_balance(atm) or 0:.2f})")


def _gamma(e, ts, spot, atm, rec):
    f = e.S(e.fut_tok) if e.fut_tok else None
    for kind, sg in (("CE", 1), ("PE", -1)):
        fut_ok = bool(f and f.cum_v and sg * (f.ltp - f.vwap()) > 0)
        band = [(k, t) for (k, kd), t in e.opt.items() if kd == kind and e.PREMIUM_BAND[0] <= e.S(t).ltp <= e.PREMIUM_BAND[1]]
        base = {"expiry day": e.is_expiry_day(), "future on the trade's side of VWAP": fut_ok,
                f"a strike at Rs {e.PREMIUM_BAND[0]:g}-{e.PREMIUM_BAND[1]:g}": bool(band)}
        if not band:
            rec.record(ts, {**base, f"own OI {e.OI_DROP:.1%} in {e.OI_WINDOW_MIN}m": False, "last bucket traded through the trigger": False,
                            f"60-s volume > {e.VOL_MULT:g}x": False, f"ask aggression >= {e.AGGR_MIN:.0%}": False}, kind)
        for k, t in band:
            se = e.S(t)
            vol60, aggr = e.flow(t, ts)
            avg = se.vol(se.n(e.VOL_BASE_MIN)) / e.VOL_BASE_MIN
            rec.record(ts, {**base, f"own OI {e.OI_DROP:.1%} in {e.OI_WINDOW_MIN}m": se.doi(se.n(e.OI_WINDOW_MIN)) <= e.OI_DROP,
                            "last bucket traded through the trigger": se.last().h >= se.hh(se.n(e.HIGH_MIN), 1) + e.TRIGGER_BUFFER,
                            f"60-s volume > {e.VOL_MULT:g}x": bool(avg and vol60 > e.VOL_MULT * avg),
                            f"ask aggression >= {e.AGGR_MIN:.0%}": aggr >= e.AGGR_MIN}, f"{kind} {k:.0f} at Rs {se.ltp:.2f}")


PROBES = {"scalp_expiry_gamma": _gamma, "scalp_expiry_breakout": _expiry, "scalp_writer_squeeze": _s1, "scalp_stealth_accum": _s2, "scalp_pcr_velocity": _s3,
          "scalp_trap_fade": _s4, "scalp_oi_volume_burst": _burst}


# Experimental options replayed after the live rules: (label, strategy id, config overrides, class attributes).
EXPERIMENTS = [
    ("S2 with volume-based big prints", "scalp_stealth_accum", {"big_print_basis": "volume"}, {}),
    ("S2 with volume-based big prints + box <= 0.75x the day's median range", "scalp_stealth_accum",
     {"big_print_basis": "volume", "box_rel": 0.75}, {}),
    ("OI + Volume Burst with volume-based big prints, 3 in 10 s", "scalp_oi_volume_burst",
     {"big_print_basis": "volume"}, {"BURST_TICKS": 3}),
]


def _replay(sid: str, insts, ticks, overrides: dict | None = None, attrs: dict | None = None):
    cls, probe = SCALP_STRATEGIES[sid], PROBES[sid]
    rec = Recorder([])

    class Probed(cls):
        def signal(self, ts, spot_, atm):
            probe(self, ts, spot_, atm, rec)
            return super().signal(ts, spot_, atm)
    for k, v in (attrs or {}).items():
        setattr(Probed, k, v)
    eng = Probed(insts, capital=50_000, config=cls.default_config().update(overrides))
    for t in ticks:
        eng.on_tick(t)
    eng.finish()
    return eng, rec


def _section(title: str, eng, rec: Recorder) -> list[str]:
    out = [f"## {title}", "",
           f"Evaluations: {rec.evals:,} | trades: {len(eng.trades)} | net PnL: Rs {sum(t.net for t in eng.trades):,.0f}", ""]
    if rec.evals:
        n_conds = rec.width or 1
        out += ["| Condition | Passed | % of evaluations |", "|---|---|---|"]
        for name, n in rec.passed.most_common():
            out.append(f"| {name} | {n:,} | {n / rec.evals:.1%} |")
        blockers = ", ".join(f"{n} ({c})" for n, c in rec.blockers.most_common()) or             "none - every condition held (an entry, unless a position/cooldown/daily limit was active)"
        out += ["", f"Closest approach: **{rec.best} of {n_conds}** conditions true at once "
                f"({rec.near[rec.best]:,} times). Blocking condition(s) at those moments: {blockers}.", ""]
        out += [f"- {s}" for s in rec.samples] + [""]
    for t in eng.trades:
        out.append(f"- Trade {t.entry_time[11:19]}-{t.exit_time[11:19]} {t.symbol} {t.lots} lots "
                   f"{t.entry} -> {t.exit} {t.reason} net Rs {t.net:,.0f} ({t.why})")
    out.append("")
    return out


def run(day: date, source: str | None = None) -> str:
    logging.disable(logging.INFO)
    source = source or best_source(day)
    if not source:
        raise SystemExit(f"no recorded ticks for {day}")
    insts, meta = load_instruments(source, day)
    ticks = list(read_ticks(source, day))
    spot = [t.ltp for t in ticks if insts.get(t.token) and insts[t.token].kind == "IDX"]
    out = [f"# Scalper condition report - {day} ({source})", "",
           f"{len(ticks):,} ticks, {len(insts)} instruments, expiry {meta.get('expiry')}. "
           f"NIFTY range {min(spot):,.0f} - {max(spot):,.0f} ({max(spot) - min(spot):,.0f} pts)." if spot else "", ""]
    for sid, cls in SCALP_STRATEGIES.items():
        out += _section(cls.name, *_replay(sid, insts, ticks))
    out += ["# Experimental options (not live)", ""]
    for label, sid, overrides, attrs in EXPERIMENTS:
        out += _section(label, *_replay(sid, insts, ticks, overrides, attrs))
    return "\n".join(out)


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--source")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    text = run(date.fromisoformat(a.date), a.source)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
    print(text)


if __name__ == "__main__":
    main(sys.argv[1:])
