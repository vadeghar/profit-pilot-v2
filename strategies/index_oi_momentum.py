"""Index Options OI-Momentum Buying System (base + expiry-day variants).

Implements spec: OI velocity on underlying-future tick/OI series (60-90s rolling
window), price-OI direction matrix, strike-level OI confirmation, option premium
breakout + velocity confirmation, spread/depth filter, session/ATR throttle
(base only), max-pain wall check (expiry only), risk sizing in Rs terms,
partial-close + trailing (incl. expiry-tightened variant), quick-exit overrides,
and auto base/expiry mode detection per index from a configurable expiry calendar.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.models import Candle, OrderSide, OrderType, Signal, Tick
from strategies import StrategyBase
from platform_config import get_index_lot_size
from utils.timezone import ensure_ist, ist_day_str, ist_minutes, now_ist


INDEX_SPECS = {
    "NIFTY":     {"exchange": "NSE", "lot_size": get_index_lot_size("NSE:NIFTY", 65), "strike_interval": 50,
                  "weekly_expiry_weekday": 1, "monthly_expiry": "last_tue",
                  "spread_threshold": 0.01, "spot_ref": 25000.0},
    "BANKNIFTY": {"exchange": "NSE", "lot_size": get_index_lot_size("NSE:BANKNIFTY", 30), "strike_interval": 100,
                  "weekly_expiry_weekday": None, "monthly_expiry": "last_tue",
                  "spread_threshold": 0.015, "spot_ref": 56000.0},
    "SENSEX":    {"exchange": "BSE", "lot_size": get_index_lot_size("BSE:SENSEX", 20), "strike_interval": 100,
                  "weekly_expiry_weekday": 3, "monthly_expiry": "last_thu",
                  "spread_threshold": 0.02, "spot_ref": 82000.0},
}


def _is_last_weekday(year: int, month: int, day: int, weekday: int) -> bool:
    import calendar as _cal
    last = max(w for w in _cal.monthcalendar(year, month) if w[weekday] != 0)[weekday]
    return day == last


def is_expiry_day(index: str, dt: datetime) -> bool:
    spec = INDEX_SPECS[index]
    wd = dt.weekday()
    if spec["weekly_expiry_weekday"] is not None and wd == spec["weekly_expiry_weekday"]:
        return True
    if spec["monthly_expiry"] == "last_tue" and wd == 1 and _is_last_weekday(dt.year, dt.month, dt.day, 1):
        return True
    if spec["monthly_expiry"] == "last_thu" and wd == 3 and _is_last_weekday(dt.year, dt.month, dt.day, 3):
        return True
    return False


@dataclass
class Position:
    side: str  # "CE" or "PE"
    entry_price: float
    qty_lots: int
    qty_units: int
    peak: float = 0.0
    be_done: bool = False
    partial1_done: bool = False
    partial2_done: bool = False
    entry_time: Any = None
    stop_price: float = 0.0


class IndexOIMomentumStrategy(StrategyBase):
    """OI-velocity + price-momentum option buyer. Works on synthetic tick dicts
    fed via on_tick using Tick with metadata carrying OI/volume/depth fields, or via
    on_candle for daily-seeded fallback. Multi-symbol: pass index via tick.instrument
    ('NIFTY' / 'BANKNIFTY' / 'SENSEX') or params['index'].
    """

    def _init_indicators(self) -> None:
        p = self.params
        self.k = float(p.get("k", 4.0))
        self.oi_window_s = int(p.get("oi_window_s", 75))
        self.risk_pct = float(p.get("risk_pct", 0.0075))
        self.sl_pct = float(p.get("sl_pct", 0.22))
        self.spread_thr = float(p.get("spread_threshold", 0.012))
        self.max_trades_day = int(p.get("max_trades_day", 7))
        self.max_trades_day_total = int(p.get("max_trades_day_total", 8))
        self.capital = float(p.get("capital", 100000.0))
        self.mode_override = p.get("mode_override")  # "base" | "expiry" | None
        self.max_pain_dist_pct = float(p.get("max_pain_dist_pct", 0.20))
        # per-index state
        self._st: Dict[str, Dict[str, Any]] = {}
        self._trades_today: Dict[str, int] = {}
        self._day: Optional[str] = None
        self._daily_pnl: float = 0.0
        self.positions: Dict[str, Position] = {}

    def _s(self, idx: str, leg: str = "") -> Dict[str, Any]:
        """Per-(index, leg) state: live WS feeds CE/PE legs separately so
        premium/OI histories never mix across legs. Legacy callers without a
        leg share the '' bucket (backtest/fabricated-tape compatible)."""
        key = f"{idx}:{leg}" if leg in ("CE", "PE") else idx
        if key not in self._st:
            self._st[key] = {
                "oi_hist": deque(maxlen=600), "px_hist": deque(maxlen=600),
                "und_hist": deque(maxlen=40), "prem_hist": deque(maxlen=20),
                "vel_hist": deque(maxlen=10), "atr5": None, "atr5_avg": None,
                "strike_oi": {}, "opp_strike_oi": {}, "last_ts": None,
                "stall": 0, "wall_px": None, "wall_oi": 0.0,
            }
        return self._st[key]

    def _mode(self, idx: str, ts: datetime) -> str:
        if self.mode_override in ("base", "expiry"):
            return self.mode_override
        try:
            return "expiry" if is_expiry_day(idx, ts) else "base"
        except Exception:
            return "base"

    def _num(self, v: Any, default: float = 0.0) -> float:
        """Broker-safe float cast: handles None, '', 'null', comma strings, nan/inf."""
        import math as _m
        try:
            if v is None:
                return default
            if isinstance(v, str):
                s = v.strip().replace(',', '')
                if s in ('', '-', 'null', 'None', 'N/A', 'nan', 'NaN', 'inf', 'Infinity'):
                    return default
                out = float(s)
            else:
                out = float(v)
            if _m.isnan(out) or _m.isinf(out):
                return default
            return out
        except (TypeError, ValueError):
            return default

    def _risk_params(self, mode: str) -> Tuple[float, float, int]:
        if mode == "expiry":
            return (self.params.get("expiry_risk_pct", 0.005),
                    self.params.get("expiry_sl_pct", 0.16),
                    int(self.params.get("expiry_max_trades", 3)))
        return (self.risk_pct, self.sl_pct, self.max_trades_day)

    def on_tick(self, tick: Tick) -> Optional[Signal]:
        md = getattr(tick, "metadata", {}) or {}
        idx = md.get("index") or self.params.get("index", "NIFTY")
        if idx not in INDEX_SPECS:
            return None
        ts = ensure_ist(getattr(tick, "timestamp", None) or now_ist())
        day = ist_day_str(ts)
        if day != self._day:
            self._day = day
            self._trades_today = {}
            self._daily_pnl = 0.0
            self.positions = {}
        leg = str(md.get("side_hint", "") or "").upper()
        if leg not in ("CE", "PE"):
            # derive from instrument suffix e.g. NIFTY_CE
            _inst = str(getattr(tick, 'instrument', '') or '').upper()
            leg = "CE" if _inst.endswith("_CE") else ("PE" if _inst.endswith("_PE") else "")
        st = self._s(idx, leg)
        mode = self._mode(idx, ts)
        risk_pct, sl_pct, max_trades = self._risk_params(mode)
        # daily loss limit -3%
        if self._daily_pnl <= -0.03 * self.capital:
            return None
        lp = self._num(getattr(tick, 'last_price', 0.0), 0.0)
        oi = self._num(md.get("underlying_oi"), 0.0)
        upx = self._num(md.get("underlying_price"), lp)
        bid = self._num(md.get("bid"), lp * 0.999 if lp else 0.0)
        ask = self._num(md.get("ask"), lp * 1.001 if lp else 0.0)
        depth = self._num(md.get("depth_qty"), 1e9)
        vol = self._num(md.get("volume"), 0.0)
        st["oi_hist"].append((ts, oi))
        st["px_hist"].append((ts, upx))
        st["und_hist"].append(upx)
        st["prem_hist"].append(lp)
        st["strike_oi"][ts] = self._num(md.get("strike_oi"), 0.0)
        st["opp_strike_oi"][ts] = self._num(md.get("opp_strike_oi"), 0.0)
        _wp = md.get("wall_px")
        st["wall_px"] = self._num(_wp, 0.0) or None
        st["wall_oi"] = self._num(md.get("wall_oi"), 0.0)

        # manage open position first (trailing / quick exits)
        if idx in self.positions:
            sig = self._manage(idx, tick.last_price, ts, mode, upx, oi, bid, ask)
            if sig:
                return sig
            return None

        if self._trades_today.get(idx, 0) >= max_trades:
            return None
        if sum(self._trades_today.values()) >= self.max_trades_day_total:
            return None
        # session throttle (IST wall-clock): no new entries last 25 min
        # equity 15:05+ IST; expiry cutoff 14:45 IST. MCX-style logic uses
        # the same equity session since index options trade 09:15-15:30 IST.
        hhmm = ist_minutes(ts)
        if mode == "expiry" and hhmm >= 14 * 60 + 45:
            return None
        if mode == "base" and hhmm >= 15 * 60 + 5:
            return None
        # ATR throttle base only
        if mode == "base" and st["atr5"] and st["atr5_avg"]:
            if st["atr5"] < 0.6 * st["atr5_avg"]:
                return None
        # --- OI velocity trigger ---
        vel = self._oi_velocity(st, ts)
        if vel is None:
            return None
        prior = list(st["vel_hist"])
        if len(prior) < 5:
            st["vel_hist"].append(abs(vel))
            return None
        avg = sum(prior) / len(prior)
        st["vel_hist"].append(abs(vel))
        if avg <= 0:
            return None
        if abs(vel) < self.k * avg:
            return None
        # --- direction matrix ---
        direction = self._direction(st)
        if direction is None:
            return None
        side, weak = direction
        # --- strike-level confirmation ---
        if not self._strike_confirms(st, side):
            return None
        # --- premium breakout + velocity ---
        if not self._premium_confirms(st, side):
            return None
        # --- spread/depth filter ---
        spec = INDEX_SPECS[idx]
        thr = self.params.get("spread_threshold", spec["spread_threshold"])
        if lp > 0 and (ask - bid) / lp > thr:
            return None
        lot = spec["lot_size"]
        if depth < 2.5 * lot:
            return None
        # --- expiry max-pain wall check ---
        if mode == "expiry" and st["wall_px"]:
            dist = abs(upx - st["wall_px"]) / st["wall_px"] * 100
            mins_left = (15 * 60 + 0) - hhmm  # to 15:00 IST
            if dist < self.max_pain_dist_pct and mins_left < 90:
                return None
        # Allocate the current available capital to whole option lots. The
        # premium (not the underlying spot) is the cash requirement.
        lots = int(self.capital // (ask * lot)) if ask > 0 else 0
        if lots <= 0:
            return None
        qty_units = lots * lot
        # record position (entry assumed at ask)
        entry = ask
        self.positions[idx] = Position(side=side, entry_price=entry, qty_lots=lots,
                                       qty_units=qty_units, peak=entry, entry_time=ts,
                                       stop_price=entry * (1 - sl_pct))
        self._trades_today[idx] = self._trades_today.get(idx, 0) + 1
        return Signal(strategy_id=self.strategy_id, instrument=f"{idx}_OPT",
                      action=OrderSide.BUY, quantity=qty_units, order_type=OrderType.MARKET,
                      metadata={"reason": "oi_momentum_entry", "index": idx, "mode": mode,
                                "side": side, "entry": entry, "lots": lots,
                                "oi_velocity": vel, "k": self.k})

    def _oi_velocity(self, st, ts) -> Optional[float]:
        hist = st["oi_hist"]
        if len(hist) < 3:
            return None
        now_oi = hist[-1][1]
        target = ts.timestamp() - self.oi_window_s
        past_oi = None
        for t, o in reversed(hist):
            if t.timestamp() <= target:
                past_oi = o
                break
        if past_oi is None:
            past_oi = hist[0][1]
        return now_oi - past_oi

    def _direction(self, st) -> Optional[Tuple[str, bool]]:
        px = st["px_hist"]
        if len(px) < 3:
            return None
        dpx = px[-1][1] - px[-3][1]
        vel = (st["oi_hist"][-1][1] - st["oi_hist"][-3][1]) if len(st["oi_hist"]) >= 3 else 0
        if dpx > 0 and vel > 0:
            return ("CE", False)
        if dpx < 0 and vel > 0:
            return ("PE", False)
        if dpx > 0 and vel < 0:
            return ("CE", True)
        return None  # long unwinding: no new entry

    def _strike_confirms(self, st, side) -> bool:
        keys = sorted(st["strike_oi"].keys())
        if len(keys) < 3:
            return True  # not enough strike history: don't block in simulation
        recent = [st["strike_oi"][k] for k in keys[-3:]]
        opp = [st["opp_strike_oi"][k] for k in keys[-3:]]
        d = recent[-1] - recent[0]
        do = opp[-1] - opp[0]
        return d > 0 and d >= do

    def _premium_confirms(self, st, side) -> bool:
        prem = list(st["prem_hist"])
        if len(prem) < 5:
            return False
        window = prem[-6:-1] if len(prem) >= 6 else prem[:-1]
        cur = prem[-1]
        if side == "CE":
            if cur < max(window):
                return False
            last = prem[-4:]
            ups = sum(1 for i in range(1, len(last)) if last[i] >= last[i - 1])
            return ups >= 2
        else:
            if cur > min(window):
                return False
            last = prem[-4:]
            dns = sum(1 for i in range(1, len(last)) if last[i] <= last[i - 1])
            return dns >= 2

    def _manage(self, idx, ltp, ts, mode, upx, oi, bid, ask) -> Optional[Signal]:
        pos = self.positions[idx]
        st = self._s(idx)
        ret = (ltp - pos.entry_price) / pos.entry_price
        pos.peak = max(pos.peak, ltp)
        sl_pct, trail_mult = (0.16, 1.25) if mode == "expiry" else (self.sl_pct, 1.75)
        # quick exits
        if self._quick_exit(idx, st, pos, ltp, bid, ask):
            return self._close(idx, pos, ltp, ts, "quick_exit")
        # hard SL
        if ltp <= pos.stop_price:
            return self._close(idx, pos, ltp, ts, "hard_sl")
        # expiry hard exit 15:15 IST (IST wall-clock)
        if mode == "expiry" and ist_minutes(ensure_ist(ts)) >= 15 * 60 + 15:
            return self._close(idx, pos, ltp, ts, "expiry_flat")
        # partials
        p1 = 0.225 if mode == "expiry" else 0.35
        p2 = 0.60 if mode == "expiry" else 0.70
        if not pos.partial1_done and ret >= p1:
            pos.partial1_done = True
            pos.stop_price = pos.entry_price  # breakeven
        if mode == "expiry" and pos.partial1_done and ret >= p2:
            return self._close(idx, pos, ltp, ts, "expiry_second_target")
        if mode == "base":
            if pos.partial1_done and not pos.partial2_done and ret >= p2:
                pos.partial2_done = True
            if pos.partial2_done:
                atr = self._prem_atr(st)
                trail = pos.peak - trail_mult * atr
                pos.stop_price = max(pos.stop_price, trail)
                if ltp <= pos.stop_price:
                    return self._close(idx, pos, ltp, ts, "trailing_sl")
        else:
            if pos.partial1_done:
                atr = self._prem_atr(st)
                trail = pos.peak - trail_mult * atr
                pos.stop_price = max(pos.stop_price, trail)
                if ltp <= pos.stop_price:
                    return self._close(idx, pos, ltp, ts, "trailing_sl")
        return None

    def _prem_atr(self, st) -> float:
        prem = list(st["prem_hist"])
        if len(prem) < 3:
            return max(prem[-1] * 0.02, 0.5) if prem else 1.0
        diffs = [abs(prem[i] - prem[i - 1]) for i in range(1, len(prem))]
        return sum(diffs) / len(diffs) or 0.5

    def _quick_exit(self, idx, st, pos, ltp, bid, ask) -> bool:
        d = self._direction(st)
        if d and ((pos.side == "CE" and d[0] == "PE" and not d[1]) or
                  (pos.side == "PE" and d[0] == "CE")):
            # matrix flipped hard against position
            if pos.side == "CE" and d[0] == "PE":
                return True
        # spread blowout
        ltp = self._num(ltp, 0.0)
        ask = self._num(ask, ltp)
        bid = self._num(bid, ltp)
        if ltp > 0 and (ask - bid) / ltp > 2 * self.spread_thr:
            return True
        return False

    def _close(self, idx, pos, ltp, ts, reason) -> Signal:
        pnl = (ltp - pos.entry_price) * pos.qty_units
        self._daily_pnl += pnl
        del self.positions[idx]
        return Signal(strategy_id=self.strategy_id, instrument=f"{idx}_OPT",
                      action=OrderSide.SELL, quantity=pos.qty_units,
                      order_type=OrderType.MARKET,
                      metadata={"reason": f"exit_{reason}", "index": idx,
                                "entry": pos.entry_price, "exit": ltp,
                                "pnl": pnl, "lots": pos.qty_lots})

    def on_candle(self, candle: Candle) -> Optional[Signal]:
        return None
