"""Backtest for the NIFTY trend-filtered credit spread, on recorded option prices.

Every price is a recorded 5-minute close of the option itself, downloaded from Breeze (data.py): no premium
model. A close is a last traded price, so slippage is added on each of the four fills; charges are the real
table in backtest/charges.py. One lot of today's size (65) throughout, so the rupee figures say what the
rules would do on today's contract; capital is fixed, profits are not reinvested.

    python -m trading_strategies.nifty_credit_spread.backtest                              # rules as specified
    python -m trading_strategies.nifty_credit_spread.backtest --study all --env-file .env  # fetch what is missing

Studies: ``baseline`` the specified rules on the whole period and its halves, ``variants`` one rule changed at
a time (list fixed in ``VARIANTS``), ``costs`` the slippage assumption.

Windows: ``early`` 2020-2022 is where a choice between variants may be made; ``late`` 2023 onward is read after.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from datetime import date, datetime, time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from backtest.charges import ChargeConfig, Fill, option_charges
from trading_strategies.nifty_afternoon_momentum.backtest import HOUSE, TIGHT, Costs
from trading_strategies.nifty_credit_spread import data as dataset
from trading_strategies.nifty_credit_spread.strategy import Params, cycles, manage, strikes, trend_side

CAPITAL = 65_000.0
LOT = 65
WINDOWS = (("full", "2020-01-01", "2026-12-31"), ("early", "2020-01-01", "2022-12-31"), ("late", "2023-01-01", "2026-12-31"))
MID = Costs(slip_abs=0.3, slip_pct=0.003)          # between the scalper engine's no-quote assumption and a tight book
NO_SLIPPAGE = Costs(slip_abs=0.0, slip_pct=0.0)


class Market:
    def __init__(self, data_root: Path, env_path: Optional[str] = None):
        candles = dataset.load_index(data_root)
        self.close = pd.Series(candles["c"].to_numpy(), index=pd.DatetimeIndex(candles["t"]))
        self.days: List[date] = sorted(set(self.close.index.date))
        self.daily, self.vix = dataset.load_daily(data_root)
        self.quotes = dataset.Quotes(data_root, env_path)
        self.cycles = cycles(self.days)


def trade(market: Market, entry_day: date, expiry: date, params: Params, costs: Costs = MID) -> dict:
    """One weekly cycle. Returns the trade, or {"skipped": reason}."""
    base = {"date": entry_day.isoformat(), "expiry": expiry.isoformat()}
    right = trend_side(market.daily, entry_day, params)
    vix = market.vix[market.vix.index < entry_day]
    if right is None or vix.empty:
        return {**base, "skipped": "no history"}
    if vix.iloc[-1] < params.vix_min:
        return {**base, "skipped": "vix"}
    start = pd.Timestamp(datetime.combine(entry_day, params.entry_time)) - pd.Timedelta(minutes=5)
    end = pd.Timestamp(datetime.combine(expiry, params.exit_time)) - pd.Timedelta(minutes=5)
    index = market.close.loc[start:end]
    if index.empty or index.index[0] != start:
        return {**base, "skipped": "no index candle at entry"}
    spot = float(index.iloc[0])
    short_k, long_k = strikes(spot, float(vix.iloc[-1]), (expiry - entry_day).days + 0.2, right, params)
    legs = []
    for strike in (short_k, long_k):
        closes = market.quotes.closes(entry_day, expiry, strike, right)
        closes = closes.loc[pd.Timestamp(entry_day):] if len(closes) else closes
        legs.append(closes.reindex(closes.index.union(index.index)).ffill().reindex(index.index).to_numpy())
    short, long_ = legs
    if np.isnan(short[0]) or np.isnan(long_[0]):
        return {**base, "skipped": "no option quote at entry"}
    credit = short[0] - long_[0]
    if credit <= max(params.min_credit, 0.0):
        return {**base, "skipped": "credit too small"}
    value = np.clip(short - long_, 0.0, params.width)
    i, reason = manage(value, credit, params)
    quantity = LOT * params.lots
    sell_short, buy_long = short[0] - costs.slip(short[0]), long_[0] + costs.slip(long_[0])
    long_exit = max(long_[i], short[i] - params.width)       # a stale far-leg quote cannot make the spread cost more than its width
    buy_short, sell_long = short[i] + costs.slip(short[i]), max(0.05, long_exit - costs.slip(long_exit))
    fills = [Fill("SELL", sell_short, quantity), Fill("BUY", buy_long, quantity), Fill("BUY", buy_short, quantity), Fill("SELL", sell_long, quantity)]
    points = (sell_short - buy_long) - (buy_short - sell_long)
    charges = option_charges(fills[:2], entry_day, ChargeConfig(brokerage_per_order=costs.brokerage_per_order))["total"] \
        + option_charges(fills[2:], index.index[i].date(), ChargeConfig(brokerage_per_order=costs.brokerage_per_order))["total"]
    gross = points * quantity
    return {**base, "right": right, "spot": round(spot, 1), "vix": round(float(vix.iloc[-1]), 2), "short": short_k, "long": long_k,
            "credit": round(float(credit), 2), "exit_value": round(float(value[i]), 2), "reason": reason,
            "exit": index.index[i].isoformat(), "days_held": (index.index[i].date() - entry_day).days,
            "points": round(float(points), 2), "gross": round(gross, 2), "charges": round(charges, 2), "net": round(gross - charges, 2),
            "max_loss": round((params.width - float(credit)) * quantity), "spot_exit": round(float(index.iloc[i]), 1)}


def run(market: Market, params: Params = Params(), costs: Costs = MID, start: str = WINDOWS[0][1], end: str = WINDOWS[0][2]) -> List[dict]:
    return [trade(market, entry, expiry, params, costs) for entry, expiry in market.cycles if start <= entry.isoformat() <= end]


def metrics(results: Sequence[dict], capital: float = CAPITAL) -> dict:
    """Statistics over the weekly cycles of a run; a skipped week counts as a week with no result."""
    trades = [r for r in results if "net" in r]
    out = {"weeks": len(results), "trades": len(trades), "skipped": len(results) - len(trades)}
    if not trades:
        return out
    weekly = np.array([r.get("net", 0.0) for r in results])
    net = np.array([t["net"] for t in trades])
    equity = capital + np.cumsum(weekly)
    peak = np.maximum.accumulate(np.maximum(equity, capital))
    wins, losses = net[net > 0], -net[net <= 0]
    years = (date.fromisoformat(results[-1]["expiry"]) - date.fromisoformat(results[0]["date"])).days / 365.25
    streak = worst_streak = 0
    for x in net:
        streak = streak + 1 if x <= 0 else 0
        worst_streak = max(worst_streak, streak)
    out.update(net=round(net.sum()), annualised_pct=round(100 * net.sum() / capital / years, 1),
               max_dd=round(float((peak - equity).max())), max_dd_pct=round(100 * float(((peak - equity) / peak).max()), 1),
               sharpe=round(float(weekly.mean() / weekly.std(ddof=1) * 52 ** 0.5), 2) if weekly.std(ddof=1) else 0.0,
               win_rate=round(100 * len(wins) / len(net), 1), avg_win=round(wins.mean()) if len(wins) else 0,
               avg_loss=round(losses.mean()) if len(losses) else 0,
               profit_factor=round(wins.sum() / losses.sum(), 2) if losses.sum() else None,
               worst_trade=round(float(net.min())), losing_streak=worst_streak,
               avg_credit=round(float(np.mean([t["credit"] for t in trades])), 1),
               charges=round(sum(t["charges"] for t in trades)), years=round(years, 1))
    return out


def by(results: Sequence[dict], key) -> Dict[str, dict]:
    groups: Dict[str, list] = {}
    for r in results:
        if "net" in r:
            groups.setdefault(str(key(r)), []).append(r["net"])
    return {k: {"trades": len(v), "net": round(sum(v)), "win_rate": round(100 * sum(x > 0 for x in v) / len(v), 1)} for k, v in sorted(groups.items())}


def row(market: Market, params: Params, costs: Costs = MID) -> dict:
    out = {}
    for name, lo, hi in WINDOWS:
        m = metrics(run(market, params, costs, lo, hi))
        out.update({f"{name}_{k}": m.get(k) for k in ("trades", "net", "annualised_pct", "max_dd", "win_rate", "profit_factor", "sharpe")})
    return out


# ---------------------------------------------------------------- studies
def study_baseline(market: Market, params: Params = Params()) -> dict:
    results = run(market, params)
    trades = [r for r in results if "net" in r]
    vix_bucket = lambda r: "<12" if r["vix"] < 12 else "12-15" if r["vix"] < 15 else "15-20" if r["vix"] < 20 else "20+"
    return {"windows": [{"window": name, **metrics(run(market, params, MID, lo, hi))} for name, lo, hi in WINDOWS],
            "by_year": by(results, lambda r: r["date"][:4]), "by_side": by(results, lambda r: r["right"]),
            "by_exit": by(results, lambda r: r["reason"]), "by_vix": by(results, vix_bucket),
            "skips": pd.Series([r["skipped"] for r in results if "skipped" in r]).value_counts().to_dict(),
            "avg_days_held": round(float(np.mean([t["days_held"] for t in trades])), 1),
            "avg_max_loss": round(float(np.mean([t["max_loss"] for t in trades]))),
            "worst": sorted(trades, key=lambda t: t["net"])[:8], "trades": results}


VARIANTS: Dict[str, dict] = {
    "baseline: 50-day trend, 1.0 move, 200 wide, 50% target, 2x stop": {},
    "trend: 20-day average": {"trend_ma": 20}, "trend: 200-day average": {"trend_ma": 200},
    "no trend filter: always put spreads": {"side": "PE"}, "no trend filter: always call spreads": {"side": "CE"},
    "exits: hold to expiry, no target or stop": {"profit_target": 0.0, "stop_multiple": 0.0},
    "exits: stop only (2x)": {"profit_target": 0.0}, "exits: target only (50%)": {"stop_multiple": 0.0},
    "exits: 50% target, 3x stop": {"stop_multiple": 3.0}, "exits: 75% target, 2x stop": {"profit_target": 0.75},
    "short strike 0.75 moves away": {"distance": 0.75}, "short strike 1.25 moves away": {"distance": 1.25},
    "100 wide": {"width": 100}, "300 wide": {"width": 300},
    "skip weeks with VIX below 13": {"vix_min": 13.0},
}


def study_variants(market: Market, only: Optional[Sequence[str]] = None) -> List[dict]:
    return [{"variant": name, **row(market, replace(Params(), **change))} for name, change in VARIANTS.items()
            if only is None or any(name.startswith(o) for o in only)]


def study_costs(market: Market, params: Params = Params()) -> List[dict]:
    return [{"slippage": name, "per_fill": f"Rs {c.slip_abs} or {100 * c.slip_pct:g}%", **row(market, params, c)}
            for name, c in (("none", NO_SLIPPAGE), ("tight", TIGHT), ("middle (used)", MID), ("house", HOUSE))]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--study", choices=("baseline", "variants", "costs", "all"), default="baseline")
    ap.add_argument("--only", nargs="*", help="variants study: names starting with these")
    ap.add_argument("--data-root", type=Path, default=dataset.DEFAULT_DATA_ROOT)
    ap.add_argument("--env-file", help="the .env with a live Breeze session; lets the run fetch missing option candles")
    ap.add_argument("--out", type=Path, help="write the results as JSON (default: <data-root>/backtests/)")
    args = ap.parse_args()
    pd.set_option("display.width", 320)
    pd.set_option("display.max_columns", 60)
    market = Market(args.data_root, args.env_file)
    print(f"{len(market.cycles)} weekly cycles, {market.cycles[0][0]} -> {market.cycles[-1][1]}\n")
    results: dict = {"generated": datetime.now().isoformat(timespec="seconds"), "capital": CAPITAL, "lot": LOT,
                     "params": {k: str(v) for k, v in asdict(Params()).items()}, "costs": asdict(MID)}
    try:
        for study in (("baseline", "costs", "variants") if args.study == "all" else (args.study,)):
            print(f"== {study} ==")
            if study == "baseline":
                res = study_baseline(market)
                print(pd.DataFrame(res["windows"]).T.to_string(header=False))
                for k in ("by_year", "by_side", "by_exit", "by_vix"):
                    print(k, json.dumps(res[k]))
                print("skips", res["skips"], "avg days held", res["avg_days_held"], "avg max loss", res["avg_max_loss"])
            elif study == "variants":
                res = study_variants(market, args.only)
                print(pd.DataFrame(res).to_string(index=False))
            else:
                res = study_costs(market)
                print(pd.DataFrame(res).to_string(index=False))
            results[study] = res
            print(f"({market.quotes.requests} option requests so far)\n")
    except dataset.QuotaExceeded as exc:
        print(f"stopped: {exc} (after {market.quotes.requests} requests; what was fetched is cached)")
    out = args.out or Path(args.data_root) / "backtests" / f"nifty_credit_spread_{args.study}_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1, default=str))
    print(f"results written to {out}")


if __name__ == "__main__":
    main()
