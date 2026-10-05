"""Backtest for NIFTY Afternoon Momentum, and the family comparison that led to it.

Signals come from real NIFTY 5-minute candles. Option premiums are *modelled* (pricing.py) because no
weekly-option candle history is on disk; charges are the real table in backtest/charges.py. One lot,
fixed Rs 50,000 capital, no compounding.

    python -m trading_strategies.nifty_afternoon_momentum.backtest                 # the strategy as specified
    python -m trading_strategies.nifty_afternoon_momentum.backtest --study all     # + every configuration tried
    python -m trading_strategies.nifty_afternoon_momentum.backtest --data-root D:/Work/automation_engines/data

Studies: ``final`` the specified rules, ``families`` noise band vs opening-range breakout vs afternoon
momentum (10 configs), ``grid`` band width x exit x VIX gate (27), ``gates`` kind of volatility gate and
call/put split (8), ``hourly`` the afternoon effect on the index alone over ~3 years of hourly candles.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

from backtest.charges import ChargeConfig, Fill, option_charges
from market_data.expiries import nifty_lot_size
from market_data.trading_days import TradingCalendar
from trading_strategies.nifty_afternoon_momentum import data as dataset
from trading_strategies.nifty_afternoon_momentum.pricing import bs_price, shifted_iv, variance_time_years
from trading_strategies.nifty_afternoon_momentum.strategy import (BARS_PER_DAY, Params, closing_bar_index, decide,
                                                                 noise_sigma, pick_strike, weekly_expiry)

CAPITAL = 50_000.0
CALENDAR = TradingCalendar()
PERIODS = (("2025", "2025-01-01", "2025-12-31"), ("2026H1", "2026-01-01", "2026-06-30"), ("2026H2", "2026-07-01", "2026-12-31"))


@dataclass(frozen=True)
class Costs:
    """Fill and pricing assumptions. ``HOUSE`` matches the scalper engine's no-quote slippage (Rs 0.5)."""
    slip_abs: float = 0.5          # rupees of premium paid per side, at least
    slip_pct: float = 0.005        # ... or this share of the premium, whichever is larger
    brokerage_per_order: float = 20.0
    iv_mult: float = 1.0           # ATM weekly IV as a multiple of India VIX
    iv_beta: float = 3.0           # relative IV change per unit of index return

    def slip(self, premium: float) -> float:
        return max(self.slip_abs, self.slip_pct * premium)


HOUSE = Costs()
TIGHT = Costs(slip_abs=0.15, slip_pct=0.001)


@dataclass
class Day:
    day: date
    bars: Dict[int, tuple]         # candle index -> (open, high, low, close)
    open: float
    prev_close: float
    vix: float                     # previous session's India VIX close
    history: List[Dict[int, float]]  # abs-move profiles of the earlier sessions (shared, do not mutate)
    n_history: int

    def sigma(self, index: int, lookback: int = 14) -> Optional[float]:
        return noise_sigma(self.history[:self.n_history], index, lookback)


