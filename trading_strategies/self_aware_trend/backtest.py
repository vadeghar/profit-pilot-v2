"""Backtest for the Self-Aware Trend System on NIFTY: the script as written, then as an intraday option trade.

Signals come from real NIFTY 5-minute candles, resampled to 10 ... 60 minutes. Two layers:

  points  the index itself, in points and in R (multiples of the initial risk) - what the signal is worth
          before anyone has to pay for an instrument.
  rupees  the intraday trades re-priced as bought weekly options (or as a synthetic future), three lots so
          the position can be closed in thirds, on fixed capital. Premiums are *modelled*
          (nifty_afternoon_momentum/pricing.py) because no option candle history is on disk; charges are the
          real table in backtest/charges.py.

    python -m trading_strategies.self_aware_trend.backtest                       # the script's defaults, every timeframe
    python -m trading_strategies.self_aware_trend.backtest --study all
    python -m trading_strategies.self_aware_trend.backtest --study signals --tf 30   # to compare with a TradingView chart

Studies: ``default`` the indicator's own book-keeping per timeframe (positions carry overnight), ``intraday``
the same signals flat by 15:10, ``exits`` six ways of managing the trade, ``filters`` entry filters one at a
time, ``vehicles`` the same trades as bought options against a synthetic future, ``benchmark`` the same plan on
a plain SuperTrend, ``signals`` the last signals with their levels.

The 2026 sessions are split into ``dev`` (Jan-Jun) and ``test`` (Jul onward). Choices are meant to be made on
``dev`` and only then read on ``test``; ``2025`` is a second check on a patchy calendar (about 12 sessions a
month), which the indicator sees as one continuous chart.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from datetime import date, datetime, time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from backtest.charges import ChargeConfig, Fill, option_charges
from market_data.expiries import nifty_lot_size
from market_data.trading_days import TradingCalendar
from trading_strategies.nifty_afternoon_momentum.backtest import HOUSE, TIGHT, Costs
from trading_strategies.nifty_afternoon_momentum.pricing import bs_price, shifted_iv, variance_time_years
from trading_strategies.nifty_afternoon_momentum.strategy import pick_strike, weekly_expiry
from trading_strategies.self_aware_trend import data as dataset
from trading_strategies.self_aware_trend.indicator import Settings, compute
from trading_strategies.self_aware_trend.strategy import INTRADAY, SCRIPT, Plan, simulate

CAPITAL = 100_000.0
LOTS = 3                       # so a third of the position is one lot
CALENDAR = TradingCalendar()
TIMEFRAMES = (5, 10, 15, 20, 25, 30, 45, 60)
HIGHER_TF = {5: 30, 10: 30, 15: 60, 20: 60, 25: 60, 30: 60, 45: 60, 60: 60}
MARGIN_PER_LOT = 180_000.0     # assumed SPAN + exposure for one NIFTY future or its option equivalent
PERIODS = (("2025", "2025-01-01", "2025-12-31"), ("dev", "2026-01-01", "2026-06-30"), ("test", "2026-07-01", "2026-12-31"))

THIRD = 1.0 / 3.0
EXITS: Dict[str, Plan] = {
    "script: thirds at 1R/2R/3R": INTRADAY,
    "thirds, stop to entry after TP1": replace(INTRADAY, breakeven_after_tp1=True),
    "no targets: ride to flip/stop/15:10": replace(INTRADAY, targets=()),
    "1/3 at 1R, rest rides": replace(INTRADAY, targets=((1.0, THIRD),)),
    "1/3 at 1R, stop to entry, rest rides": replace(INTRADAY, targets=((1.0, THIRD),), breakeven_after_tp1=True),
    "all out at 2R": replace(INTRADAY, targets=((2.0, 1.0),)),
}
FILTERS: Dict[str, dict] = {
    "none": {},
    "TQI >= 0.35 (grade B)": {"min_tqi": 0.35},
    "TQI >= 0.50 (grade A)": {"min_tqi": 0.50},
    "with the higher-timeframe trend": {"htf_align": True},
    "entries 09:45-13:30": {"first_entry": time(9, 45), "last_entry": time(13, 30)},
    "VIX >= 15": {"vix_min": 15.0},
    "also join the standing trend at 09:45": {"join_time": time(9, 45)},
    "longs only": {"sides": (1,)},
    "shorts only": {"sides": (-1,)},
}


class Market:
    """The candles, and the indicator on each timeframe (computed once per timeframe and settings)."""

    def __init__(self, candles: pd.DataFrame, vix_close: pd.Series):
        self.candles = candles
        self.days: List[date] = sorted(set(candles["t"].dt.date))
        vix_days = sorted(vix_close.index)
        self.vix: Dict[date, float] = {}
        for d in self.days:
            earlier = [x for x in vix_days if x < d]
            if earlier:
                self.vix[d] = float(vix_close[earlier[-1]])
        self._frames: Dict[tuple, Tuple[pd.DataFrame, np.ndarray]] = {}

    def frame(self, tf: int, settings: Settings = Settings()) -> Tuple[pd.DataFrame, np.ndarray]:
        """(indicator output on ``tf``-minute bars, index of the 5-minute candle each bar closes on)."""
        key = (tf, settings)
        if key not in self._frames:
            bars, closes_on = dataset.resample(self.candles, tf)
            self._frames[key] = (compute(bars, settings, tf), closes_on)
        return self._frames[key]

    def run(self, tf: int, plan: Plan = INTRADAY, settings: Settings = Settings()) -> List[dict]:
        sig, closes_on = self.frame(tf, settings)
        htf_trend = vix = None
        if plan.htf_align:
            higher, higher_closes_on = self.frame(HIGHER_TF[tf], settings)
            last_closed = np.searchsorted(higher_closes_on, closes_on, side="right") - 1
            htf_trend = np.where(last_closed >= 0, higher["trend"].to_numpy()[np.maximum(last_closed, 0)], 0)
        if plan.vix_min > 0:
            vix = np.array([self.vix.get(d, np.nan) for d in sig["t"].dt.date])
        if plan.intraday:
            return simulate(sig, self.candles, closes_on, plan, dataset.BASE_MINUTES, htf_trend, vix)
        return simulate(sig, sig, np.arange(len(sig)), plan, tf, htf_trend, vix)


# ---------------------------------------------------------------- options
def price_options(trades: Sequence[dict], market: Market, costs: Costs = HOUSE, itm_steps: int = 0, min_dte: int = 1,
                  lots: int = LOTS) -> List[dict]:
    """The intraday ``trades`` as bought weekly options: a call for a long, a put for a short, ``lots`` lots.

    Each fill of the index trade sells its share of the lots at the modelled premium for that moment and index
    level. Adds buy, gross, charges and net (rupees). Trades on a day with no earlier VIX close are dropped."""
    cfg = ChargeConfig(brokerage_per_order=costs.brokerage_per_order)
    out = []
    for tr in trades:
        day = date.fromisoformat(tr["date"])
        if day not in market.vix or tr["exit_date"] != tr["date"]:
            continue
        right = "CE" if tr["dir"] > 0 else "PE"
        spot0 = tr["entry"]
        strike = pick_strike(spot0, right, itm_steps)
        expiry = weekly_expiry(day, min_dte, CALENDAR)
        iv0 = market.vix[day] / 100.0 * costs.iv_mult

        def premium(minute: float, spot: float) -> float:
            return bs_price(spot, strike, variance_time_years(day, minute, expiry, CALENDAR),
                            shifted_iv(iv0, spot, spot0, costs.iv_beta), right)

        lot = nifty_lot_size(day)
        mid = premium(tr["entry_minute"], spot0)
        buy = mid + costs.slip(mid)
        fills = [Fill("BUY", buy, lot * lots)]
        for f in tr["fills"]:
            px = premium(f["minute"], f["price"])
            fills.append(Fill("SELL", max(0.05, px - costs.slip(px)), int(round(f["share"] * lots)) * lot))
        if sum(f.quantity for f in fills[1:]) != lot * lots:
            raise ValueError(f"the fills of the {tr['date']} trade do not divide into {lots} lots")
        gross = sum(f.price * f.quantity for f in fills[1:]) - buy * lot * lots
        charges = option_charges(fills, day, cfg)["total"]
        out.append({**tr, "right": right, "strike": strike, "expiry": expiry.isoformat(), "buy": round(buy, 2),
                    "outlay": round(buy * lot * lots), "gross": round(gross, 2), "charges": round(charges, 2),
                    "net": round(gross - charges, 2), "vix": market.vix[day]})
    return out


def price_synthetic(trades: Sequence[dict], market: Market, costs: Costs = HOUSE, min_dte: int = 1, lots: int = LOTS) -> List[dict]:
    """The intraday ``trades`` as synthetic futures: long = buy the ATM call and sell the ATM put, short the reverse.

    The pair moves point for point with the index and its time decay cancels, so the result is the index
    points less four option fills of slippage and charges per round trip. It needs futures-like margin
    (``MARGIN_PER_LOT``) and permission to write options. Same fields as ``price_options``."""
    cfg = ChargeConfig(brokerage_per_order=costs.brokerage_per_order)
    out = []
    for tr in trades:
        day = date.fromisoformat(tr["date"])
        if day not in market.vix or tr["exit_date"] != tr["date"]:
            continue
        d, spot0 = tr["dir"], tr["entry"]
        strike = pick_strike(spot0, "CE")
        expiry = weekly_expiry(day, min_dte, CALENDAR)
        iv0 = market.vix[day] / 100.0 * costs.iv_mult
        lot = nifty_lot_size(day)

        def legs(minute: float, spot: float, quantity: int, opening: bool) -> List[Fill]:
            t = variance_time_years(day, minute, expiry, CALENDAR)
            vol = shifted_iv(iv0, spot, spot0, costs.iv_beta)
            call, put = bs_price(spot, strike, t, vol, "CE"), bs_price(spot, strike, t, vol, "PE")
            bought, sold = (call, put) if (d > 0) == opening else (put, call)
            return [Fill("BUY", bought + costs.slip(bought), quantity), Fill("SELL", max(0.05, sold - costs.slip(sold)), quantity)]

        fills = legs(tr["entry_minute"], spot0, lot * lots, True)
        for f in tr["fills"]:
            fills += legs(f["minute"], f["price"], int(round(f["share"] * lots)) * lot, False)
        if sum(f.quantity for f in fills) != 4 * lot * lots:
            raise ValueError(f"the fills of the {tr['date']} trade do not divide into {lots} lots")
        gross = sum(f.price * f.quantity * (1 if f.side == "SELL" else -1) for f in fills)
        charges = option_charges(fills, day, cfg)["total"]
        out.append({**tr, "right": "CE" if d > 0 else "PE", "strike": strike, "expiry": expiry.isoformat(),
                    "gross": round(gross, 2), "charges": round(charges, 2), "net": round(gross - charges, 2),
                    "vix": market.vix[day]})
    return out


# ---------------------------------------------------------------- metrics
def between(items: Sequence, lo: str, hi: str, key=lambda x: x["date"]) -> list:
    return [x for x in items if lo <= key(x) <= hi]


def metrics_points(trades: Sequence[dict]) -> dict:
    """The signal on the index: R-multiples as the script's dashboard counts them, and points."""
    out = {"trades": len(trades)}
    if not trades:
        return out
    r = np.array([t["r"] for t in trades])
    points = np.array([t["points"] for t in trades])
    equity = np.cumsum(r)
    wins, losses = r[r > 0].sum(), -r[r <= 0].sum()
    spread = points.std(ddof=1) if len(points) > 1 else 0.0
    out.update(win_rate=round(100 * (r > 0).mean(), 1), avg_r=round(r.mean(), 3), total_r=round(r.sum(), 1),
               t_stat=round(points.mean() / (spread / len(points) ** 0.5), 2) if spread else 0.0,
               max_dd_r=round(float((np.maximum.accumulate(np.maximum(equity, 0)) - equity).max()), 1),
               profit_factor=round(wins / losses, 2) if losses else None,
               avg_points=round(points.mean(), 1), total_points=round(points.sum()),
               avg_risk=round(float(np.mean([t["risk"] for t in trades])), 1),
               long_points=round(sum(t["points"] for t in trades if t["dir"] > 0)),
               short_points=round(sum(t["points"] for t in trades if t["dir"] < 0)))
    return out


