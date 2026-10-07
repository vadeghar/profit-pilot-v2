"""Backtest for the stock pullback strategy: whole shares, delivery charges, a small account.

Same data and money rules as the momentum rotation (investment_strategies/momentum_rotation): Yahoo's adjusted
daily candles for today's Nifty 500 members, decisions at a close traded at the next open with slippage, whole
shares, idle cash earning nothing, delivery charges on every order.

    python -m trading_strategies.stock_pullback.backtest                 # the rules as specified
    python -m trading_strategies.stock_pullback.backtest --study all

Studies: ``baseline`` the specified rules on the whole period and its halves, ``variants`` one rule changed at
a time, before costs (list fixed in ``VARIANTS``), ``costs`` what charges and account size do to it.

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

from investment_strategies.momentum_rotation import data as dataset
from investment_strategies.momentum_rotation.backtest import Costs, stats
from investment_strategies.momentum_rotation.strategy import Params as UniverseParams, month_ends, universe
from trading_strategies.stock_pullback.strategy import Params, indicators, market_ok

CAPITAL = 65_000.0
WINDOWS = (("full", "2011-01-01", "2026-12-31"), ("early", "2011-01-01", "2018-12-31"), ("late", "2019-01-01", "2026-12-31"))
FREE = Costs(0, 0, 0, 0, 0, 0, 0, 0)
MIN_ORDER = 1_000.0            # a slot smaller than this is not opened (flat fees would exceed any gain)


class Market:
    def __init__(self, data_root: Path):
        self.close, self.open, self.turnover, bench = dataset.load(data_root)
        self.high, self.low = dataset.load_field("h", data_root), dataset.load_field("l", data_root)
        self.nifty = bench["NIFTY"].reindex(self.close.index).ffill()
        self._members: Dict[int, pd.DataFrame] = {}
        self._indicators: Dict[Params, dict] = {}

    def members(self, size: int) -> pd.DataFrame:
        """date x symbol: is the stock in the point-in-time most-traded ``size`` (as of the last month-end)."""
        if size not in self._members:
            mask = pd.DataFrame(False, index=self.close.index, columns=self.close.columns)
            ends = month_ends(self.close.index)
            for start, stop in zip(ends, ends[1:] + [self.close.index[-1] + pd.Timedelta(days=1)]):
                names = universe(self.turnover, self.close, start, UniverseParams(universe_size=size))
                mask.loc[(mask.index > start) & (mask.index <= stop), names] = True
            self._members[size] = mask
        return self._members[size]

    def signals(self, params: Params) -> dict:
        key = replace(params, slots=0, universe_size=0, max_hold=0, stop_pct=0.0, market_ma=0)
        if key not in self._indicators:
            self._indicators[key] = indicators(self.close, self.high, self.low, params)
        return self._indicators[key]


def run(market: Market, params: Params = Params(), costs: Costs = Costs(), capital: float = CAPITAL,
        start: str = WINDOWS[0][1], end: str = WINDOWS[0][2], whole_shares: bool = True) -> dict:
    """Simulate ``params`` from ``start`` to ``end``: daily equity, closed trades, fees."""
    sig, members = market.signals(params), market.members(params.universe_size)
    days = market.close.loc[start:end].index
    names = market.close.columns.to_numpy()
    rows = market.close.index.get_indexer(days)
    close, open_ = market.close.to_numpy(), market.open.to_numpy()
    last_close = market.close.ffill().to_numpy()
    setup = (sig["setup"] & members).to_numpy()
    score, exit_ok = sig["score"].to_numpy(), sig["exit_ok"].to_numpy()
    risk_on = market_ok(market.nifty, params).to_numpy()

    cash, held = capital, {}                  # column -> [quantity, cost, entry row, days held]
    equity, trades, fees_paid, traded_value, invested = [], [], 0.0, 0.0, []
    to_sell: List[int] = []
    to_buy: List[int] = []
    for r in rows:
        for col in to_sell:
            price = open_[r, col]
            if col not in held or np.isnan(price):
                continue
            qty, cost, entry_row, _ = held.pop(col)
            proceeds = qty * price * (1 - costs.slippage)
            fee = costs.fees(proceeds, buy=False)
            cash += proceeds - fee
            fees_paid, traded_value = fees_paid + fee, traded_value + proceeds
            trades.append({"symbol": names[col], "buy": market.close.index[entry_row].date().isoformat(), "sell": market.close.index[r].date().isoformat(),
                           "days": int(r - entry_row), "net": proceeds - fee - cost, "return_pct": 100 * ((proceeds - fee) / cost - 1)})
        total = cash + sum(h[0] * last_close[r - 1, c] for c, h in held.items()) if held else cash
        slot = total / params.slots
        for col in to_buy:
            if len(held) >= params.slots:
                break
            price = open_[r, col]
            if col in held or np.isnan(price):
                continue
            price *= 1 + costs.slippage
            budget = min(slot, cash)
            qty = max(0.0, budget - costs.fees(budget, buy=True)) / price
            if whole_shares:
                qty = np.floor(qty)
            if qty <= 0 or qty * price < MIN_ORDER:
                continue
            fee = costs.fees(qty * price, buy=True)
            cash -= qty * price + fee
            fees_paid, traded_value = fees_paid + fee, traded_value + qty * price
            held[col] = [qty, qty * price + fee, r, 0]
        equity.append(cash + sum(h[0] * last_close[r, c] for c, h in held.items()))
        invested.append(len(held))
        # decisions at this close, for the next open
        to_sell = []
        for col, h in held.items():
            h[3] += 1
            stopped = params.stop_pct and close[r, col] <= (h[1] / h[0]) * (1 - params.stop_pct)
            if exit_ok[r, col] or h[3] >= params.max_hold or stopped:
                to_sell.append(col)
        to_buy = []
        if risk_on[r]:
            candidates = [c for c in np.flatnonzero(setup[r]) if c not in held]
            to_buy = sorted(candidates, key=lambda c: (np.inf if np.isnan(score[r, c]) else score[r, c]))
    return {"equity": pd.Series(equity, index=days), "trades": trades, "fees": fees_paid, "traded_value": traded_value,
            "capital": capital, "avg_positions": float(np.mean(invested)), "days_in_market_pct": 100 * float(np.mean(np.array(invested) > 0))}


def summary(result: dict) -> dict:
    s = stats(result["equity"], result["capital"])
    t = pd.DataFrame(result["trades"])
    out = {k: v for k, v in s.items() if k != "yearly"}
    if len(t):
        wins, losses = t[t["net"] > 0], t[t["net"] <= 0]
        out.update(trades=len(t), trades_per_year=round(len(t) / s["years"], 1), win_rate=round(100 * len(wins) / len(t), 1),
                   avg_trade_pct=round(t["return_pct"].mean(), 2), avg_win_pct=round(wins["return_pct"].mean(), 2),
                   avg_loss_pct=round(losses["return_pct"].mean(), 2),
                   profit_factor=round(wins["net"].sum() / -losses["net"].sum(), 2) if losses["net"].sum() else None,
                   worst_trade_pct=round(t["return_pct"].min(), 1), avg_hold_days=round(t["days"].mean(), 1),
                   fees=round(result["fees"]), avg_positions=round(result["avg_positions"], 1),
                   days_in_market_pct=round(result["days_in_market_pct"], 1))
    return out


def row(market: Market, params: Params, costs: Costs = Costs(), capital: float = CAPITAL, whole_shares: bool = True) -> dict:
    out = {}
    for name, lo, hi in WINDOWS:
        s = summary(run(market, params, costs, capital, lo, hi, whole_shares))
        out.update({f"{name}_{k}": s.get(k) for k in ("cagr", "max_dd", "sharpe", "trades", "win_rate", "avg_trade_pct")})
    return out


# ---------------------------------------------------------------- studies
def study_baseline(market: Market, params: Params = Params()) -> dict:
    out = {"windows": []}
    for name, lo, hi in WINDOWS:
        result = run(market, params, start=lo, end=hi)
        nifty = stats(market.nifty.loc[result["equity"].index[0]:result["equity"].index[-1]])
        out["windows"].append({"window": name, **summary(result), "nifty_cagr": nifty["cagr"], "nifty_dd": nifty["max_dd"]})
        if name == "full":
            out["yearly"] = {y: {"strategy": v, "nifty": nifty["yearly"].get(y)} for y, v in stats(result["equity"], CAPITAL)["yearly"].items()}
            t = pd.DataFrame(result["trades"])
            out["trades_by_year"] = t.groupby(t["buy"].str[:4]).agg(trades=("net", "size"), avg_pct=("return_pct", "mean"), net=("net", "sum")).round(2).to_dict("index")
            out["hold_days"] = t["days"].value_counts().sort_index().head(12).to_dict()
            out["worst"] = t.nsmallest(8, "return_pct").round(1).to_dict("records")
    gross = run(market, params, FREE, 1e7, whole_shares=False)
    out["before_costs"] = {k: summary(gross).get(k) for k in ("cagr", "max_dd", "avg_trade_pct", "win_rate", "trades")}
    return out


VARIANTS: Dict[str, dict] = {
    "baseline: RSI(2) < 10, above 200-day, exit above 5-day, 6 slots": {},
    "RSI(2) < 5": {"threshold": 5.0}, "RSI(2) < 15": {"threshold": 15.0},
    "signal: IBS < 0.2": {"signal": "ibs", "threshold": 0.2},
    "no stock trend filter": {"trend_ma": 0}, "stock above 100-day average": {"trend_ma": 100},
    "no market filter": {"market_ma": 0},
    "exit above 3-day average": {"exit_ma": 3}, "exit above 10-day average": {"exit_ma": 10},
    "hold at most 5 days": {"max_hold": 5}, "hold at most 20 days": {"max_hold": 20},
    "10% stop": {"stop_pct": 0.10},
    "4 slots": {"slots": 4}, "10 slots": {"slots": 10},
    "universe: top 100": {"universe_size": 100},
    "buy strongest 6-month stocks first": {"rank": "momentum"},
}


def study_variants(market: Market) -> List[dict]:
    """Each variant before any cost (fractional shares), so the edge per trade can be set against what a trade
    costs: with the specified charges the Rs 65,000 account is run down by every variant alike."""
    return [{"variant": name, **row(market, replace(Params(), **change), FREE, 1e7, whole_shares=False)} for name, change in VARIANTS.items()]


def study_costs(market: Market, params: Params = Params()) -> List[dict]:
    rows = [{"case": "no costs, fractional shares", **row(market, params, FREE, 1e7, whole_shares=False)},
            {"case": "Rs 65,000, no brokerage (taxes and slippage only)", **row(market, params, replace(Costs(), brokerage_per_order=0.0))},
            {"case": "Rs 65,000, Rs 20 an order (specified)", **row(market, params)},
            {"case": "Rs 65,000, 0.5% brokerage", **row(market, params, replace(Costs(), brokerage_pct=0.005))},
            {"case": "Rs 65,000, slippage 0.3% a side", **row(market, params, replace(Costs(), slippage=0.003))}]
    rows += [{"case": f"Rs {int(c):,}, Rs 20 an order", **row(market, params, capital=c)} for c in (50_000.0, 80_000.0, 500_000.0)]
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--study", choices=("baseline", "variants", "costs", "all"), default="baseline")
    ap.add_argument("--data-root", type=Path, default=dataset.DEFAULT_DATA_ROOT)
    ap.add_argument("--out", type=Path, help="write the results as JSON (default: <data-root>/backtests/)")
    args = ap.parse_args()
    pd.set_option("display.width", 320)
    pd.set_option("display.max_columns", 60)
    market = Market(args.data_root)
    print(f"{market.close.shape[1]} stocks, {market.close.index[0]:%Y-%m-%d} -> {market.close.index[-1]:%Y-%m-%d}\n")
    results: dict = {"generated": datetime.now().isoformat(timespec="seconds"), "capital": CAPITAL, "params": asdict(Params()), "costs": asdict(Costs())}
    for study in (("baseline", "costs", "variants") if args.study == "all" else (args.study,)):
        print(f"== {study} ==")
        if study == "baseline":
            res = study_baseline(market)
            print(pd.DataFrame(res["windows"]).T.to_string(header=False))
            print(pd.DataFrame(res["yearly"]).to_string())
            print("before costs:", res["before_costs"])
        elif study == "variants":
            res = study_variants(market)
            print(pd.DataFrame(res).to_string(index=False))
        else:
            res = study_costs(market)
            print(pd.DataFrame(res).to_string(index=False))
        results[study] = res
        print()
    out = args.out or Path(args.data_root) / "backtests" / f"stock_pullback_{args.study}_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1, default=str))
    print(f"results written to {out}")


if __name__ == "__main__":
    main()
