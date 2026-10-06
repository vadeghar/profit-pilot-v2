"""OI + Volume Burst scalper - NIFTY ATM options (rules in docs/scalping/STRATEGIES.md).

Candidates: ATM CE/PE plus one ITM and one OTM strike on each side.
Entry (all true on a closed 15-second bucket, evaluated on the rolling 1-minute bar):
  1. volume spike: last 1-minute volume >= 3x the average of the previous twenty 1-minute bars,
     and an LTQ burst: >= 5 traded ticks within 10 s, each with LTQ >= 5x the running average LTQ;
  2. long buildup (price up, OI up) or short covering (price up, OI down) on the traded strike over 3 min;
  3. the opposite-side ATM option's OI falls >= 2% over 3 min (writers unwinding);
  4. LTP above the option's VWAP.
Optional (off by default, as it is listed as a monitored signal, not an entry rule):
  LTP breaking the previous 5-minute high.
Exits: target +20%, stop -10%, trail to the previous 1-minute low, exit on LTP below VWAP,
5-minute time stop, 15:00 hard exit. Risk: 3 trades / day, stop after 2 losses; sizing compounds the whole balance (engine default).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from scalp_strategies.engine import ScalpConfig, ScalpEngine


class OiVolumeBurst(ScalpEngine):
    strategy_id = "scalp_oi_volume_burst"
    name = "OI + Volume Burst"

    EXPIRY_MODE = "skip"  # calibrated off-expiry; the expiry cards handle expiry day
    VOL_SPIKE = 3.0
    BASE_MIN = 20
    BURST_TICKS = 5
    BURST_WINDOW_S = 10
    BURST_LTQ_MULT = 5.0
    OI_WINDOW_MIN = 3
    OPP_UNWIND = -0.02
    REQUIRE_BREAKOUT = False

    @classmethod
    def default_config(cls) -> ScalpConfig:
        return ScalpConfig(entry_start="09:20", entry_end="14:45", square_off="15:00", max_trades=3, max_losses=2,
                           sl_pct=0.10, target_pct=0.20, trail_trigger=9.99, time_stop_min=5,
                           time_stop_min_gain=0.05, vwap_exit=True, trail_prev_minute_low=True)

    def signal(self, ts: datetime, spot: float, atm: float) -> Optional[tuple[str, float, str]]:
        step = self.cfg.strike_step
        best = None
        for kind, sg in (("CE", 1), ("PE", -1)):
            opp_tok = self.tok(atm, "PE" if kind == "CE" else "CE")
            if not opp_tok:
                continue
            opp = self.S(opp_tok)
            m3 = opp.n(self.OI_WINDOW_MIN)
            if not opp.has(m3 + 1) or opp.doi(m3) > self.OPP_UNWIND:
                continue  # rule 3: opposite-side writers must be unwinding
            # ITM, ATM, OTM for this side (a call is ITM below spot, a put above)
            for k in (atm - sg * step, atm, atm + sg * step):
                t = self.tok(k, kind)
                if not t:
                    continue
                o = self.S(t)
                if not o.has(o.n(self.BASE_MIN) + o.n(1)):
                    continue
                ratio = o.vol_ratio(1, self.BASE_MIN)
                if ratio < self.VOL_SPIKE:
                    continue
                if o.big_prints_since(ts - timedelta(seconds=self.BURST_WINDOW_S), self.BURST_LTQ_MULT) < self.BURST_TICKS:
                    continue
                dpx, doi = o.dpx(m3), o.doi_abs(m3)
                if dpx <= 0:
                    continue  # rule 2: price down = short buildup -> avoid
                buildup = "long buildup" if doi > 0 else "short covering"
                if o.last().c <= o.vwap():
                    continue
                if self.REQUIRE_BREAKOUT and o.last().c <= o.hh(o.n(5), 1):
                    continue
                if best is None or ratio > best[0]:
                    why = (f"vol {ratio:.1f}x, LTQ burst, {buildup} (dOI {o.doi(m3):+.1%}), "
                           f"opp ATM OI {opp.doi(m3):+.1%}, > VWAP")
                    best = (ratio, kind, k, why)
        return (best[1], best[2], best[3]) if best else None