def metrics_rupees(trades: Sequence[dict], days: Sequence[date], capital: float = CAPITAL) -> dict:
    """Option trades on fixed capital; sessions without a trade count as zero-return days."""
    out = {"trades": len(trades), "sessions": len(days)}
    if not trades or not days:
        return out
    by_day: Dict[str, float] = {}
    for t in trades:
        by_day[t["date"]] = by_day.get(t["date"], 0.0) + t["net"]
    series = np.array([by_day.get(d.isoformat(), 0.0) for d in days])
    net = np.array([t["net"] for t in trades])
    equity = capital + np.cumsum(series)
    peak = np.maximum.accumulate(np.maximum(equity, capital))
    wins, losses = net[net > 0], -net[net <= 0]
    sd = series.std(ddof=1) if len(series) > 1 else 0.0
    out.update(win_rate=round(100 * len(wins) / len(net), 1),
               profit_factor=round(wins.sum() / losses.sum(), 2) if losses.sum() else None,
               avg_win=round(wins.mean()) if len(wins) else 0, avg_loss=round(losses.mean()) if len(losses) else 0,
               net=round(series.sum()), return_pct=round(100 * series.sum() / capital, 1),
               annualised_pct=round(100 * series.sum() / capital / (len(days) / 250.0), 1),
               max_dd_pct=round(100 * float(((peak - equity) / peak).max()), 1),
               sharpe=round(series.mean() / sd * 252 ** 0.5, 2) if sd else 0.0,
               charges=round(sum(t["charges"] for t in trades)),
               calls=round(sum(t["net"] for t in trades if t["right"] == "CE")),
               puts=round(sum(t["net"] for t in trades if t["right"] == "PE")))
    return out


