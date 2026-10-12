"""Expiry Closing Auction - option buying during the closing auction on the weekly expiry day
(rules in docs/scalping/STRATEGIES.md; source: the user's "NIFTY CAS Option-Buying Strategy").

The index stops updating at 15:15 while its constituents go through the closing auction; futures and
options keep trading until 15:40, and the expiring options settle against the auction's closing print
(about 15:29). The card reads the futures as a live proxy for that print.

Baseline at 15:15: F0 = futures VWAP over 15:10-15:15, S0 = the frozen index level.
Evaluated once a second on the future, 15:21-15:28, flat, expiry day only (D = 90-second futures VWAP - F0):
  1. dislocation: |D| >= MIN_DISLOCATION index points (up -> CE, down -> PE);
  2. momentum: the future moved >= 0.03% that way over the last 5 seconds;
  3. order flow: (aggressive buys - aggressive sells) / both over the last 10 seconds >= 0.25 that way
     (a trade at or above the previous snapshot's ask is a buy, at or below its bid a sell);
  4. the future is on that side of its own 60-second VWAP;
  5. the ATM option (strike nearest S0) is quoted with a spread <= 0.75% of its mid-price.
One trade a day, one lot. Exits: stop -25%, target +37.5% (1.5 x the risk), stop lifted to the entry
once the premium is up 25%, everything flat at 15:35 (the option trades at its settled value from the
print until 15:40).

Left out of the source spec: the Black-Scholes fair-value filter (no option model here), the "option
not up more than 25% since 15:15" filter (an expiring ATM option routinely moves 50%+ in these minutes;
it would have blocked the one recorded winner) and the flow-flip / VWAP-failure / spread-expansion exits.

Evidence is one tick-recorded expiry (NIFTY 2026-10-06: CE at 15:24:59, target in under 3 minutes); on
five non-expiry days the same signal fired once and lost, hence expiry only. SENSEX runs the same rules
with the threshold scaled to its level and is untested: its futures may be too thin for rules 2-3.
Every fill, rejection and a per-day summary of the window are emitted as "signal" events so the
thresholds can be calibrated from real sessions. Charges use the NSE option table for both indices.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, time
from typing import Optional

from market_data.tick_store import Tick
from scalp_strategies.engine import TICK_SIZE, ScalpConfig, ScalpEngine, hhmm


class ExpiryCloseAuction(ScalpEngine):
    strategy_id = "scalp_expiry_auction"
    name = "Expiry Closing Auction (NIFTY)"

    AUTO_START = True            # paper session starts with the app; it only trades on expiry days
    EXPIRY_MODE = "only"
    BASE_START, BASE_END = time(15, 10), time(15, 15)
    MIN_DISLOCATION = 20.0       # index points
    MIN_MOMENTUM = 0.0003        # futures return over MOMENTUM_S
    MIN_OFI = 0.25
    VWAP_S, SIDE_VWAP_S, MOMENTUM_S, FLOW_S = 90, 60, 5, 10
    MAX_SPREAD = 0.0075          # of the option's mid-price (never tighter than two ticks)
    REJECT_LOG_S = 15            # a blocked trigger is logged at most this often

    @classmethod
    def default_config(cls) -> ScalpConfig:
        return ScalpConfig(entry_start="15:21", entry_end="15:28", square_off="15:35", max_trades=1, max_losses=1,
                           cooldown_min=0, sl_pct=0.25, target_pct=0.375, trail_trigger=9.99, time_stop_min=999,
                           breakeven_trigger=0.25, sizing="fixed", fixed_lots=1)

    # ------------------------------------------------------------------ state
    def _new_day(self, day: str, ts: datetime) -> None:
        super()._new_day(day, ts)
        self._base_pv = self._base_v = 0.0
        self._frozen = False
        self._f0: Optional[float] = None      # futures VWAP 15:10-15:15
        self._s0 = 0.0                        # index level frozen at 15:15
        self._ref: dict[str, float] = {}      # option prices at 15:15
        self._fut: deque = deque()            # (ts, ltp, traded volume, aggression flag) per futures snapshot
        self._prev: Optional[tuple] = None    # (cumulative volume, bid, ask) of the previous futures snapshot
        self._last_eval: Optional[datetime] = None
        self._last_reject: Optional[datetime] = None
        self._summarised = False
        self._win_end = hhmm(self.cfg.entry_end)
        self._stats = {"evaluations": 0, "dislocation": 0, "momentum": 0, "flow": 0, "vwap_side": 0, "all": 0,
                       "d_min": 0.0, "d_max": 0.0}

    def signal(self, ts: datetime, spot: float, atm: float) -> None:
        return None  # evaluated on futures ticks (once a second), not on closed buckets

    # ------------------------------------------------------------- tick input
    def on_tick(self, t: Tick) -> None:
        super().on_tick(t)
        if t.token not in self.insts or t.ltp <= 0:
            return
        if t.token == self.fut_tok:
            self._track(t)
            if not self._frozen and t.ts.time() >= self.BASE_END:
                self._freeze()
            if self.pos is None and self._f0 and self.can_enter(t.ts):
                if self._last_eval is None or (t.ts - self._last_eval).total_seconds() >= 1:
                    self._last_eval = t.ts
                    self._evaluate(t.ts)
        if not self._summarised and t.ts.time() > self._win_end:
            self._summarised = True
            if self.is_expiry_day():
                self._summarise(t.ts)

    def _track(self, t: Tick) -> None:
        prev = self._prev
        dv = max(t.volume - prev[0], 0) if prev and t.volume else 0
        flag = 0
        if dv:
            flag = 1 if prev[2] > 0 and t.ltp >= prev[2] else -1 if prev[1] > 0 and t.ltp <= prev[1] else 0
            if self.BASE_START <= t.ts.time() < self.BASE_END:
                self._base_pv += t.ltp * dv
                self._base_v += dv
        self._fut.append((t.ts, t.ltp, dv, flag))
        while self._fut and (t.ts - self._fut[0][0]).total_seconds() > self.VWAP_S + 30:
            self._fut.popleft()
        self._prev = (t.volume or (prev[0] if prev else 0), t.bid, t.ask)

    def _freeze(self) -> None:
        self._frozen = True
        self._f0 = self._base_pv / self._base_v if self._base_v else None
        self._s0 = self.S(self.spot_tok).ltp if self.spot_tok else 0.0
        self._ref = {tok: self.S(tok).ltp for tok in self.opt.values() if self.S(tok).ltp > 0}

    # --------------------------------------------------------------- trigger
    def _evaluate(self, ts: datetime) -> None:
        age = lambda at: (ts - at).total_seconds()
        ltp = self._fut[-1][1]
        v90 = sum(dv for at, _p, dv, _f in self._fut if age(at) <= self.VWAP_S)
        v60 = sum(dv for at, _p, dv, _f in self._fut if age(at) <= self.SIDE_VWAP_S)
        past = next((p for at, p, _dv, _f in reversed(self._fut) if age(at) >= self.MOMENTUM_S), None)
        if not v90 or not v60 or past is None or self._s0 <= 0:
            return
        vwap90 = sum(p * dv for at, p, dv, _f in self._fut if age(at) <= self.VWAP_S) / v90
        vwap60 = sum(p * dv for at, p, dv, _f in self._fut if age(at) <= self.SIDE_VWAP_S) / v60
        buy = sum(dv for at, _p, dv, f in self._fut if f > 0 and age(at) <= self.FLOW_S)
        sell = sum(dv for at, _p, dv, f in self._fut if f < 0 and age(at) <= self.FLOW_S)
        d = vwap90 - self._f0
        momentum = ltp / past - 1
        ofi = (buy - sell) / (buy + sell) if buy + sell else 0.0
        sg = 1 if d > 0 else -1
        ok = {"dislocation": sg * d >= self.MIN_DISLOCATION, "momentum": sg * momentum >= self.MIN_MOMENTUM,
              "flow": sg * ofi >= self.MIN_OFI, "vwap_side": sg * (ltp - vwap60) > 0}
        st = self._stats
        st["evaluations"] += 1
        for k, v in ok.items():
            st[k] += v
        st["d_min"], st["d_max"] = min(st["d_min"], d), max(st["d_max"], d)
        if not all(ok.values()):
            return
        st["all"] += 1
        kind = "CE" if sg > 0 else "PE"
        step = self.cfg.strike_step
        strike = float(round(self._s0 / step) * step)
        tok = self.tok(strike, kind)
        se = self.S(tok) if tok else None
        mid = (se.bid + se.ask) / 2 if se and se.bid > 0 and se.ask > 0 else 0.0
        if not mid:
            outcome = "NO_QUOTE"
        elif se.ask - se.bid > max(self.MAX_SPREAD * mid, 2 * TICK_SIZE) + 1e-9:
            outcome = "REJECTED_SPREAD"
        else:
            outcome = "FILLED"
        if outcome != "FILLED":
            if self._last_reject and (ts - self._last_reject).total_seconds() < self.REJECT_LOG_S:
                return
            self._last_reject = ts
        rec = {"type": "signal", "time": ts.isoformat(), "outcome": outcome, "kind": kind, "strike": strike,
               "symbol": self.insts[tok].symbol if tok else None, "dislocation": round(d, 2),
               "momentum_pct": round(momentum * 100, 4), "ofi": round(ofi, 2), "future": ltp,
               "future_base_vwap": round(self._f0, 2), "index_frozen": self._s0,
               "option_implied_close": self._implied(strike), "bid": se.bid if se else 0, "ask": se.ask if se else 0,
               "run_since_1515": round(se.ltp / self._ref[tok] - 1, 4) if se and self._ref.get(tok) else None}
        if outcome == "FILLED":
            why = (f"expiry auction: futures {d:+.1f} pts vs the 15:10-15:15 VWAP, {momentum:+.3%} in "
                   f"{self.MOMENTUM_S}s, order flow {ofi:+.2f}")
            self._enter(ts, kind, strike, why)
            if self.pos:
                self.signals_seen += 1
                rec["fill"] = round(self.pos.entry, 2)
            else:
                rec["outcome"] = "SKIPPED_NO_CAPITAL"
        self._emit("signal", rec)

    def _implied(self, strike: float) -> Optional[float]:
        """Close implied by the expiring ATM options: strike + CE mid - PE mid (logged for later study)."""
        ce, pe = self.tok(strike, "CE"), self.tok(strike, "PE")
        if not ce or not pe:
            return None
        c, p = self.S(ce), self.S(pe)
        if not (c.ask > 0 and p.ask > 0):
            return None
        return round(strike + (c.bid + c.ask) / 2 - (p.bid + p.ask) / 2, 2)

    def _summarise(self, ts: datetime) -> None:
        """One record per expiry day: how close the window came, traded or not."""
        st = self._stats
        step = self.cfg.strike_step
        self._emit("signal", {"type": "window", "time": ts.isoformat(), "index_frozen": self._s0,
                              "future_base_vwap": round(self._f0, 2) if self._f0 else None,
                              "base_volume": int(self._base_v), "seconds_evaluated": st["evaluations"],
                              "seconds_passed": {k: st[k] for k in ("dislocation", "momentum", "flow", "vwap_side", "all")},
                              "dislocation_range": [round(st["d_min"], 2), round(st["d_max"], 2)],
                              "option_implied_close": self._implied(float(round(self._s0 / step) * step))
                              if self._s0 else None, "traded": self.day_trades})


class ExpiryCloseAuctionSensex(ExpiryCloseAuction):
    strategy_id = "scalp_expiry_auction_sensex"
    name = "Expiry Closing Auction (SENSEX)"

    UNDERLYING = "SENSEX"
    MIN_DISLOCATION = 65.0       # the NIFTY threshold scaled to the SENSEX level (about 3.3x)

    @classmethod
    def default_config(cls) -> ScalpConfig:
        cfg = super().default_config()
        cfg.strike_step = 100
        return cfg
