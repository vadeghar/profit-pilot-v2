"""Backtest for the trend breakout: whole shares, delivery charges, a small account.

Same data and money rules as the momentum rotation (Yahoo adjusted daily candles for today's Nifty 500 members,
decisions at a close traded at the next open with slippage, whole shares, idle cash earning nothing, delivery
charges on every order). The day-by-day simulator is the one in trading_strategies/stock_pullback.

    python -m investment_strategies.trend_breakout.backtest                 # the rules as specified
    python -m investment_strategies.trend_breakout.backtest --study all

Studies: ``baseline`` the specified rules on the whole period and its halves against the Nifty 50, ``variants``
one rule changed at a time (list fixed in ``VARIANTS``), ``account`` capital, brokerage and slippage, ``picks``
what it holds, ``overlap`` how it moves with the momentum rotation.

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
from investment_strategies.momentum_rotation.backtest import Costs, stats
from investment_strategies.trend_breakout.strategy import Params, indicators
from trading_strategies.stock_pullback import backtest as engine
from trading_strategies.stock_pullback.strategy import market_ok

CAPITAL = 65_000.0
WINDOWS = engine.WINDOWS
FREE = engine.FREE


class Market(engine.Market):
    def signals(self, params: Params) -> dict:
        key = replace(params, slots=0, universe_size=0)
        if key not in self._indicators:
            sig = indicators(self.close, self.high, self.low, self.turnover, params)
            if params.market_exit:
                falling = ~market_ok(self.nifty, params)
                sig["exit_ok"] = sig["exit_ok"] | np.repeat(falling.to_numpy()[:, None], sig["exit_ok"].shape[1], axis=1)
            self._indicators[key] = sig
        return self._indicators[key]


def run(market: Market, params: Params = Params(), costs: Costs = Costs(), capital: float = CAPITAL,
        start: str = WINDOWS[0][1], end: str = WINDOWS[0][2], whole_shares: bool = True) -> dict:
    return engine.run(market, params, costs, capital, start, end, whole_shares)


def row(market: Market, params: Params, costs: Costs = Costs(), capital: float = CAPITAL, whole_shares: bool = True) -> dict:
    out = {}
    for name, lo, hi in WINDOWS:
        s = engine.summary(run(market, params, costs, capital, lo, hi, whole_shares))
        out.update({f"{name}_{k}": s.get(k) for k in ("cagr", "max_dd", "sharpe", "trades", "win_rate")})
    return out


# ---------------------------------------------------------------- studies
def study_baseline(market: Market, params: Params = Params()) -> dict:
    out = {"windows": []}
    for name, lo, hi in WINDOWS:
        result = run(market, params, start=lo, end=hi)
        equity = result["equity"]
        nifty = stats(market.nifty.loc[equity.index[0]:equity.index[-1]])
        out["windows"].append({"window": name, **engine.summary(result), "nifty_cagr": nifty["cagr"], "nifty_dd": nifty["max_dd"]})
        if name == "full":
            out["yearly"] = {y: {"strategy": v, "nifty": nifty["yearly"].get(y)} for y, v in stats(equity, CAPITAL)["yearly"].items()}
            drawdown = equity / equity.cummax() - 1
            under = (drawdown < 0).astype(int)
            out["now"] = {"equity": round(equity.iloc[-1]), "peak_date": equity.idxmax().date().isoformat(),
                          "current_drawdown_pct": round(100 * float(drawdown.iloc[-1]), 1),
                          "deepest_drawdown_bottom": drawdown.idxmin().date().isoformat(),
                          "longest_days_below_high": int(under.groupby((under != under.shift()).cumsum()).sum().max())}
            out["rolling"] = {}
            for years in (1, 3, 5):
                roll = ((equity / equity.shift(252 * years)) ** (1 / years) - 1).dropna()
                out["rolling"][f"{years}y"] = {"min": round(100 * float(roll.min()), 1), "median": round(100 * float(roll.median()), 1),
                                               "below_zero_pct": round(100 * float((roll < 0).mean())), "at_least_20_pct": round(100 * float((roll >= 0.2).mean()))}
    gross = engine.summary(run(market, params, FREE, 1e7, whole_shares=False))
    out["before_costs"] = {k: gross.get(k) for k in ("cagr", "max_dd", "avg_trade_pct", "win_rate", "trades")}
    return out


VARIANTS: Dict[str, dict] = {
    "baseline: 52-week high, exit below 50-day low, 6 slots": {},
    "entry: 6-month high": {"entry_days": 126}, "entry: 55-day high": {"entry_days": 55},
    "exit: below 20-day low": {"exit_days": 20}, "exit: below 100-day low": {"exit_days": 100},
    "exit: below 50-day average": {"exit": "average"}, "exit: below 200-day average": {"exit": "average", "exit_days": 200},
    "exit: chandelier, 3 ATR from the 22-day high": {"exit": "chandelier", "exit_days": 22},
    "market: ignore": {"market_ma": 0}, "market: sell everything below the 200-day": {"market_exit": True},
    "4 slots": {"slots": 4}, "8 slots": {"slots": 8}, "10 slots": {"slots": 10},
    "universe: top 100": {"universe_size": 100}, "universe: top 300": {"universe_size": 300},
    "buy the most traded first": {"rank": "turnover"},
}


def study_variants(market: Market) -> List[dict]:
    return [{"variant": name, **row(market, replace(Params(), **change))} for name, change in VARIANTS.items()]


def study_account(market: Market, params: Params = Params()) -> List[dict]:
    rows = [{"account": f"Rs {int(c):,}, Rs 20 an order", **row(market, params, capital=c)} for c in (50_000.0, 65_000.0, 80_000.0, 500_000.0)]
    rows.append({"account": "Rs 65,000, no brokerage", **row(market, params, replace(Costs(), brokerage_per_order=0.0))})
    rows.append({"account": "Rs 65,000, 0.5% brokerage", **row(market, params, replace(Costs(), brokerage_pct=0.005))})
    rows.append({"account": "Rs 65,000, slippage 0.3% a side", **row(market, params, replace(Costs(), slippage=0.003))})
    rows.append({"account": "no costs, fractional shares", **row(market, params, FREE, 1e7, whole_shares=False)})
    return rows


def study_picks(market: Market, data_root: Path, params: Params = Params()) -> dict:
    result = run(market, params)
    trades = pd.DataFrame(result["trades"])
    industry = dataset.members(data_root).set_index("Symbol")["Industry"]
    weight = trades.assign(industry=trades["symbol"].map(industry)).groupby("industry")["days"].sum()
    by_year = trades.groupby(trades["buy"].str[:4]).agg(trades=("net", "size"), win_rate=("net", lambda s: round(100 * (s > 0).mean())),
                                                        avg_pct=("return_pct", "mean")).round(1)
    winners = trades.sort_values("net", ascending=False)
    return {"sector_share_of_holding_days_pct": (100 * weight / weight.sum()).sort_values(ascending=False).round(1).head(10).to_dict(),
            "distinct_stocks": int(trades["symbol"].nunique()), "hold_days_median": float(trades["days"].median()),
            "hold_days_quartiles": [float(trades["days"].quantile(q)) for q in (0.25, 0.75)],
            "return_pct_quantiles": {str(q): round(float(trades["return_pct"].quantile(q)), 1) for q in (0.05, 0.25, 0.5, 0.75, 0.95)},
            "share_of_profit_from_top_10pct_of_trades": round(100 * float(winners["net"].head(max(1, len(trades) // 10)).sum() / trades["net"].sum())),
            "best_trades": trades.nlargest(8, "return_pct")[["symbol", "buy", "sell", "days", "return_pct"]].round(1).to_dict("records"),
            "worst_trades": trades.nsmallest(8, "return_pct")[["symbol", "buy", "sell", "days", "return_pct"]].round(1).to_dict("records"),
            "by_year": by_year.to_dict("index"), "avg_positions": round(result["avg_positions"], 1),
            "days_in_market_pct": round(result["days_in_market_pct"], 1)}


def study_overlap(market: Market, data_root: Path, params: Params = Params()) -> dict:
    """Is this the momentum rotation again? Monthly returns of the two side by side, and a half-and-half account."""
    a = rotation.run(rotation.Market.load(data_root))["equity"]
    b = run(market, params)["equity"]
    monthly = lambda e: e.groupby([e.index.year, e.index.month]).last().pct_change().dropna()
    ma, mb = monthly(a), monthly(b)
    both = pd.concat([ma, mb], axis=1, keys=["rotation", "breakout"]).dropna()
    half = (a / a.iloc[0] + b / b.iloc[0]) / 2
    yearly = pd.DataFrame({"rotation": stats(a, CAPITAL)["yearly"], "breakout": stats(b, CAPITAL)["yearly"]})
    return {"monthly_correlation": round(float(both.corr().iloc[0, 1]), 2),
            "months_both_down_pct": round(100 * float(((both < 0).all(axis=1)).mean()), 1),
            "half_and_half": {k: v for k, v in stats(half).items() if k != "yearly"},
            "rotation": {k: v for k, v in stats(a, CAPITAL).items() if k in ("cagr", "max_dd", "sharpe")},
            "breakout": {k: v for k, v in stats(b, CAPITAL).items() if k in ("cagr", "max_dd", "sharpe")},
            "years_both_lost": [int(y) for y, r in yearly.iterrows() if r["rotation"] < 0 and r["breakout"] < 0]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--study", choices=("baseline", "variants", "account", "picks", "overlap", "all"), default="baseline")
    ap.add_argument("--data-root", type=Path, default=dataset.DEFAULT_DATA_ROOT)
    ap.add_argument("--out", type=Path, help="write the results as JSON (default: <data-root>/backtests/)")
    args = ap.parse_args()
    pd.set_option("display.width", 320)
    pd.set_option("display.max_columns", 60)
    market = Market(args.data_root)
    print(f"{market.close.shape[1]} stocks, {market.close.index[0]:%Y-%m-%d} -> {market.close.index[-1]:%Y-%m-%d}\n")
    results: dict = {"generated": datetime.now().isoformat(timespec="seconds"), "capital": CAPITAL, "params": asdict(Params()), "costs": asdict(Costs())}
    for study in (("baseline", "variants", "account", "picks", "overlap") if args.study == "all" else (args.study,)):
        print(f"== {study} ==")
        if study == "baseline":
            res = study_baseline(market)
            print(pd.DataFrame(res["windows"]).T.to_string(header=False))
            print(pd.DataFrame(res["yearly"]).to_string())
            print(json.dumps({k: res[k] for k in ("now", "rolling", "before_costs")}, default=float))
        elif study == "variants":
            res = study_variants(market)
            print(pd.DataFrame(res).to_string(index=False))
        elif study == "account":
            res = study_account(market)
            print(pd.DataFrame(res).to_string(index=False))
        elif study == "picks":
            res = study_picks(market, args.data_root)
            print(json.dumps(res, indent=1, default=str))
        else:
            res = study_overlap(market, args.data_root)
            print(json.dumps(res, indent=1, default=float))
        results[study] = res
        print()
    out = args.out or Path(args.data_root) / "backtests" / f"trend_breakout_{args.study}_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1, default=str))
    print(f"results written to {out}")


if __name__ == "__main__":
    main()