def summary(market: Market, trades: Sequence[dict], costs: Costs = HOUSE, itm_steps: int = 0) -> dict:
    """One row for an intraday run: index points per trade, then the option result overall and per period."""
    pts = metrics_points(trades)
    priced = price_options(trades, market, costs, itm_steps)
    rs = metrics_rupees(priced, market.days)
    row = {k: pts.get(k) for k in ("trades", "win_rate", "avg_r", "avg_points", "t_stat", "total_points")}
    row.update({"opt_" + k: rs.get(k) for k in ("win_rate", "profit_factor", "net", "max_dd_pct", "sharpe")})
    for name, lo, hi in PERIODS:
        row[f"pts_{name}"] = metrics_points(between(trades, lo, hi)).get("total_points", 0)
        row[f"net_{name}"] = metrics_rupees(between(priced, lo, hi), between(market.days, lo, hi, key=str)).get("net", 0)
    return row


# ---------------------------------------------------------------- studies
def study_default(market: Market, settings: Settings = Settings()) -> List[dict]:
    """The script exactly as it keeps its own score: every flip traded, thirds at 1R/2R/3R, held overnight."""
    rows = []
    for tf in TIMEFRAMES:
        sig, _ = market.frame(tf, settings)
        trades = market.run(tf, SCRIPT, settings)
        reasons = pd.Series([t["reason"] for t in trades]).value_counts()
        rows.append({"tf": tf, "preset": settings.resolve(tf).preset, "signals": int((sig["signal"] != 0).sum()),
                     "char_flips": int((sig["char_flip"] & (sig["signal"] != 0)).sum()), **metrics_points(trades),
                     **{f"r_{name}": metrics_points(between(trades, lo, hi)).get("total_r", 0) for name, lo, hi in PERIODS},
                     "overnight": sum(t["exit_date"] != t["date"] for t in trades),
                     **{f"n_{k}": int(reasons.get(k, 0)) for k in ("tp3", "stop", "flip", "timeout")}})
    return rows


