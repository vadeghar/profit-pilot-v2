"""Backtest for the volume shock strategy (the high-volume return premium): whole shares, delivery charges, a small account.

Same data, account and charges as the momentum rotation, and its month-by-month simulator; only the ranking
rule differs.

    python -m investment_strategies.volume_shock.backtest                 # the signal test and the rules as specified
    python -m investment_strategies.volume_shock.backtest --study all

Studies: ``events`` what every stock did over the next 20 days by the size of its volume shock, with no account
in the way; ``baseline`` the specified rules on the whole period and its halves; ``variants`` one rule changed
at a time (list fixed in ``VARIANTS``).

Windows: ``early`` 2011-2018 is where a choice between variants may be made; ``late`` 2019 onward is read after.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

from investment_strategies.momentum_rotation import backtest as rotation
from investment_strategies.momentum_rotation import data as dataset
from investment_strategies.momentum_rotation.backtest import CAPITAL, WINDOWS, Costs, Market, stats, trade_stats
from investment_strategies.momentum_rotation.strategy import month_ends, universe
from investment_strategies.volume_shock.strategy import Params, ranked, shock_ratio

BUCKETS = ((0.0, 0.5, "below 0.5x"), (0.5, 0.8, "0.5x to 0.8x"), (0.8, 1.25, "0.8x to 1.25x (normal)"), (1.25, 2.0, "1.25x to 2x"),
           (2.0, 3.0, "2x to 3x"), (3.0, 1e9, "3x and above"))


def run(market: Market, params: Params = Params(), costs: Costs = Costs(), capital: float = CAPITAL,
        start: str = WINDOWS[0][1], end: str = WINDOWS[0][2], whole_shares: bool = True) -> dict:
    ranker = lambda m, day, names: ranked(m.turnover, m.close, day, names, params)
    return rotation.run(market, params.rotation(), costs, capital, start, end, whole_shares, ranker)


def row(market: Market, params: Params, costs: Costs = Costs()) -> dict:
    out = {}
    for name, lo, hi in WINDOWS:
        result = run(market, params, costs, start=lo, end=hi)
        s, t = stats(result["equity"], CAPITAL), trade_stats(result)
        out.update({f"{name}_cagr": s["cagr"], f"{name}_dd": s["max_dd"], f"{name}_sharpe": s["sharpe"], f"{name}_trades": t.get("trades", 0)})
    return out


# ---------------------------------------------------------------- studies
def study_events(market: Market, params: Params = Params(), horizon: int = 20, step: int = 5) -> List[dict]:
    """Every universe stock every ``step`` trading days, grouped by its volume shock: return from the next open
    to the close ``horizon`` days later, minus the average of the universe over the same days.

    Each date's group average is one observation, so the t-statistic is across dates and is not inflated by the
    many stocks that share a date. ``up`` / ``down`` split the shocks of 2x or more by the price move during them."""
    members = pd.DataFrame(False, index=market.close.index, columns=market.close.columns)
    ends = month_ends(market.close.index)
    for start, stop in zip(ends, ends[1:] + [market.close.index[-1] + pd.Timedelta(days=1)]):
        names = universe(market.turnover, market.close, start, params.rotation())
        members.loc[(members.index > start) & (members.index <= stop), names] = True
    shock = shock_ratio(market.turnover, params.recent_days, params.base_days).where(members)
    forward = (market.close.shift(-horizon) / market.open.shift(-1) - 1.0).where(members)
    excess = forward.sub(forward.mean(axis=1), axis=0)
    move = market.close / market.close.shift(params.recent_days) - 1.0
    dates = market.close.index[::step]
    groups = [(label, (shock >= lo) & (shock < hi)) for lo, hi, label in BUCKETS]
    groups += [("2x and above, price up during the shock", (shock >= 2.0) & (move > 0)),
               ("2x and above, price down during the shock", (shock >= 2.0) & (move < 0))]
    rows = []
    for window, lo, hi in WINDOWS:
        for label, mask in groups:
            per_date = excess.where(mask).loc[dates].loc[lo:hi].mean(axis=1).dropna()
            count = int(mask.loc[dates].loc[lo:hi].sum().sum())
            if len(per_date) > 2:
                rows.append({"window": window, "volume_shock": label, "stock_weeks": count, "dates": len(per_date),
                             "avg_excess_pct": round(100 * float(per_date.mean()), 2),
                             "dates_ahead_pct": round(100 * float((per_date > 0).mean()), 1),
                             "t_stat": round(float(per_date.mean() / (per_date.std(ddof=1) / len(per_date) ** 0.5)), 2)})
    return rows


def study_baseline(market: Market, params: Params = Params()) -> dict:
    out = {"windows": []}
    for name, lo, hi in WINDOWS:
        result = run(market, params, start=lo, end=hi)
        equity = result["equity"]
        nifty = stats(market.bench["NIFTY"].loc[equity.index[0]:equity.index[-1]])
        s = stats(equity, CAPITAL)
        out["windows"].append({"window": name, **{k: v for k, v in s.items() if k != "yearly"}, **trade_stats(result),
                               "nifty_cagr": nifty["cagr"], "nifty_dd": nifty["max_dd"]})
        if name == "full":
            out["yearly"] = {y: {"strategy": v, "nifty": nifty["yearly"].get(y)} for y, v in s["yearly"].items()}
            holdings = [len(p["hold"]) for p in result["picks"] if p["risk_on"]]
            out["avg_holdings_when_invested"] = round(float(np.mean(holdings)), 1)
            out["months_with_fewer_than_6"] = round(100 * float(np.mean(np.array(holdings) < params.top_n)))
            out["current_drawdown_pct"] = round(100 * float(equity.iloc[-1] / equity.max() - 1), 1)
    free = run(market, params, Costs(0, 0, 0, 0, 0, 0, 0, 0), 1e7, whole_shares=False)
    out["before_costs_cagr"] = stats(free["equity"], 1e7)["cagr"]
    return out


VARIANTS: Dict[str, dict] = {
    "baseline: 5-day shock of 2x or more, 6 largest, monthly": {},
    "shock of 1.5x or more": {"min_shock": 1.5}, "shock of 3x or more": {"min_shock": 3.0},
    "1-day shock": {"recent_days": 1}, "20-day shock": {"recent_days": 20},
    "only shocks with the price up": {"direction": "up"}, "only shocks with the price down": {"direction": "down"},
    "hold 3 months": {"rebalance_months": 3},
    "no market filter": {"regime": "none"},
    "4 stocks": {"top_n": 4}, "10 stocks": {"top_n": 10},
    "universe: top 100": {"universe_size": 100}, "universe: top 300": {"universe_size": 300},
}


def study_variants(market: Market) -> List[dict]:
    return [{"variant": name, **row(market, replace(Params(), **change))} for name, change in VARIANTS.items()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--study", choices=("events", "baseline", "variants", "all"), default="all")
    ap.add_argument("--data-root", type=Path, default=dataset.DEFAULT_DATA_ROOT)
    ap.add_argument("--out", type=Path, help="write the results as JSON (default: <data-root>/backtests/)")
    args = ap.parse_args()
    pd.set_option("display.width", 320)
    pd.set_option("display.max_columns", 60)
    market = Market.load(args.data_root)
    print(f"{market.close.shape[1]} stocks, {market.close.index[0]:%Y-%m-%d} -> {market.close.index[-1]:%Y-%m-%d}\n")
    results: dict = {"generated": datetime.now().isoformat(timespec="seconds"), "capital": CAPITAL, "params": asdict(Params()), "costs": asdict(Costs())}
    for study in (("events", "baseline", "variants") if args.study == "all" else (args.study,)):
        print(f"== {study} ==")
        if study == "events":
            res = study_events(market)
            print(pd.DataFrame(res).to_string(index=False))
        elif study == "baseline":
            res = study_baseline(market)
            print(pd.DataFrame(res["windows"]).T.to_string(header=False))
            print(pd.DataFrame(res["yearly"]).to_string())
            print({k: v for k, v in res.items() if k not in ("windows", "yearly")})
        else:
            res = study_variants(market)
            print(pd.DataFrame(res).to_string(index=False))
        results[study] = res
        print()
    out = args.out or Path(args.data_root) / "backtests" / f"volume_shock_{args.study}_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1, default=str))
    print(f"results written to {out}")


if __name__ == "__main__":
    main()
