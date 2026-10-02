"""Expiry Gamma Squeeze - order-flow option buying on the weekly expiry day (spec v2.0, docs/scalping/STRATEGIES.md).

Two stages, 13:15-14:50 on the expiry day only:

Regime (re-checked on every closed 15-second bucket, per CE/PE strike):
  * the strike's premium is Rs 12-25;
  * its own open interest fell >= 1.5% over the last 15 minutes (writers covering);
  * the near-month future trades above its VWAP for a CE, below it for a PE.
  Each strike in regime gets a resting stop-limit buy: trigger = its 5-minute high + 0.20,
  limit = trigger + 0.40.

Trigger (checked on every snapshot of an armed strike):
  * LTP reaches the trigger;
  * the last 60 s of traded volume is more than 2x the average minute of the previous 15 minutes;
  * ask aggression over the last 30 s is >= 60%: the share of traded volume in snapshots whose LTP was at or
    above the previous snapshot's best ask (Angel sends ~1 snapshot a second, not every trade).
  The buy fills at the ask (+ slippage ticks) only if that is within the limit.

Exits: target +100%, stop -35% (market), stop lifted to entry + 0.50 once the premium is up 40%, out after
8 minutes if it never reached +25%, everything flat at 15:10. Up to 3 trades a day, none after 2 consecutive
stop-outs, 10-minute cooldown after an exit. Sizing: one lot.

Every evaluated trigger is reported as a "signal" event (quotes, flow numbers and the outcome), each trade with
its worst and best excursion, and a breakeven exit is followed to see what the trade would have done without the
lock ("shadow") - the forward-test record the spec asks for.

Without quotes (Breeze 1-second days) aggression falls back to the tick rule and fills to LTP + no_quote_slippage,
so such days only exercise the logic; judge the strategy on recorded Angel expiry days.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Optional

from market_data.tick_store import Tick
from strategies.scalping.engine import TICK_SIZE, ScalpConfig, ScalpEngine, hhmm
from strategies.scalping.expiry_breakout import ExpiryOnly


class ExpiryGammaSqueeze(ExpiryOnly, ScalpEngine):
    strategy_id = "scalp_expiry_gamma"
    name = "Expiry Gamma Squeeze"

    AUTO_START = True            # paper session starts with the app; it only trades on expiry days
    PREMIUM_BAND = (12.0, 25.0)
    OI_WINDOW_MIN = 15
    OI_DROP = -0.015             # own-strike OI change over OI_WINDOW_MIN
    HIGH_MIN = 5                 # breakout reference: high of the last HIGH_MIN minutes
    TRIGGER_BUFFER = 0.20
    LIMIT_BUFFER = 0.40
    VOL_MULT = 2.0               # last 60 s of volume vs the average minute of the previous VOL_BASE_MIN
    VOL_BASE_MIN = 15
    AGGR_WINDOW_S = 30
    AGGR_MIN = 0.60
    MAX_CONSEC_STOPS = 2
    ARM_TTL_S = 45               # an armed order is dropped if the regime has not been re-checked for this long

    @classmethod
    def default_config(cls) -> ScalpConfig:
        return ScalpConfig(entry_start="13:15", entry_end="14:50", square_off="15:10", max_trades=3, max_losses=99,
                           cooldown_min=10, sl_pct=0.35, target_pct=1.00, trail_trigger=9.99, time_stop_min=8,
                           time_stop_min_gain=0.25, breakeven_trigger=0.40, breakeven_offset=0.50,
                           sizing="fixed", fixed_lots=1)

    # ------------------------------------------------------------------ state
    def _new_day(self, day: str, ts: datetime) -> None:
        super()._new_day(day, ts)
        self._flow: dict[str, deque] = {}     # token -> (ts, traded volume, aggression flag) per snapshot, last 60 s
        self._prev: dict[str, tuple] = {}     # token -> (cumulative volume, bid, ask, ltp) of the previous snapshot
        self._armed: dict[str, dict] = {}     # token -> resting stop-limit order
        self._consec_stops = 0
        self._low = self._high = 0.0          # extremes of the open position's LTP
        self._shadow: Optional[dict] = None   # a breakeven exit followed to its unlocked outcome

    def can_enter(self, ts: datetime) -> bool:
        return self._consec_stops < self.MAX_CONSEC_STOPS and super().can_enter(ts)

    # ------------------------------------------------------------- tick input
    def on_tick(self, t: Tick) -> None:
        held = self.pos.token if self.pos else None
        super().on_tick(t)
        if t.token not in self.insts or self.insts[t.token].kind not in ("CE", "PE") or t.ltp <= 0:
            return
        self._track_flow(t)
        if self.pos and self.pos.token == t.token:
            self._low = min(self._low or t.ltp, t.ltp)
            self._high = max(self._high, t.ltp)
        if self._shadow and self._shadow["token"] == t.token:
            self._follow_shadow(t)
        if held is None and self.pos is None and t.token in self._armed:
            self._try_fill(t)

    def _track_flow(self, t: Tick) -> None:
        prev = self._prev.get(t.token)
        if prev is not None:
            dv = max(t.volume - prev[0], 0)
            if dv:
                if prev[2] > 0 and prev[1] > 0:   # the spec's flag: LTP against the previous snapshot's quotes
                    flag = 1 if t.ltp >= prev[2] else -1 if t.ltp <= prev[1] else 0
                else:                             # no quotes in the data: tick rule
                    flag = (t.ltp > prev[3]) - (t.ltp < prev[3])
                q = self._flow.setdefault(t.token, deque())
                q.append((t.ts, dv, flag))
                while q and (t.ts - q[0][0]).total_seconds() > 60:
                    q.popleft()
        self._prev[t.token] = (t.volume or (prev[0] if prev else 0), t.bid, t.ask, t.ltp)

    def flow(self, token: str, ts: datetime) -> tuple[int, float]:
        """(volume traded in the last 60 s, ask-aggression ratio over the last AGGR_WINDOW_S)."""
        q = self._flow.get(token, ())
        vol60 = sum(dv for at, dv, _ in q if (ts - at).total_seconds() <= 60)
        recent = [(dv, f) for at, dv, f in q if (ts - at).total_seconds() <= self.AGGR_WINDOW_S]
        tot = sum(dv for dv, _ in recent)
        return vol60, (sum(dv for dv, f in recent if f == 1) / tot if tot else 0.0)

    # ----------------------------------------------------------------- regime
    def regime(self, kind: str) -> list[dict]:
        """Strikes of ``kind`` currently in regime, each with its stop-limit trigger."""
        f = self.S(self.fut_tok) if self.fut_tok else None
        if f is None or not f.cum_v or not f.ltp:
            return []
        if (f.ltp - f.vwap()) * (1 if kind == "CE" else -1) <= 0:
            return []
        out = []
        for (strike, kd), tok in self.opt.items():
            se = self.S(tok)
            if kd != kind or not (self.PREMIUM_BAND[0] <= se.ltp <= self.PREMIUM_BAND[1]):
                continue
            n_oi = se.n(self.OI_WINDOW_MIN)
            if not se.has(n_oi + 1) or se.doi(n_oi) > self.OI_DROP:
                continue
            trigger = round((se.hh(se.n(self.HIGH_MIN)) + self.TRIGGER_BUFFER) / TICK_SIZE) * TICK_SIZE
            out.append({"token": tok, "kind": kind, "strike": strike, "trigger": trigger,
                        "limit": trigger + self.LIMIT_BUFFER, "oi_chg": se.doi(n_oi),
                        "fut_vs_vwap": f.ltp - f.vwap()})
        return out

    def signal(self, ts: datetime, spot: float, atm: float) -> None:
        """Runs on each closed bucket while flat and allowed to enter: refresh the resting orders."""
        self._armed = {}
        if self.is_expiry_day():
            for kind in ("CE", "PE"):
                for order in self.regime(kind):
                    self._armed[order["token"]] = {**order, "armed_at": ts}
        return None

    # ---------------------------------------------------------------- trigger
    def _try_fill(self, t: Tick) -> None:
        o = self._armed[t.token]
        if (t.ts - o["armed_at"]).total_seconds() > self.ARM_TTL_S or not self.can_enter(t.ts):
            del self._armed[t.token]
            return
        if t.ltp < o["trigger"]:
            return
        se = self.S(t.token)
        vol60, aggr = self.flow(t.token, t.ts)
        base = se.vol(se.n(self.VOL_BASE_MIN)) / self.VOL_BASE_MIN
        vol_x = vol60 / base if base > 0 else 0.0
        px = self._buy_px(se)
        fill_ref = px if se.ask > 0 else se.ltp   # without quotes the limit is checked against LTP
        if vol_x <= self.VOL_MULT:
            outcome = "REJECTED_VOLUME"
        elif aggr < self.AGGR_MIN:
            outcome = "REJECTED_AGGRESSION"
        elif fill_ref > o["limit"] + 1e-9:
            outcome = "NO_FILL_ABOVE_LIMIT"
        else:
            outcome = "FILLED"
        rec = {"type": "signal", "time": t.ts.isoformat(), "symbol": self.insts[t.token].symbol, "outcome": outcome,
               "trigger": round(o["trigger"], 2), "limit": round(o["limit"], 2), "ltp": t.ltp, "bid": se.bid,
               "ask": se.ask, "vol_x": round(vol_x, 2), "aggression": round(aggr, 2), "oi_chg_15m": round(o["oi_chg"], 4),
               "fut_vs_vwap": round(o["fut_vs_vwap"], 2), "armed_at": o["armed_at"].isoformat()}
        del self._armed[t.token]  # one evaluation per arming; the next closed bucket re-arms if the regime still holds
        if outcome == "FILLED":
            why = (f"expiry squeeze: {o['kind']} {o['strike']:.0f} through {o['trigger']:.2f}, vol {vol_x:.1f}x, "
                   f"ask aggression {aggr:.0%}, own OI {o['oi_chg']:+.1%}/15m")
            self._enter(t.ts, o["kind"], o["strike"], why)
            if self.pos:
                self._armed = {}
                self._low = self._high = t.ltp
                self.signals_seen += 1
                rec["fill"] = round(self.pos.entry, 2)
            else:
                rec["outcome"] = "SKIPPED_NO_CAPITAL"
        self._emit("signal", rec)

    # ------------------------------------------------------------------ exits
    def _exit(self, ts: datetime, se, reason: str) -> None:
        p = self.pos
        super()._exit(ts, se, reason)
        tr = self.trades[-1]
        self._consec_stops = self._consec_stops + 1 if reason == "STOP" else 0
        self._emit("signal", {"type": "trade", "time": ts.isoformat(), "symbol": p.symbol, "entry_time": tr.entry_time,
                              "entry": tr.entry, "exit": tr.exit, "reason": reason, "net": tr.net,
                              "worst_pct": round((self._low or p.entry) / p.entry - 1, 4),
                              "best_pct": round(max(self._high, p.entry) / p.entry - 1, 4),
                              "hold_sec": tr.hold_sec})
        if reason == "BREAKEVEN":
            self._shadow = {"token": p.token, "symbol": p.symbol, "entry": p.entry, "entry_time": tr.entry_time,
                            "stop": p.entry * (1 - self.cfg.sl_pct), "target": p.target}

    def _follow_shadow(self, t: Tick) -> None:
        """What the breakeven-stopped trade would have done with the original stop and target."""
        s = self._shadow
        if t.ltp <= s["stop"]:
            result = "STOP"
        elif t.ltp >= s["target"]:
            result = "TARGET"
        elif t.ts.time() >= hhmm(self.cfg.square_off):
            result = "SQUARE_OFF"
        else:
            return
        self._shadow = None
        self._emit("signal", {"type": "shadow", "time": t.ts.isoformat(), "symbol": s["symbol"],
                              "entry_time": s["entry_time"], "entry": round(s["entry"], 2), "ltp": t.ltp,
                              "result_without_breakeven": result, "return_pct": round(t.ltp / s["entry"] - 1, 4)})