def study_intraday(market: Market, settings: Settings = Settings()) -> List[dict]:
    """Same signals and targets, entries 09:20-14:30, flat at 15:10, as three lots of ATM weekly options."""
    return [{"tf": tf, **summary(market, market.run(tf, INTRADAY, settings))} for tf in TIMEFRAMES]


def study_exits(market: Market, settings: Settings = Settings(), timeframes: Sequence[int] = TIMEFRAMES) -> List[dict]:
    return [{"tf": tf, "exit": name, **summary(market, market.run(tf, plan, settings))}
            for tf in timeframes for name, plan in EXITS.items()]


def study_filters(market: Market, tf: int, base: Plan, settings: Settings = Settings()) -> List[dict]:
    return [{"tf": tf, "filter": name, **summary(market, market.run(tf, replace(base, **change), settings))}
            for name, change in FILTERS.items()]


def study_vehicles(market: Market, tf: int, plan: Plan, settings: Settings = Settings()) -> List[dict]:
    """One set of intraday trades carried by different instruments, at house and at tight costs."""
    trades = market.run(tf, plan, settings)
    vehicles = (("buy ATM option", lambda c: price_options(trades, market, c), CAPITAL),
                ("buy option 200 points in the money", lambda c: price_options(trades, market, c, itm_steps=4), CAPITAL),
                ("synthetic future (buy call + sell put)", lambda c: price_synthetic(trades, market, c), MARGIN_PER_LOT * LOTS))
    rows = []
    for name, price, capital in vehicles:
        for cost_name, costs in (("house", HOUSE), ("tight", TIGHT)):
            priced = price(costs)
            m = metrics_rupees(priced, market.days, capital)
            rows.append({"tf": tf, "vehicle": name, "costs": cost_name, "capital": round(capital),
                         **{k: m.get(k) for k in ("trades", "win_rate", "profit_factor", "net", "annualised_pct", "max_dd_pct", "sharpe", "charges")},
                         "per_trade": round(m["net"] / m["trades"]) if m.get("trades") and "net" in m else None,
                         **{f"net_{p}": metrics_rupees(between(priced, lo, hi), between(market.days, lo, hi, key=str), capital).get("net", 0)
                            for p, lo, hi in PERIODS}})
    return rows