def prepare_days(candles: pd.DataFrame, daily_close: pd.Series, vix_close: pd.Series, lookback: int = 14) -> List[Day]:
    """Sessions that have a previous close (within 5 days), a previous VIX close and ``lookback`` sessions of history."""
    candles = candles.assign(d=candles["t"].dt.date,
                             i=(candles["t"].dt.hour * 60 + candles["t"].dt.minute - 555) // 5)
    close_days, vix_days = sorted(daily_close.index), sorted(vix_close.index)
    history: List[Dict[int, float]] = []
    days: List[Day] = []
    for d, g in candles.groupby("d"):
        bars = {int(r.i): (r.o, r.h, r.l, r.c) for r in g.itertuples()}
        if 0 not in bars:
            continue
        day_open = bars[0][0]
        prev_c = [x for x in close_days if x < d]
        prev_v = [x for x in vix_days if x < d]
        if prev_c and prev_v and len(history) >= lookback and (d - prev_c[-1]).days <= 5:
            days.append(Day(d, bars, day_open, float(daily_close[prev_c[-1]]), float(vix_close[prev_v[-1]]),
                            history, len(history)))
        history.append({i: abs(b[3] / day_open - 1.0) for i, b in bars.items()})
    return days


class Simulator:
    """Buys one option at a candle close and walks it forward on modelled premiums."""

    def __init__(self, costs: Costs = HOUSE, params: Params = Params()):
        self.costs, self.p = costs, params
        self.charge_cfg = ChargeConfig(brokerage_per_order=costs.brokerage_per_order)

    def _premium(self, day: Day, index: int, frac: float, spot: float, strike: int, right: str, expiry: date,
                 iv0: float, spot0: float) -> float:
        t = variance_time_years(day.day, (index + frac) * 5, expiry, CALENDAR)
        return bs_price(spot, strike, t, shifted_iv(iv0, spot, spot0, self.costs.iv_beta), right)

    def trade(self, day: Day, entry_i: int, right: str, exit_fn: Callable[[int, float], Optional[str]],
              last_i: int) -> Optional[dict]:
        """Enter at the close of candle ``entry_i``; ``exit_fn(index, close)`` may return an exit reason at each
        later close. The premium stop is tested against each candle's adverse extreme. Flat at ``last_i``."""
        bars, c = day.bars, self.costs
        spot0 = bars[entry_i][3]
        strike = pick_strike(spot0, right, self.p.itm_steps)
        expiry = weekly_expiry(day.day, self.p.min_dte, CALENDAR)
        iv0 = day.vix / 100.0 * c.iv_mult
        px = lambda i, frac, spot: self._premium(day, i, frac, spot, strike, right, expiry, iv0, spot0)
        mid = px(entry_i, 1.0, spot0)
        buy = mid + c.slip(mid)
        qty = nifty_lot_size(day.day) * self.p.lots
        if buy * qty > CAPITAL * self.p.max_premium_pct:
            return None
        stop = buy * (1.0 - self.p.premium_stop)
        sell = reason = exit_i = None
        for i in range(entry_i + 1, last_i + 1):
            if i not in bars:
                continue
            o, h, l, close = bars[i]
            if px(i, 0.5, l if right == "CE" else h) <= stop:
                sell, reason, exit_i = min(stop, px(i, 0.0, o)), "premium_stop", i
                break
            r = exit_fn(i, close)
            if r or i == last_i:
                sell, reason, exit_i = px(i, 1.0, close), r or "time_exit", i
                break
        if sell is None:  # session data ended early
            exit_i = max(k for k in bars if k <= last_i)
            sell, reason = px(exit_i, 1.0, bars[exit_i][3]), "time_exit"
        sell = max(0.05, sell - c.slip(sell))
        charges = option_charges([Fill("BUY", buy, qty), Fill("SELL", sell, qty)], day.day, self.charge_cfg)["total"]
        gross = (sell - buy) * qty
        return {"date": day.day.isoformat(), "right": right, "strike": strike, "expiry": expiry.isoformat(),
                "entry_bar": entry_i, "exit_bar": exit_i, "spot_in": spot0, "spot_out": bars[exit_i][3],
                "buy": round(buy, 2), "sell": round(sell, 2), "qty": qty, "gross": round(gross, 2),
                "charges": round(charges, 2), "net": round(gross - charges, 2), "reason": reason, "vix": day.vix}


# ---------------------------------------------------------------- signal families
def afternoon(day: Day, sim: Simulator) -> List[dict]:
    """The strategy as specified (strategy.py): one decision at ``decide_time``, flat at ``exit_time``."""
    p = sim.p
    i = closing_bar_index(p.decide_time)
    if i not in day.bars:
        return []
    right = decide(day.prev_close, day.bars[i][3], day.sigma(i, p.sigma_lookback), day.vix, p)
    if right is None:
        return []
    t = sim.trade(day, i, right, lambda j, c: None, closing_bar_index(p.exit_time))
    return [t] if t else []


def _twap(bars: Dict[int, tuple], upto: int) -> float:
    """Session average of typical price - the index has no volume, so this stands in for VWAP."""
    v = [(bars[k][1] + bars[k][2] + bars[k][3]) / 3 for k in range(upto + 1) if k in bars]
    return sum(v) / len(v)


def noise_band(day: Day, sim: Simulator, k: float = 1.0, entry_grid: int = 6, exit_grid: int = 1, exit_style: str = "paper",
               use_vwap: bool = True, max_trades: int = 2, last_i: int = 70) -> List[dict]:
    """Zarattini, Aziz & Barbon (2024): trade a break of the open/previous-close level +- k x noise sigma, trailing
    on max(band, VWAP) (``paper``) or VWAP alone. Decisions from 09:45 to 14:15 on a 30-minute grid by default."""
    if day.vix < sim.p.vix_min:
        return []
    bars, first_i, last_entry = day.bars, 5, 59
    hi, lo = max(day.open, day.prev_close), min(day.open, day.prev_close)
    sig = {i: day.sigma(i) for i in range(BARS_PER_DAY)}
    ub = lambda i: hi * (1 + k * (sig[i] or 0))
    lb = lambda i: lo * (1 - k * (sig[i] or 0))
    out: List[dict] = []
    i = first_i
    while i <= last_entry and len(out) < max_trades:
        if i in bars and sig[i] is not None and (i - first_i) % entry_grid == 0:
            c, vw = bars[i][3], _twap(bars, i)
            d = 1 if (c > ub(i) and (not use_vwap or c > vw)) else -1 if (c < lb(i) and (not use_vwap or c < vw)) else 0
            if d:
                def exit_fn(j, cj, d=d):
                    if (j - first_i) % exit_grid:
                        return None
                    vwj = _twap(bars, j)
                    level = vwj if exit_style == "vwap" else (max(ub(j), vwj) if d > 0 else min(lb(j), vwj))
                    return "trail" if (cj - level) * d < 0 else None
                t = sim.trade(day, i, "CE" if d > 0 else "PE", exit_fn, last_i)
                if t:
                    out.append(t)
                    i = t["exit_bar"] + 6
                    i += (-(i - first_i)) % entry_grid
                    continue
        i += 1
    return out


def opening_range(day: Day, sim: Simulator, or_bars: int = 3, last_entry: int = 27, stop: str = "mid", last_i: int = 70) -> List[dict]:
    """Opening-range breakout: first 5-minute close outside the first ``or_bars`` candles, stopped at the range
    midpoint (``mid``) or its far side (``opp``), otherwise held to the close."""
    bars = day.bars
    if day.vix < sim.p.vix_min or any(k not in bars for k in range(or_bars)):
        return []
    top, bottom = max(bars[k][1] for k in range(or_bars)), min(bars[k][2] for k in range(or_bars))
    for i in range(or_bars, last_entry + 1):
        if i not in bars:
            continue
        d = 1 if bars[i][3] > top else -1 if bars[i][3] < bottom else 0
        if d:
            level = (top + bottom) / 2 if stop == "mid" else (bottom if d > 0 else top)
            t = sim.trade(day, i, "CE" if d > 0 else "PE", lambda j, cj: "range_stop" if (cj - level) * d < 0 else None, last_i)
            return [t] if t else []
    return []


# ---------------------------------------------------------------- metrics
def metrics(trades: List[dict], days: List[Day]) -> dict:
    """Trade and daily statistics on fixed capital; days without a trade count as zero-return days."""
    out = {"trades": len(trades), "sessions": len(days)}
    if not trades:
        return out
    by_day: Dict[str, float] = {}
    for t in trades:
        by_day[t["date"]] = by_day.get(t["date"], 0.0) + t["net"]
    series = [by_day.get(d.day.isoformat(), 0.0) for d in days]
    wins = [t["net"] for t in trades if t["net"] > 0]
    losses = [-t["net"] for t in trades if t["net"] <= 0]
    equity = peak = CAPITAL
    max_dd = 0.0
    for x in series:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
    mean = sum(series) / len(series)
    sd = (sum((x - mean) ** 2 for x in series) / max(1, len(series) - 1)) ** 0.5
    net, years = sum(series), len(series) / 250.0
    out.update(
        win_rate=round(100 * len(wins) / len(trades), 1),
        profit_factor=round(sum(wins) / sum(losses), 2) if losses else None,
        avg_win=round(sum(wins) / len(wins)) if wins else 0,
        avg_loss=round(sum(losses) / len(losses)) if losses else 0,
        net=round(net), return_pct=round(100 * net / CAPITAL, 1),
        annualised_pct=round(100 * net / CAPITAL / years, 1),
        max_dd_pct=round(100 * max_dd, 1), sharpe=round(mean / sd * 252 ** 0.5, 2) if sd else 0.0,
        charges=round(sum(t["charges"] for t in trades)),
        calls=round(sum(t["net"] for t in trades if t["right"] == "CE")),
        puts=round(sum(t["net"] for t in trades if t["right"] == "PE")),
        **{name: round(sum(t["net"] for t in trades if lo <= t["date"] <= hi)) for name, lo, hi in PERIODS})
    return out


def run(days: List[Day], family: Callable[[Day, Simulator], List[dict]], sim: Simulator) -> List[dict]:
    return [t for d in days for t in family(d, sim)]


def _table(rows: List[dict]) -> str:
    return pd.DataFrame(rows).to_string(index=False)


# ---------------------------------------------------------------- studies
def study_final(days: List[Day], params: Params = Params()) -> dict:
    res = {}
    for name, costs in (("house", HOUSE), ("tight", TIGHT)):
        trades = run(days, afternoon, Simulator(costs, params))
        res[name] = {"metrics": metrics(trades, days), "trades": trades}
    return res


def study_families(days: List[Day]) -> List[dict]:
    """Ungated, house costs: is any of the three published signal families enough on its own?"""
    sim = Simulator(HOUSE, Params(vix_min=0.0))
    at = lambda **kw: Simulator(HOUSE, Params(vix_min=0.0, **kw))
    configs = [
        ("noise band, as published (30m in / 30m out)", lambda d, s: noise_band(d, s, exit_grid=6), sim),
        ("noise band, 30m in / 5m out", noise_band, sim),
        ("noise band, 5m in / 5m out", lambda d, s: noise_band(d, s, entry_grid=1), sim),
        ("noise band, no VWAP filter", lambda d, s: noise_band(d, s, use_vwap=False), sim),
        ("opening range 15m, stop mid", opening_range, sim),
        ("opening range 15m, stop far side", lambda d, s: opening_range(d, s, stop="opp"), sim),
        ("opening range 30m, stop mid", lambda d, s: opening_range(d, s, or_bars=6, last_entry=33), sim),
        ("afternoon, threshold 0.5", afternoon, at(threshold=0.5)),
        ("afternoon, threshold 1.0", afternoon, at(threshold=1.0)),
        ("afternoon, threshold 1.5", afternoon, at(threshold=1.5)),
    ]
    return [{"config": name, **metrics(run(days, fam, s), days)} for name, fam, s in configs]


def study_grid(days: List[Day]) -> List[dict]:
    """27 configurations fixed before running: band width x exit x VIX gate, and afternoon threshold x VIX gate."""
    rows = []
    for gate in (0.0, 13.5, 15.0):
        for k in (1.0, 1.5, 2.0):
            for style in ("paper", "vwap"):
                fam = lambda d, s, k=k, style=style: noise_band(d, s, k=k, exit_style=style)
                rows.append(_grid_row(f"noise band k={k} exit={style} vix>={gate}", days, fam, Params(vix_min=gate)))
        for thr in (1.0, 1.5, 2.0):
            rows.append(_grid_row(f"afternoon thr={thr} vix>={gate}", days, afternoon, Params(vix_min=gate, threshold=thr)))
    return rows


def _grid_row(name: str, days: List[Day], fam, params: Params) -> dict:
    h = metrics(run(days, fam, Simulator(HOUSE, params)), days)
    t = metrics(run(days, fam, Simulator(TIGHT, params)), days)
    keep = ("trades", "win_rate", "profit_factor", "net", "max_dd_pct", "sharpe") + tuple(p[0] for p in PERIODS)
    return {"config": name, **{k: h.get(k) for k in keep}, "pf_tight": t.get("profit_factor"), "net_tight": t.get("net")}


def study_gates(days: List[Day], daily: pd.DataFrame, vix_close: pd.Series) -> List[dict]:
    """Which kind of volatility gate, if any, and where the money comes from (calls vs puts)."""
    sma20 = vix_close.rolling(20).mean()
    day_range = (daily["high"] - daily["low"]) / daily["close"]
    range14 = day_range.rolling(14).mean()

    def before(series: pd.Series, d: date) -> Optional[float]:
        s = series[series.index < d].dropna()
        return float(s.iloc[-1]) if len(s) else None

    gates = {
        "none": lambda d: True,
        "VIX >= 15": lambda d: d.vix >= 15,
        "VIX > 1.05 x its 20-day average": lambda d: d.vix > 1.05 * (before(sma20, d.day) or 1e9),
        "yesterday's range > 1.2 x 14-day average": lambda d: (before(day_range, d.day) or 0) > 1.2 * (before(range14, d.day) or 1e9),
    }
    rows = []
    sim = Simulator(HOUSE, Params(vix_min=0.0))
    for gname, gate in gates.items():
        sub = [d for d in days if gate(d)]
        for fname, fam in (("noise band k=1", noise_band), ("afternoon thr=1", afternoon)):
            m = metrics(run(sub, fam, sim), days)
            rows.append({"gate": gname, "family": fname, "gate_days": len(sub),
                         **{k: m.get(k) for k in ("trades", "win_rate", "profit_factor", "net", "max_dd_pct", "calls", "puts")
                            + tuple(p[0] for p in PERIODS)}})
    return rows


def study_hourly() -> List[dict]:
    """Index only, no option model: does the move from the previous close to 14:15 continue to 15:15?
    ~3 years of Yahoo hourly candles (needs network)."""
    hourly = dataset._yahoo("^NSEI", period="730d", interval="1h")
    hourly["t"] = pd.to_datetime(hourly["t"]).dt.tz_localize(None)
    hourly["d"], hourly["hm"] = hourly["t"].dt.date, hourly["t"].dt.strftime("%H:%M")
    closes = dataset._yahoo("^NSEI", period="5y", interval="1d").dropna(subset=["Close"])
    vix = dataset._yahoo("^INDIAVIX", period="5y", interval="1d").dropna(subset=["Close"])
    closes = pd.Series(closes["Close"].values, index=pd.to_datetime(closes["t"]).dt.date)
    vix = pd.Series(vix["Close"].values, index=pd.to_datetime(vix["t"]).dt.date)
    obs = []
    for d, g in hourly.groupby("d"):
        g = g.set_index("hm")
        pc, pv = closes[closes.index < d], vix[vix.index < d]
        if "13:15" not in g.index or "14:15" not in g.index or not len(pc) or not len(pv):
            continue
        at_1415, at_1515 = float(g.loc["13:15", "Close"]), float(g.loc["14:15", "Close"])
        r = at_1415 / float(pc.iloc[-1]) - 1
        obs.append({"year": d.year, "abs_r": abs(r), "vix": float(pv.iloc[-1]), "pts": (at_1515 - at_1415) * (1 if r > 0 else -1)})
    x = pd.DataFrame(obs)
    rows = []
    for name, mask in (("all days", x.abs_r >= 0), ("|move| >= 0.5%", x.abs_r >= 0.005), ("VIX >= 15", x.vix >= 15),
                       ("VIX >= 15 and |move| >= 0.5%", (x.vix >= 15) & (x.abs_r >= 0.005)),
                       ("VIX < 15 and |move| >= 0.5%", (x.vix < 15) & (x.abs_r >= 0.005))):
        y = x[mask]
        rows.append({"subset": name, "days": len(y), "mean_pts": round(y.pts.mean(), 1), "hit_pct": round(100 * (y.pts > 0).mean(), 1),
                     "t_stat": round(y.pts.mean() / (y.pts.std() / len(y) ** 0.5), 2),
                     **{str(yr): round(g.pts.mean(), 1) for yr, g in y.groupby("year")}})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--study", choices=("final", "families", "grid", "gates", "hourly", "all"), default="final")
    ap.add_argument("--data-root", type=Path, default=dataset.DEFAULT_DATA_ROOT)
    ap.add_argument("--refresh", action="store_true", help="rebuild the candle / VIX cache first")
    ap.add_argument("--out", type=Path, help="write the results as JSON (default: <data-root>/backtests/)")
    args = ap.parse_args()
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)

    candles, daily_close, vix_close = dataset.load(args.data_root, refresh=args.refresh)
    days = prepare_days(candles, daily_close, vix_close)
    print(f"{len(days)} tradable sessions, {days[0].day} -> {days[-1].day}; "
          f"{sum(d.vix >= Params().vix_min for d in days)} with VIX >= {Params().vix_min:g}\n")
    wanted = ("final", "families", "grid", "gates", "hourly") if args.study == "all" else (args.study,)
    results: dict = {"generated": datetime.now().isoformat(timespec="seconds"), "capital": CAPITAL,
                     "sessions": len(days), "from": days[0].day.isoformat(), "to": days[-1].day.isoformat(),
                     "params": {k: str(v) for k, v in asdict(Params()).items()},
                     "costs": {"house": asdict(HOUSE), "tight": asdict(TIGHT)}}
    for study in wanted:
        print(f"== {study} ==")
        if study == "final":
            res = study_final(days)
            print(_table([{"costs": k, **v["metrics"]} for k, v in res.items()]))
            print("\nby exit reason (house costs):")
            t = pd.DataFrame(res["house"]["trades"])
            if len(t):
                print(t.groupby("reason").agg(trades=("net", "size"), net=("net", "sum")).round(0).to_string())
        elif study == "families":
            res = study_families(days)
            print(_table(res))
        elif study == "grid":
            res = study_grid(days)
            print(_table(res))
        elif study == "gates":
            daily = pd.read_csv(Path(args.data_root) / dataset.SUBDIR / "nsei_1d.csv", parse_dates=["d"])
            daily["d"] = daily["d"].dt.date
            res = study_gates(days, daily.set_index("d"), vix_close)
            print(_table(res))
        else:
            res = study_hourly()
            print(_table(res))
        results[study] = res
        print()
    out = args.out or Path(args.data_root) / "backtests" / f"nifty_afternoon_momentum_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1, default=str))
    print(f"results written to {out}")


if __name__ == "__main__":
    main()
