"""Order-flow scalpers S1-S4 (ported rule-for-rule from nifty_orderflow.py).

Each is an independent ScalpEngine: own position, own 3-trades / 2-losses
daily limits. All windows are in minutes of closed 15-second buckets.
Rules are documented in docs/scalping/STRATEGIES.md.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Optional

from scalp_strategies.engine import ScalpConfig, ScalpEngine, Series

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
    """S2 - big-print breakout: spot coils (<= BOX_PTS over 15 min), then closes out of the box on an
    option showing institutional-size prints and a volume spike.

    Recalibrated from the original "CVD / stealth accumulation" rules, which never fired: a <=20-pt box,
    CVD >= 20% of volume and dOI >= 15% of volume are each ~2% likely, so their conjunction was ~never,
    and the tight box did not predict good breakouts. On the recorded week the one predictive filter was
    big prints (>= 3 large-LTQ trades): it lifted the +20%-before-stop rate from 26% (all breakouts) to
    50%. So S2 now trades a wider <=40-pt box confirmed by big prints + a 2x volume spike, at half
    balance, and drops the CVD / dOI gates. Four non-expiry days: 0 trades -> +Rs 1,138 (3 of 4 days up),
    and it wins on 2026-10-05 where S3 loses. Still regime-sensitive (breakouts fail in chop) - the
    eventual gate is the same trend-day filter S3 wants.
    """

    strategy_id = "scalp_stealth_accum"
    name = "S2 Stealth Accumulation (Big-Print Breakout)"

    BOX_PTS = 40
    MIN_BIG = 3            # big (>= 5x average LTQ) prints in the last 3 min on the breakout option
    VOL_MULT = 2.0         # 1-min volume vs the 15-min average
    MIN_BOX_SAMPLES = 120  # 30 minutes of evaluations before the relative box limit is trusted

    @classmethod
    def default_config(cls) -> ScalpConfig:
        return ScalpConfig(deploy_pct=0.5)  # half balance: the breakout edge is thin, keep risk down

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
                m3 = o.n(3)
                if o.vol(o.n(15)) > 0 and o.big(m3) >= self.MIN_BIG and o.vol_ratio(1, 15) >= self.VOL_MULT:
                    return kind, k, f"box {bh - bl:.0f}pt broken, {o.big(m3)} big prints, vol {o.vol_ratio(1, 15):.1f}x"
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

    @classmethod
    def default_config(cls) -> ScalpConfig:
        # Sized down from the shared default: on the recorded week S3's OI signal was marginal in the
        # choppy regime, so it is run at 1/3 of balance with a wider -20% stop and a +50% target (give
        # the 15-30 min signal room). This turned the four non-expiry days from -Rs 12k to about +Rs 5k
        # and cut the worst day from -Rs 12k to -Rs 5k. The real fix is a trend-day gate (needs more
        # data); until then this is capital protection. Other scalpers keep whole-balance compounding.
        return ScalpConfig(deploy_pct=0.33, sl_pct=0.20, target_pct=0.50)

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
    """S4 - trend-aligned trap: spot pokes out of its 15-minute range AGAINST the last hour's direction
    and falls back inside; buy the option on the hour's side (a failed counter-trend breakout).

    Recalibrated from the original rules (fake breakout + "futures OI flat" + breakout-side writers
    +3% OI + 2x volume + the opposite option at a 1-minute high), which traded twice in a week and lost
    both. On the recorded week "futures OI flat" held 74-99% of the time (no information), +3% ATM OI
    in 3 minutes held 10-20% of the time and did not predict the fade, and the bare poke-and-fall-back
    was a coin flip: it won on range days and lost on trend days, because half the fades were bets
    against an established move. Splitting the same traps by the futures' 60-minute efficiency ratio
    (net move / path length, signed towards the trade) separated them: fades against the hour averaged
    about -1% per trade, fades WITH the hour about +10% (win rate ~70-80%, up on all four non-expiry
    days), and the result held across 10-20 minute ranges, 2-5 minute pokes and ratio floors of
    0.00-0.04. A trend-only entry without the trap made about +4%, so the trap is the timing edge.
    The move takes 15-45 minutes, hence the wide stop/target, no trail and a 45-minute time exit.
    """

    strategy_id = "scalp_trap_fade"
    name = "S4 Trap Fade"

    RANGE_MIN = 15    # the range that gets poked
    POKE_MIN = 2      # the poke and the fall-back happen within this many minutes
    TREND_MIN = 60    # the futures' efficiency ratio is measured over this window
    MIN_ER = 0.02     # minimum efficiency ratio in the trade's direction

    @classmethod
    def default_config(cls) -> ScalpConfig:
        # Half balance with a -20% stop risks ~10% of the balance per trade (as S1 does at full size
        # with a -10% stop). The 45-minute time exit is unconditional (the gain floor is unreachable)
        # and trailing is off: the trend leg needs room, and a trail cut the winners short.
        return ScalpConfig(deploy_pct=0.5, sl_pct=0.20, target_pct=0.40, trail_trigger=9.0,
                           time_stop_min=45, time_stop_min_gain=9.0)

    def trend(self) -> Optional[float]:
        """Futures efficiency ratio over TREND_MIN: +1 = straight up, -1 = straight down, ~0 = chop."""
        L = self.S(self.fut_tok).buckets
        k = self.S(self.fut_tok).n(self.TREND_MIN)
        if len(L) <= k:
            return None
        c = [L[i].c for i in range(len(L) - k - 1, len(L))]
        path = sum(abs(b - a) for a, b in zip(c, c[1:]))
        return (c[-1] - c[0]) / path if path else 0.0

    def signal(self, ts: datetime, spot: float, atm: float) -> Signal:
        if not self.fut_tok:
            return None
        sp = self.S(self.spot_tok)
        mp, mr = sp.n(self.POKE_MIN), sp.n(self.RANGE_MIN)
        prior, recent = sp.win(mr + mp, mp), sp.win(mp)
        if len(prior) < mr:
            return None
        er = self.trend()
        if er is None:
            return None
        H, Lo, c = max(b.h for b in prior), min(b.l for b in prior), sp.last().c
        if max(b.h for b in recent) > H and c < H and -er >= self.MIN_ER and self.tok(atm, "PE"):
            return "PE", atm, f"bull trap above {H:.0f} in a falling hour (trend {er:+.2f})"
        if min(b.l for b in recent) < Lo and c > Lo and er >= self.MIN_ER and self.tok(atm, "CE"):
            return "CE", atm, f"bear trap below {Lo:.0f} in a rising hour (trend {er:+.2f})"
        return None