PLAIN = dict(use_adaptive=False, use_tqi=False, use_eff_atr=False, use_char_flip=False, mult_smooth=False)
BENCHMARKS: Dict[str, Settings] = {
    "SATS, script defaults": Settings(),
    "plain SuperTrend, ATR 14 x 2.0": Settings(**PLAIN),
    "plain SuperTrend, ATR 14 x 1.5": Settings(preset="Custom", atr_len=14, base_mult=1.5, **PLAIN),
}


def study_benchmark(market: Market, plan: Plan, timeframes: Sequence[int] = (15, 30, 45)) -> List[dict]:
    """Does the adaptive machinery earn anything? The same plan on a SuperTrend with every adaptive part off."""
    rows = []
    for tf in timeframes:
        for name, settings in BENCHMARKS.items():
            trades = market.run(tf, plan, settings)
            pts = metrics_points(trades)
            rows.append({"tf": tf, "indicator": name,
                         **{k: pts.get(k) for k in ("trades", "win_rate", "avg_points", "t_stat", "total_points")},
                         **{f"pts_{p}": metrics_points(between(trades, lo, hi)).get("total_points", 0) for p, lo, hi in PERIODS}})
    return rows


def study_signals(market: Market, tf: int, count: int = 30, settings: Settings = Settings()) -> pd.DataFrame:
    """The last ``count`` signals on ``tf`` minutes with the levels the script would draw, for a chart comparison."""
    sig, _ = market.frame(tf, settings)
    s = sig[sig["signal"] != 0].tail(count)
    stop = np.where(s["signal"] > 0, s["sl_long"], s["sl_short"])
    risk = (s["c"] - stop) * s["signal"]
    out = pd.DataFrame({"bar": s["t"], "side": np.where(s["signal"] > 0, "BUY", "SELL"), "close": s["c"], "sl": stop,
                        **{f"tp{i}": s["c"] + s["signal"] * risk * s[f"tp{i}_r"] for i in (1, 2, 3)},
                        "tqi": s["tqi"], "score": s["score"], "char_flip": s["char_flip"]})
    return out.round(2)


