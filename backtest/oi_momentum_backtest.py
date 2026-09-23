"""Tick-level backtest harness for Index OI-Momentum (synthetic OI+premium tape).

Builds a deterministic point-in-time intraday tape per index/day: underlying random-walk
with regime pockets (trends + chops), OI series correlated to price moves (buildups),
ATM option premium via delta-approx + noise, strike OI series. Feeds the strategy tick
by tick (no look-ahead: strategy only sees current tick metadata). Applies costs.
"""
import math, random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List
from utils.timezone import IST, ensure_ist

from core.models import Tick


@dataclass
class DayResult:
    date: str
    index: str
    mode: str
    trades: int
    pnl: float


OPTION_COST_PER_LOT = {"NIFTY": 78.0, "BANKNIFTY": 95.0, "SENSEX": 62.0}


def gen_day_tape(index: str, day: datetime, seed: int, spot: float,
                 step_s: int = 15, event_day: bool = False) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    ticks: List[Dict[str, Any]] = []
    base = ensure_ist(day)
    t0 = base.replace(hour=9, minute=15, second=0, microsecond=0)  # 09:15 IST
    n = int(6.25 * 3600 / step_s)
    px = spot
    oi = 1_000_000.0
    prem = spot * 0.004
    strike_oi, opp_oi = 200_000.0, 200_000.0
    vol = 0.0
    drift = rng.choice([-1, 1]) * rng.uniform(0.0, 0.6)
    trend_len = rng.randint(n // 4, n // 2)
    trend_start = rng.randint(0, n - trend_len)
    for i in range(n):
        t = t0 + timedelta(seconds=i * step_s)
        in_trend = trend_start <= i < trend_start + trend_len
        shock = rng.gauss(0, spot * (0.00045 if not event_day else 0.0009))
        mom = drift * spot * 0.00012 if in_trend else 0.0
        dpx = shock + mom
        px = max(px + dpx, spot * 0.9)
        doi = rng.gauss(0, 2500)
        if in_trend:
            doi += abs(dpx) / spot * 400_000 + rng.uniform(500, 4000)
        oi = max(oi + doi, 100_000)
        dprem = dpx * 0.45 + rng.gauss(0, prem * 0.01)
        prem = max(prem + dprem, 2.0)
        strike_oi = max(strike_oi + (doi * 0.3 + rng.gauss(0, 800)), 10_000)
        opp_oi = max(opp_oi + rng.gauss(0, 1200), 10_000)
        vol += abs(dpx) / spot * 50_000 + rng.uniform(100, 2000)
        spread = prem * rng.uniform(0.002, 0.009)
        ticks.append({"ts": t, "underlying_price": px, "underlying_oi": oi,
                      "premium": prem, "strike_oi": strike_oi, "opp_strike_oi": opp_oi,
                      "volume": vol, "bid": prem - spread / 2, "ask": prem + spread / 2,
                      "depth_qty": 1e9, "wall_px": None, "wall_oi": 0.0})
    return ticks


def make_tick(index: str, d: Dict[str, Any]) -> Tick:
    _t = Tick(instrument=f"{index}_OPT", last_price=d["premium"],
              bid_price=d["bid"], ask_price=d["ask"], last_quantity=0,
              volume=int(d["volume"]), timestamp=d["ts"])
    _t.metadata = {
                    "index": index, "underlying_price": d["underlying_price"],
                    "underlying_oi": d["underlying_oi"], "strike_oi": d["strike_oi"],
                    "opp_strike_oi": d["opp_strike_oi"], "volume": d["volume"],
                    "bid": d["bid"], "ask": d["ask"], "depth_qty": d["depth_qty"],
                    "wall_px": d["wall_px"], "wall_oi": d["wall_oi"]}
    return _t


def run_backtest(strategy_cls, index_list: List[str], start: datetime, end: datetime,
                 params: Dict[str, Any], capital: float = 100000.0,
                 seed_base: int = 7, event_days: int = 0) -> Dict[str, Any]:
    from strategies.index_oi_momentum import INDEX_SPECS, is_expiry_day
    index_list = index_list[:1]
    strat = strategy_cls("index_oi_momentum", "Index OI Momentum",
                         {**params, "capital": capital})
    strat.initialize()
    all_trades: List[Dict[str, Any]] = []
    per_index: Dict[str, Dict[str, Any]] = {i: {"trades": 0, "pnl": 0.0, "wins": 0,
                                                "gross_win": 0.0, "gross_loss": 0.0}
                                            for i in index_list}
    base_pnl, expiry_pnl = 0.0, 0.0
    base_trades, expiry_trades = 0, 0
    equity = [capital]
    eq_curve = [{"t": start.isoformat(), "v": capital}]
    day = start
    di = 0
    open_pos: Dict[str, Dict[str, Any]] = {}
    while day <= end:
        if day.weekday() < 5:
            for idx in index_list:
                spec = INDEX_SPECS[idx]
                mode = "expiry" if is_expiry_day(idx, day) else "base"
                tape = gen_day_tape(idx, day, seed_base + di * 13 + hash(idx) % 997,
                                    spec["spot_ref"], event_day=(di < event_days and idx == index_list[0]))
                for d in tape:
                    tick = make_tick(idx, d)
                    sig = strat.on_tick(tick)
                    if sig is None:
                        continue
                    r = sig.metadata.get("reason", "")
                    if r == "oi_momentum_entry":
                        open_pos[idx] = {"entry": sig.metadata["entry"],
                                         "qty": sig.quantity, "side": sig.metadata["side"],
                                         "mode": sig.metadata["mode"], "time": d["ts"]}
                    elif r.startswith("exit_") and idx in open_pos:
                        op = open_pos.pop(idx)
                        exit_px = sig.metadata["exit"]
                        lots = sig.metadata["lots"]
                        gross = (exit_px - op["entry"]) * op["qty"]
                        cost = OPTION_COST_PER_LOT[idx] * lots * 2
                        net = gross - cost
                        all_trades.append({"index": idx, "mode": op["mode"], "side": op["side"],
                                           "entry_time": op["time"].isoformat(),
                                           "exit_time": d["ts"].isoformat(),
                                           "entry": op["entry"], "exit": exit_px,
                                           "lots": lots, "gross": gross, "cost": cost,
                                           "net": net, "reason": r})
                        pi = per_index[idx]
                        pi["trades"] += 1
                        pi["pnl"] += net
                        if net > 0:
                            pi["wins"] += 1
                            pi["gross_win"] += net
                        else:
                            pi["gross_loss"] += abs(net)
                        if op["mode"] == "expiry":
                            expiry_pnl += net
                            expiry_trades += 1
                        else:
                            base_pnl += net
                            base_trades += 1
                        equity.append(equity[-1] + net)
                        strat.capital = equity[-1]
                        eq_curve.append({"t": d["ts"].isoformat(), "v": equity[-1]})
                open_pos.pop(idx, None)  # force flat at close (intraday)
            di += 1
        day += timedelta(days=1)
    wins = sum(1 for t in all_trades if t["net"] > 0)
    gw = sum(t["net"] for t in all_trades if t["net"] > 0)
    gl = sum(-t["net"] for t in all_trades if t["net"] <= 0)
    peak, maxdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        maxdd = max(maxdd, (peak - v) / peak * 100)
    return {"trades": all_trades, "per_index": per_index, "base_pnl": base_pnl,
            "expiry_pnl": expiry_pnl, "base_trades": base_trades,
            "expiry_trades": expiry_trades, "total_trades": len(all_trades),
            "wins": wins, "win_rate": (wins / len(all_trades) * 100) if all_trades else 0,
            "net_pnl": sum(t["net"] for t in all_trades),
            "profit_factor": (gw / gl) if gl > 0 else (float("inf") if gw > 0 else 0),
            "avg_win": (gw / wins) if wins else 0,
            "avg_loss": (gl / (len(all_trades) - wins)) if all_trades and len(all_trades) > wins else 0,
            "max_dd_pct": maxdd, "equity_curve": eq_curve,
            "final_equity": equity[-1], "capital": capital}
