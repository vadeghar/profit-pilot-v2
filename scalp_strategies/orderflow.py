"""Order-flow scalpers S1-S4 (ported rule-for-rule from nifty_orderflow.py).

Each is an independent ScalpEngine: own position, own 3-trades / 2-losses
daily limits. All windows are in minutes of closed 15-second buckets.
Rules are documented in docs/scalping/STRATEGIES.md.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Optional

from scalp_strategies.engine import ScalpEngine, Series

Signal = Optional[tuple[str, float, str]]


class _OrderFlowBase(ScalpEngine):
    EXPIRY_MODE = "skip"  # S1-S4 were calibrated off-expiry; the expiry cards handle expiry day

    def ready(self, se: Series) -> bool:
        return se.has(se.n(15) + se.n(1))


class WriterSqueeze(_OrderFlowBase):
    """S1 - high-OI wall breach: call (put) writers flee while the opposite side is written."""

    strategy_id = "scalp_writer_squeeze"
    name = "S1 Writer Squeeze (OI Wall Breach)"

    def signal(self, ts: datetime, spot: float, atm: float) -> Signal:
        if not self.fut_tok:
            return None
        step = self.cfg.strike_step
        f = self.S(self.fut_tok)
        for kind, sg in (("CE", 1), ("PE", -1)):
            opp = "PE" if kind == "CE" else "CE"
            walls = [(k, self.tok(k, kind)) for k in (atm + sg * i * step for i in range(4))]
            walls = [(k, t) for k, t in walls if t and sg * (k - spot) >= -10]
            if not walls:
                continue
            k, t = max(walls, key=lambda x: self.S(x[1]).oi)
            if abs(k - spot) > 20 or not self.tok(k, opp) or not self.tok(atm, kind):
                continue
            w, o, a = self.S(t), self.S(self.tok(k, opp)), self.S(self.tok(atm, kind))
            if not (self.ready(w) and self.ready(a)):
                continue
            m3, m5 = a.n(3), a.n(5)
            if (w.doi(m3) <= -0.03 and o.doi(m3) > 0 and a.vol_ratio(1, 15) >= 2.5
                    and a.last().c > a.hh(m5, 1) and a.last().c > a.vwap()
                    and sg * f.dpx(m3) > 0 and f.doi(m3) > 0):
                return kind, atm, f"wall {k:.0f}{kind} dOI {w.doi(m3):.1%}, opp {opp} dOI {o.doi(m3):+.1%}"
        return None


class StealthAccumulation(_OrderFlowBase):
    """S2 - spot boxed in 20 points while an ATM/next option is quietly accumulated, then breaks out."""

    strategy_id = "scalp_stealth_accum"
    name = "S2 Stealth Accumulation (CVD Breakout)"

    BOX_PTS = 20
    MIN_BOX_SAMPLES = 120  # 30 minutes of evaluations before the relative box limit is trusted

    def _new_day(self, day: str, ts: datetime) -> None:
        super()._new_day(day, ts)
        self._ranges: list[float] = []  # 15-minute spot range seen at each evaluation today

    def box_limit(self) -> Optional[float]:
        """Widest spot box that still counts as 'boxed'; None while the relative limit lacks history."""
        if self.cfg.box_rel <= 0:
            return self.BOX_PTS
        if len(self._ranges) < self.MIN_BOX_SAMPLES:
            return None
        return max(self.BOX_PTS, self.cfg.box_rel * median(self._ranges))

    def signal(self, ts: datetime, spot: float, atm: float) -> Signal:
        sp = self.S(self.spot_tok)
        m15 = sp.n(15)
        box = sp.win(m15 + 1, 1)
        if len(box) < m15:
            return None
        bh, bl = max(b.h for b in box), min(b.l for b in box)
        self._ranges.append(bh - bl)
        limit = self.box_limit()
        if limit is None or bh - bl > limit:
            return None
        c = sp.last().c
        step = self.cfg.strike_step
        for kind, sg, brk in (("CE", 1, c > bh), ("PE", -1, c < bl)):
            if not brk:
                continue
            for k in (atm, atm + sg * step):
                t = self.tok(k, kind)
                if not t or not self.ready(self.S(t)):
                    continue
                o = self.S(t)
                cvd, v = o.cvd(o.n(15)), o.vol(o.n(15))
                m3 = o.n(3)
                if (v and cvd >= 0.2 * v and o.big(m3) >= 3 and o.vol_ratio(1, 15) >= 2
                        and abs(o.doi_abs(m3)) >= 0.15 * max(o.vol(m3), 1)):
                    return kind, k, f"box {bl:.0f}-{bh:.0f} broken, CVD {cvd / v:.0%} of volume"
        return None


class PcrVelocity(_OrderFlowBase):
    """S3 - two consecutive 3-minute windows of call-OI unwinding + put-OI building (or the reverse).

    The latest window's unwind must be at least MIN_UNWIND_RATIO of the build, and both legs must
    actually move (MIN_TWO_SIDED_RATIO): on 2026-10-01 a losing entry had puts shedding 28,860 OI
    against 408,590 written on calls (7%); on 2026-10-06 one had calls unwinding 4.28M while puts
    built only 117k (3%) - a one-sided OI shift, not the two-sided unwind+build the strategy trades.
    """

    strategy_id = "scalp_pcr_velocity"
    name = "S3 Delta-PCR Velocity"

    MIN_UNWIND_RATIO = 0.2    # unwinding side's 3-min OI drop as a share of the building side's OI rise
    MIN_TWO_SIDED_RATIO = 0.2  # the smaller OI leg must be at least this x the larger (both sides move)

    def signal(self, ts: datetime, spot: float, atm: float) -> Signal:
        if not self.fut_tok:
            return None
        step = self.cfg.strike_step
        ks = [atm + i * step for i in range(-2, 3)]

        def d(kind: str, lag: int) -> Optional[float]:
            tot = 0.0
            for k in ks:
                t = self.tok(k, kind)
                if not t:
                    return None
                L = self.S(t).buckets
                m3 = self.S(t).n(3)
                if len(L) <= lag + m3:
                    return None
                tot += L[-1 - lag].oi - L[-1 - lag - m3].oi
            return tot

        m3 = self.S(self.spot_tok).n(3)
        ce0, pe0, ce1, pe1 = d("CE", 0), d("PE", 0), d("CE", m3), d("PE", m3)
        if None in (ce0, pe0, ce1, pe1):
            return None
        if ce0 < 0 and ce1 < 0 and pe0 > 0 and pe1 > 0:
            kind, sg = "CE", 1
        elif pe0 < 0 and pe1 < 0 and ce0 > 0 and ce1 > 0:
            kind, sg = "PE", -1
        else:
            return None
        unwind, build = (ce0, pe0) if kind == "CE" else (pe0, ce0)
        if abs(unwind) < self.MIN_UNWIND_RATIO * build:
            return None  # a token unwind against heavy writing is noise, not a shift
        if min(abs(unwind), abs(build)) < self.MIN_TWO_SIDED_RATIO * max(abs(unwind), abs(build)):
            return None  # one-sided OI shift: one leg barely moved, so it is not a real PCR rotation
        t = self.tok(atm, kind)
        if not t:
            return None
        a, f = self.S(t), self.S(self.fut_tok)
        pcr = pe0 / ce0 if ce0 else float("inf")
        if self.ready(a) and a.vol_ratio(1, 15) >= 2 and a.last().c > a.vwap() and sg * f.dpx(a.n(3)) > 0:
            return kind, atm, f"dPCR(3m) {pcr:.2f}, CE dOI {ce0:+,.0f} / PE dOI {pe0:+,.0f}"
        return None


class TrapFade(_OrderFlowBase):
    """S4 - fake breakout of the 15-minute range with no futures OI support: buy the opposite side."""

    strategy_id = "scalp_trap_fade"
    name = "S4 Trap Fade"

    def signal(self, ts: datetime, spot: float, atm: float) -> Signal:
        if not self.fut_tok or not self.tok(atm, "CE") or not self.tok(atm, "PE"):
            return None
        sp, f = self.S(self.spot_tok), self.S(self.fut_tok)
        m2, m15 = sp.n(2), sp.n(15)
        prior, recent = sp.win(m15 + m2, m2), sp.win(m2)
        if len(prior) < m15:
            return None
        H, Lo, c = max(b.h for b in prior), min(b.l for b in prior), sp.last().c
        ce, pe = self.S(self.tok(atm, "CE")), self.S(self.tok(atm, "PE"))
        if not (self.ready(ce) and self.ready(pe)):
            return None
        m3, m1 = ce.n(3), ce.n(1)
        if (max(b.h for b in recent) > H and c < H and f.doi(m3) <= 0.001
                and ce.doi(m3) >= 0.03 and ce.vol_ratio(1, 15) >= 2 and pe.last().c > pe.hh(m1, 1)):
            return "PE", atm, f"bull trap above {H:.0f}: CE writers +{ce.doi(m3):.1%} OI, futures OI flat"
        if (min(b.l for b in recent) < Lo and c > Lo and f.doi(m3) <= 0.001
                and pe.doi(m3) >= 0.03 and pe.vol_ratio(1, 15) >= 2 and ce.last().c > ce.hh(m1, 1)):
            return "CE", atm, f"bear trap below {Lo:.0f}: PE writers +{pe.doi(m3):.1%} OI, futures OI flat"
        return None
