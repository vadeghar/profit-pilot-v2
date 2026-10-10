"""Expiry Trend Breakout - option buying on the weekly expiry day only (docs/scalping/STRATEGIES.md).

From a study of every NIFTY and SENSEX weekly expiry from Sep-2025 to Sep-2026 on 1-minute data
(scalp_strategies/research/expiry_patterns.py): on an expiry day that is already trending, a fresh day high or
low after 11:00 tends to run, and a near-the-money option bought there doubled about one time in three.

Entry (checked on each completed 1-minute close of the index, 11:00-14:30):
  1. today is the expiry day of the recorded option chain;
  2. the index's range so far today (high - low of its 1-minute closes since 09:15) is >= 0.5%;
  3. this minute closes above that high -> buy a CE, below that low -> buy a PE;
  4. strike by price: the option trading nearest Rs 40 (within Rs 20-64);
  5. open interest agrees: over the ATM +/- 4 strikes, put OI exceeds call OI for a CE (put writers
     underneath), call OI exceeds put OI for a PE. A breakout that fails this is skipped and the next
     new high/low is checked again.
One trade per direction per day. Exits: stop -30%, target +100%, square-off at 15:10 (the index
stops updating at 15:15 for the closing auction session, so nothing is held into it).

Rules 5 and the 14:30 cut-off were added after re-studying the same NIFTY expiries (2026-10-10): entries
after 14:30 lost (too little time left to double), and every breakout taken against the OI balance was
stopped out; the OI rule improved the average in all 30 start-time x range variants tried and in both
halves of the year. SENSEX history has no OI, so rule 5 is verified on NIFTY only.
Sizing: 25% of the current balance per trade - losing streaks of 6+ occurred in the study.

The day's range is rebuilt from live ticks, so a service restart during an expiry session loses the
morning's high/low and the range restarts from that moment.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Optional

from scalp_strategies.engine import ScalpConfig, ScalpEngine


class ExpiryTrendBreakout(ScalpEngine):
    strategy_id = "scalp_expiry_breakout"
    name = "Expiry Trend Breakout"

    AUTO_START = True            # paper session starts with the app; it only trades on expiry days
    EXPIRY_MODE = "only"         # never enters outside the option chain's expiry day
    SESSION_OPEN = time(9, 15)
    MIN_RANGE = 0.005            # day range (share of spot) required before a breakout counts
    PREMIUM = 40.0
    PREMIUM_BAND = (0.5, 1.6)    # accepted premium as a multiple of PREMIUM
    OI_STRIKES = 4               # put/call OI balance is summed over ATM +/- this many strikes

    @classmethod
    def default_config(cls) -> ScalpConfig:
        return ScalpConfig(entry_start="11:00", entry_end="14:30", square_off="15:10", max_trades=2, max_losses=2,
                           cooldown_min=0, sl_pct=0.30, target_pct=1.00, trail_trigger=9.99, time_stop_min=999,
                           deploy_pct=0.25)

    def _new_day(self, day: str, ts: datetime) -> None:
        super()._new_day(day, ts)
        self._hi: Optional[float] = None   # high/low of the index's 1-minute closes so far today
        self._lo: Optional[float] = None
        self._break: Optional[str] = None  # "CE"/"PE" when the minute that just closed made a new extreme
        self._range = 0.0                  # range before that minute, as a share of spot
        self._done: set[str] = set()

    def _on_bucket(self, ts: datetime) -> None:
        self._break = None
        sp = self.S(self.spot_tok) if self.spot_tok else None
        minute_closed = ts.second < self.cfg.bucket_sec and ts.time() > self.SESSION_OPEN
        if sp and sp.buckets and minute_closed:
            c = sp.last().c
            if self._hi is not None:
                self._range = (self._hi - self._lo) / c
                self._break = "CE" if c > self._hi else "PE" if c < self._lo else None
            self._hi = c if self._hi is None else max(self._hi, c)
            self._lo = c if self._lo is None else min(self._lo, c)
        super()._on_bucket(ts)

    def pick_strike(self, kind: str) -> Optional[float]:
        """Strike of ``kind`` whose last price is nearest PREMIUM, if any trades inside the band."""
        lo, hi = (m * self.PREMIUM for m in self.PREMIUM_BAND)
        prices = {k: self.S(t).ltp for (k, kd), t in self.opt.items() if kd == kind}
        near = {k: p for k, p in prices.items() if lo <= p <= hi}
        return min(near, key=lambda k: abs(near[k] - self.PREMIUM)) if near else None

    def oi_balance(self, atm: float) -> Optional[float]:
        """Put OI / call OI over ATM +/- OI_STRIKES; None when either side has no OI yet."""
        step = self.cfg.strike_step
        tot = {"CE": 0.0, "PE": 0.0}
        for i in range(-self.OI_STRIKES, self.OI_STRIKES + 1):
            for kind in tot:
                t = self.tok(atm + i * step, kind)
                if t:
                    tot[kind] += self.S(t).oi
        return tot["PE"] / tot["CE"] if tot["CE"] > 0 and tot["PE"] > 0 else None

    def oi_agrees(self, kind: str, atm: float) -> bool:
        """A CE needs more put OI than call OI around the money (writers underneath); a PE the reverse."""
        bal = self.oi_balance(atm)
        return bal is not None and (bal > 1 if kind == "CE" else bal < 1)

    def signal(self, ts: datetime, spot: float, atm: float) -> Optional[tuple[str, float, str]]:
        kind = self._break
        if not kind or kind in self._done or self._range < self.MIN_RANGE or not self.is_expiry_day():
            return None
        strike = self.pick_strike(kind)
        if strike is None or not self.oi_agrees(kind, atm):
            return None  # not marked done: a later new extreme with the OI balance on its side still trades
        self._done.add(kind)
        side = "high" if kind == "CE" else "low"
        return kind, strike, (f"expiry day: new day {side} {spot:,.1f} after a {self._range:.2%} range, "
                              f"put/call OI {self.oi_balance(atm):.2f}, "
                              f"{strike:.0f}{kind} at Rs {self.S(self.tok(strike, kind)).ltp:.2f}")
