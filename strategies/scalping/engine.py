"""Tick-driven scalping engine shared by every NIFTY option-scalping strategy.

One ``ScalpEngine`` = one strategy with its own position, risk limits and
trade log. The same engine runs in live paper trading (fed by the tick hub)
and in backtests (fed by recorded ticks), so the two cannot diverge.

Ticks are aggregated per instrument into fixed ``bucket_sec`` buckets (15 s
by default): NSE broadcasts OI in 1-3 s snapshots, so OI/volume features are
evaluated on closed buckets, never per tick. Strategy signals are evaluated
once per closed bucket; exits are checked on every tick of the held contract.

Fills: a buy fills at the best ask (a sell at the best bid) when the tick
carries quotes, plus ``slippage_ticks`` x 0.05; without quotes (Breeze 1-second
data) at LTP +/- ``no_quote_slippage`` points. Charges come from
backtest/charges.py (the platform's NSE option charge table).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Callable, Optional

from backtest.charges import ChargeConfig, Fill, option_charges
from market_data.tick_store import Instrument, Tick

TICK_SIZE = 0.05


def hhmm(s: str) -> time:
    h, m = map(int, s.split(":"))
    return time(h, m)


@dataclass
class ScalpConfig:
    bucket_sec: int = 15
    strike_step: int = 50
    entry_start: str = "09:20"
    entry_end: str = "14:45"
    square_off: str = "15:00"
    max_trades: int = 3
    max_losses: int = 2
    cooldown_min: int = 3
    sl_pct: float = 0.10
    target_pct: float = 0.20
    trail_trigger: float = 0.10     # start trailing once the premium is up this much
    trail_pct: float = 0.08         # trail this far below the peak
    time_stop_min: int = 5
    time_stop_min_gain: float = 0.05
    sizing: str = "capital"         # "capital": deploy deploy_pct of the balance; "risk": risk risk_pct to the stop
    deploy_pct: float = 1.0         # share of the current balance spent on premium per trade (compounds)
    risk_pct: float = 0.015         # capital at risk per trade (to the stop), sizing="risk" only
    max_lots: int = 27              # NSE freeze limit for NIFTY: 1,800 units / 65 per lot
    slippage_ticks: int = 1
    no_quote_slippage: float = 0.5
    vwap_exit: bool = False         # exit when LTP closes below the option's VWAP
    trail_prev_minute_low: bool = False
    brokerage_per_order: float = 20.0

    def update(self, overrides: Optional[dict]) -> "ScalpConfig":
        for k, v in (overrides or {}).items():
            if not hasattr(self, k) or v is None or v == "":
                continue
            cur = getattr(self, k)
            if isinstance(cur, bool):
                v = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
            setattr(self, k, type(cur)(v))
        return self


@dataclass
class Bucket:
    o: float
    h: float
    l: float
    c: float
    vol: int = 0
    oi: float = 0.0
    delta: int = 0      # aggressive-buy volume minus aggressive-sell volume
    big: int = 0        # prints with LTQ >= 5x the running average


class Series:
    """Per-instrument tick aggregator + features on closed buckets."""

    def __init__(self, bucket_sec: int):
        self.bs = bucket_sec
        self.buckets: deque[Bucket] = deque(maxlen=600)
        self.cur: Optional[Bucket] = None
        self.last_vol: Optional[int] = None
        self.prev_ltp = self.ltp = self.oi = 0.0
        self.bid = self.ask = 0.0
        self.cum_pv = 0.0
        self.cum_v = 0
        self.ltq_avg = 0.0
        self.prints: deque[tuple[datetime, int, float]] = deque(maxlen=400)  # (ts, ltq, ltq / running average) per traded tick

    def on_tick(self, t: Tick) -> None:
        ltp = t.ltp
        if ltp <= 0:
            return
        if self.cur is None:
            self.cur = Bucket(ltp, ltp, ltp, ltp, oi=t.oi or self.oi)
        b = self.cur
        b.h, b.l, b.c = max(b.h, ltp), min(b.l, ltp), ltp
        if t.oi:
            b.oi = self.oi = t.oi
        self.bid, self.ask = t.bid, t.ask
        dv = 0 if self.last_vol is None else max(t.volume - self.last_vol, 0)
        self.last_vol = t.volume if t.volume else self.last_vol
        if dv:
            if t.ask and ltp >= t.ask:
                side = 1
            elif t.bid and ltp <= t.bid:
                side = -1
            else:  # tick rule when there is no quote to compare against
                side = (ltp > self.prev_ltp) - (ltp < self.prev_ltp) if self.prev_ltp else 0
            b.vol += dv
            b.delta += side * dv
            self.cum_pv += ltp * dv
            self.cum_v += dv
            ltq = t.ltq or dv
            mult = ltq / self.ltq_avg if self.ltq_avg else 0.0
            if mult >= 5:
                b.big += 1
            self.prints.append((t.ts, ltq, mult))
            self.ltq_avg = ltq if not self.ltq_avg else 0.98 * self.ltq_avg + 0.02 * ltq
        self.prev_ltp = self.ltp = ltp

    def roll(self) -> None:
        if self.cur is None:
            if not self.ltp:
                return
            self.cur = Bucket(self.ltp, self.ltp, self.ltp, self.ltp, oi=self.oi)
        self.buckets.append(self.cur)
        self.cur = None

    # -- features on closed buckets (k = number of buckets) --
    def n(self, minutes: float) -> int:
        return max(1, int(round(minutes * 60 / self.bs)))

    def win(self, a: int, b: int = 0) -> list[Bucket]:
        L = list(self.buckets)
        return L[max(len(L) - a, 0): len(L) - b]

    def has(self, k: int) -> bool:
        return len(self.buckets) >= k

    def last(self) -> Bucket:
        return self.buckets[-1]

    def vol(self, k: int, skip: int = 0) -> int:
        return sum(b.vol for b in self.win(k + skip, skip))

    def cvd(self, k: int) -> int:
        return sum(b.delta for b in self.win(k))

    def big(self, k: int) -> int:
        return sum(b.big for b in self.win(k))

    def hh(self, k: int, skip: int = 0) -> float:
        w = self.win(k + skip, skip)
        return max(b.h for b in w) if w else math.inf

    def ll(self, k: int, skip: int = 0) -> float:
        w = self.win(k + skip, skip)
        return min(b.l for b in w) if w else -math.inf

    def doi_abs(self, k: int) -> float:
        return 0.0 if len(self.buckets) <= k else self.buckets[-1].oi - self.buckets[-k - 1].oi

    def doi(self, k: int) -> float:
        if len(self.buckets) <= k or not self.buckets[-k - 1].oi:
            return 0.0
        return self.doi_abs(k) / self.buckets[-k - 1].oi

    def dpx(self, k: int) -> float:
        return 0.0 if len(self.buckets) <= k else self.buckets[-1].c - self.buckets[-k - 1].c

    def vwap(self) -> float:
        return self.cum_pv / self.cum_v if self.cum_v else self.ltp

    def vol_ratio(self, window_min: float, base_min: float) -> float:
        """Volume of the last ``window_min`` vs its per-window average over the ``base_min`` before it."""
        w, base = self.n(window_min), self.n(base_min)
        if not self.has(w + base):
            return 0.0
        avg = self.vol(base, skip=w) / (base / w)
        return self.vol(w) / avg if avg > 0 else 0.0

    def big_prints_since(self, since: datetime, mult: float = 5.0) -> int:
        """Traded ticks after ``since`` whose LTQ was >= ``mult`` x the running average LTQ."""
        return sum(1 for ts, _q, m in self.prints if m >= mult and ts > since)


@dataclass
class Position:
    token: str
    symbol: str
    kind: str
    strike: float
    qty: int
    lots: int
    entry: float
    entry_ltp: float
    entry_mid: float
    t_in: datetime
    sl: float
    target: float
    peak: float
    why: str


@dataclass
class ScalpTrade:
    strategy: str
    date: str
    symbol: str
    kind: str
    strike: float
    lots: int
    qty: int
    entry_time: str
    exit_time: str
    entry: float
    exit: float
    gross: float
    charges: float
    spread_cost: float     # paid vs the quote mid-point (both legs), when quotes exist
    net: float
    reason: str
    why: str
    hold_sec: float


class ScalpEngine:
    """Base engine; subclasses implement ``signal(ts, spot, atm)``."""

    strategy_id = "scalp"
    name = "Scalp"

    def __init__(self, instruments: dict[str, Instrument], *, capital: float = 50_000.0,
                 config: Optional[ScalpConfig] = None, on_event: Optional[Callable[[str, dict], None]] = None):
        self.cfg = config or self.default_config()
        self.insts = instruments
        self.capital = self.balance = float(capital)
        self.on_event = on_event
        self.series: dict[str, Series] = {}
        self.spot_tok = next((t for t, i in instruments.items() if i.kind == "IDX"), None)
        self.fut_tok = next((t for t, i in instruments.items() if i.kind == "FUT"), None)
        self.opt = {(float(i.strike), i.kind): t for t, i in instruments.items() if i.kind in ("CE", "PE")}
        self.cur_bucket: Optional[datetime] = None
        self.pos: Optional[Position] = None
        self.trades: list[ScalpTrade] = []
        self.day: Optional[str] = None
        self.day_trades = self.day_losses = 0
        self.last_exit: Optional[datetime] = None
        self.last_tick_at: Optional[datetime] = None
        self.ticks = 0
        self.signals_seen = 0
        self.skips: list[dict] = []  # signals not taken because one lot cost more than the balance
        self.charge_cfg = ChargeConfig(brokerage_per_order=self.cfg.brokerage_per_order)

    @classmethod
    def default_config(cls) -> ScalpConfig:
        return ScalpConfig()

    # ---------------------------------------------------------------- helpers
    def S(self, tok: Optional[str]) -> Series:
        if tok not in self.series:
            self.series[tok] = Series(self.cfg.bucket_sec)
        return self.series[tok]

    def tok(self, strike: float, kind: str) -> Optional[str]:
        return self.opt.get((float(strike), kind))

    def set_instruments(self, instruments: dict[str, Instrument]) -> None:
        """Replace the contract map (a new day has new tokens/expiry)."""
        self.insts = dict(instruments)
        self.opt = {(float(i.strike), i.kind): t for t, i in instruments.items() if i.kind in ("CE", "PE")}
        self.spot_tok = next((t for t, i in instruments.items() if i.kind == "IDX"), None)
        self.fut_tok = next((t for t, i in instruments.items() if i.kind == "FUT"), None)

    def add_instruments(self, instruments: dict[str, Instrument]) -> None:
        self.insts.update(instruments)
        self.opt.update({(float(i.strike), i.kind): t for t, i in instruments.items() if i.kind in ("CE", "PE")})
        self.spot_tok = self.spot_tok or next((t for t, i in instruments.items() if i.kind == "IDX"), None)
        self.fut_tok = self.fut_tok or next((t for t, i in instruments.items() if i.kind == "FUT"), None)

    def _emit(self, kind: str, payload: dict) -> None:
        if self.on_event:
            try:
                self.on_event(kind, payload)
            except Exception:
                pass

    # ------------------------------------------------------------- tick input
    def on_tick(self, t: Tick) -> None:
        if t.token not in self.insts:
            return
        day = t.ts.date().isoformat()
        if day != self.day:
            self._new_day(day, t.ts)
        bs = self.cfg.bucket_sec
        tb = t.ts.replace(second=(t.ts.second // bs) * bs, microsecond=0)
        if self.cur_bucket is None:
            self.cur_bucket = tb
        if tb > self.cur_bucket:
            steps = int((tb - self.cur_bucket).total_seconds() // bs)
            for _ in range(min(steps, 40)):  # empty buckets carry the last price/OI forward
                for se in self.series.values():
                    se.roll()
            self.cur_bucket = tb
            self._on_bucket(t.ts)
        self.S(t.token).on_tick(t)
        self.ticks += 1
        self.last_tick_at = t.ts
        if self.pos:
            if t.ts.time() >= hhmm(self.cfg.square_off):
                se = self.S(self.pos.token)
                self._exit(t.ts, se, "SQUARE_OFF")
            elif t.token == self.pos.token:
                self._manage(t.ts)

    def _new_day(self, day: str, ts: datetime) -> None:
        if self.pos and self.day:
            self._exit(ts, self.S(self.pos.token), "DAY_END")
        self.day = day
        self.day_trades = self.day_losses = 0
        self.last_exit = None
        self.series = {}
        self.cur_bucket = None

    def finish(self, ts: Optional[datetime] = None) -> None:
        """End of data: close anything still open at the last price."""
        if self.pos:
            self._exit(ts or self.last_tick_at, self.S(self.pos.token), "END_OF_DATA")

    # ---------------------------------------------------------- entry gating
    def can_enter(self, ts: datetime) -> bool:
        t = ts.time()
        if not (hhmm(self.cfg.entry_start) <= t <= hhmm(self.cfg.entry_end)):
            return False
        if self.day_trades >= self.cfg.max_trades or self.day_losses >= self.cfg.max_losses:
            return False
        if self.last_exit and ts - self.last_exit < timedelta(minutes=self.cfg.cooldown_min):
            return False
        return True

    def _on_bucket(self, ts: datetime) -> None:
        if self.pos:
            self._on_bucket_in_position(ts)
            return
        if not self.can_enter(ts) or not self.spot_tok:
            return
        sp = self.S(self.spot_tok)
        if not sp.has(sp.n(2)):
            return
        spot = sp.last().c
        step = self.cfg.strike_step
        atm = round(spot / step) * step
        sig = self.signal(ts, spot, atm)
        if sig:
            self.signals_seen += 1
            kind, strike, why = sig
            self._enter(ts, kind, strike, why)

    def signal(self, ts: datetime, spot: float, atm: float) -> Optional[tuple[str, float, str]]:
        raise NotImplementedError

    # ------------------------------------------------------------ execution
    def _buy_px(self, se: Series) -> float:
        if se.ask > 0:
            return se.ask + self.cfg.slippage_ticks * TICK_SIZE
        return se.ltp + self.cfg.no_quote_slippage

    def _sell_px(self, se: Series) -> float:
        if se.bid > 0:
            return max(se.bid - self.cfg.slippage_ticks * TICK_SIZE, TICK_SIZE)
        return max(se.ltp - self.cfg.no_quote_slippage, TICK_SIZE)

    @staticmethod
    def _mid(se: Series) -> float:
        return (se.bid + se.ask) / 2 if se.bid > 0 and se.ask > 0 else se.ltp

    def _enter(self, ts: datetime, kind: str, strike: float, why: str) -> None:
        tok = self.tok(strike, kind)
        if not tok:
            return
        inst, se = self.insts[tok], self.S(tok)
        if se.ltp <= 0:
            return
        px = self._buy_px(se)
        lot = inst.lot or 1
        cost_per_lot = px * lot
        if self.cfg.sizing == "risk":
            risk_per_lot = px * self.cfg.sl_pct * lot
            lots = max(1, int(self.balance * self.cfg.risk_pct // risk_per_lot)) if risk_per_lot > 0 else 0
        else:  # compounding: deploy a share of the CURRENT balance, so wins grow and losses shrink the next size
            lots = int(self.balance * self.cfg.deploy_pct // cost_per_lot) if cost_per_lot > 0 else 0
        lots = min(lots, self.cfg.max_lots, int(self.balance // cost_per_lot) if cost_per_lot > 0 else 0)
        if lots < 1:  # never spend more premium than the account holds
            self.skips.append({"time": ts.isoformat(), "symbol": inst.symbol, "premium": round(px, 2),
                               "cost_per_lot": round(cost_per_lot, 2), "balance": round(self.balance, 2), "why": why})
            self._emit("skip", {"time": ts.isoformat(), "why": f"one lot of {inst.symbol} costs "
                                f"Rs {cost_per_lot:,.0f} > balance Rs {self.balance:,.0f}"})
            return
        self.pos = Position(tok, inst.symbol, kind, strike, lots * lot, lots, px, se.ltp, self._mid(se), ts,
                            px * (1 - self.cfg.sl_pct), px * (1 + self.cfg.target_pct), px, why)
        self.day_trades += 1
        self._emit("entry", {"position": self.position_dict(), "balance": self.balance})

    def _on_bucket_in_position(self, ts: datetime) -> None:
        p = self.pos
        se = self.S(p.token)
        if self.cfg.trail_prev_minute_low and se.has(se.n(1)):
            prev_low = se.ll(se.n(1))
            if prev_low > p.sl and prev_low < se.ltp:
                p.sl = prev_low
        if self.cfg.vwap_exit and se.ltp < se.vwap():
            self._exit(ts, se, "BELOW_VWAP")

    def _manage(self, ts: datetime) -> None:
        p, cfg = self.pos, self.cfg
        se = self.S(p.token)
        ltp = se.ltp
        p.peak = max(p.peak, ltp)
        if p.peak >= p.entry * (1 + cfg.trail_trigger):
            p.sl = max(p.sl, p.entry, p.peak * (1 - cfg.trail_pct))
        if ltp <= p.sl:
            self._exit(ts, se, "STOP" if p.sl < p.entry else "TRAIL")
        elif ltp >= p.target:
            self._exit(ts, se, "TARGET")
        elif ts - p.t_in >= timedelta(minutes=cfg.time_stop_min) and p.peak < p.entry * (1 + cfg.time_stop_min_gain):
            self._exit(ts, se, "TIME_STOP")

    def _exit(self, ts: datetime, se: Series, reason: str) -> None:
        p = self.pos
        px = self._sell_px(se)
        gross = (px - p.entry) * p.qty
        ch = option_charges([Fill("BUY", p.entry, p.qty), Fill("SELL", px, p.qty)], ts.date(), self.charge_cfg)["total"]
        spread_cost = ((p.entry - p.entry_mid) + (self._mid(se) - px)) * p.qty
        net = gross - ch
        self.balance += net
        tr = ScalpTrade(self.strategy_id, p.t_in.date().isoformat(), p.symbol, p.kind, p.strike, p.lots, p.qty,
                        p.t_in.isoformat(), ts.isoformat(), round(p.entry, 2), round(px, 2), round(gross, 2),
                        round(ch, 2), round(spread_cost, 2), round(net, 2), reason, p.why,
                        (ts - p.t_in).total_seconds())
        self.trades.append(tr)
        if net < 0:
            self.day_losses += 1
        self.last_exit, self.pos = ts, None
        self._emit("exit", {"trade": asdict(tr), "balance": self.balance})

    # ---------------------------------------------------------------- status
    def position_dict(self) -> Optional[dict]:
        if not self.pos:
            return None
        p = self.pos
        se = self.S(p.token)
        return {"symbol": p.symbol, "kind": p.kind, "strike": p.strike, "lots": p.lots, "qty": p.qty,
                "entry": round(p.entry, 2), "ltp": se.ltp, "sl": round(p.sl, 2), "target": round(p.target, 2),
                "entry_time": p.t_in.isoformat(), "unrealized": round((se.ltp - p.entry) * p.qty, 2), "why": p.why}

    def snapshot(self) -> dict[str, Any]:
        return {"strategy_id": self.strategy_id, "name": self.name, "capital": self.capital,
                "balance": round(self.balance, 2), "position": self.position_dict(),
                "trades": [asdict(t) for t in self.trades], "day": self.day,
                "day_trades": self.day_trades, "day_losses": self.day_losses, "ticks": self.ticks,
                "signals": self.signals_seen, "skipped": len(self.skips), "last_skips": self.skips[-5:],
                "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
                "config": asdict(self.cfg)}