def _table(rows) -> str:
    return (rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)).to_string(index=False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--study", choices=("default", "intraday", "exits", "filters", "vehicles", "benchmark", "signals", "all"), default="default")
    ap.add_argument("--tf", type=int, default=30, choices=TIMEFRAMES, help="timeframe for the filters, vehicles and signals studies")
    ap.add_argument("--exit", default="script: thirds at 1R/2R/3R", choices=tuple(EXITS), help="exit plan for the filters, vehicles and benchmark studies")
    ap.add_argument("--data-root", type=Path, default=dataset.DEFAULT_DATA_ROOT)
    ap.add_argument("--refresh", action="store_true", help="rebuild the candle / VIX cache first")
    ap.add_argument("--out", type=Path, help="write the results as JSON (default: <data-root>/backtests/)")
    args = ap.parse_args()
    pd.set_option("display.width", 320)
    pd.set_option("display.max_columns", 60)

    candles, vix_close = dataset.load(args.data_root, refresh=args.refresh)
    market = Market(candles, vix_close)
    print(f"{len(market.days)} sessions of 5-minute candles, {market.days[0]} -> {market.days[-1]}; "
          + ", ".join(f"{name} {len(between(market.days, lo, hi, key=str))}" for name, lo, hi in PERIODS) + "\n")
    results: dict = {"generated": datetime.now().isoformat(timespec="seconds"), "capital": CAPITAL, "lots": LOTS,
                     "sessions": len(market.days), "from": market.days[0].isoformat(), "to": market.days[-1].isoformat(),
                     "settings": asdict(Settings()), "costs": {"house": asdict(HOUSE), "tight": asdict(TIGHT)}}
    for study in (("default", "intraday", "exits", "filters", "vehicles", "benchmark") if args.study == "all" else (args.study,)):
        print(f"== {study} ==")
        if study == "default":
            res = study_default(market)
        elif study == "intraday":
            res = study_intraday(market)
        elif study == "exits":
            res = study_exits(market)
        elif study == "filters":
            res = study_filters(market, args.tf, EXITS[args.exit])
        elif study == "vehicles":
            res = study_vehicles(market, args.tf, EXITS[args.exit])
        elif study == "benchmark":
            res = study_benchmark(market, EXITS[args.exit])
        else:
            res = study_signals(market, args.tf)
        print(_table(res) + "\n")
        results[study] = res.astype(str).to_dict("records") if isinstance(res, pd.DataFrame) else res
    out = args.out or Path(args.data_root) / "backtests" / f"self_aware_trend_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1, default=str))
    print(f"results written to {out}")


if __name__ == "__main__":
    main()
