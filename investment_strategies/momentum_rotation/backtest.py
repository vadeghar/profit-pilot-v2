"""Backtest for the momentum rotation: whole shares, delivery charges, a small account.

Prices are Yahoo's adjusted daily candles for today's Nifty 500 members (data.py), so returns include
dividends. Decisions are taken at a month-end close and traded at the next open with slippage. Shares are
whole numbers, cash earns nothing, and every order pays the delivery charges in ``Costs``.

    python -m investment_strategies.momentum_rotation.backtest                    # the rules as specified
    python -m investment_strategies.momentum_rotation.backtest --study all

Studies: ``baseline`` the specified rules on the whole period and its two halves against the Nifty 50 (price index:
add about 1.3% a year for dividends),
``variants`` one rule changed at a time (the list is fixed in ``VARIANTS``), ``account`` capital and brokerage,
``bias`` how much today's member list flatters the result, ``picks`` what it holds and what it would buy now,
``volume`` the rules with a volume filter on the ranked list (V2), ``volume_events`` next-month returns of the
top momentum stocks by how heavily they have been trading.

Windows: ``early`` 2011-2018 is where a choice between variants may be made; ``late`` 2019 onward is read
afterwards. Each window starts with fresh capital, because whole-share and flat-fee drag depend on account size.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from investment_strategies.momentum_rotation import data as dataset
from investment_strategies.momentum_rotation.strategy import (Params, month_ends, risk_on, scores, select, universe, volume_ratio,
                                                             volume_screen)

CAPITAL = 65_000.0
WINDOWS = (("full", "2011-01-01", "2026-12-31"), ("early", "2011-01-01", "2018-12-31"), ("late", "2019-01-01", "2026-12-31"))


@dataclass(frozen=True)
class Costs:
    """Delivery-equity charges. Brokerage is the flat fee, or ``brokerage_pct`` of the order value when that is set."""
    brokerage_per_order: float = 20.0
    brokerage_pct: float = 0.0
    stt: float = 0.001                 # buy and sell
    exchange: float = 0.0000307        # NSE transaction charge + SEBI fee
    stamp_buy: float = 0.00015
    gst: float = 0.18                  # on brokerage + exchange
    dp_per_sell: float = 18.5          # depository charge per stock sold, with GST
    slippage: float = 0.001            # paid on the price, each side

    def fees(self, value: float, buy: bool) -> float:
        brokerage = value * self.brokerage_pct if self.brokerage_pct else self.brokerage_per_order
        exchange = value * self.exchange
        return (brokerage + exchange) * (1 + self.gst) + value * self.stt + (value * self.stamp_buy if buy else self.dp_per_sell)


@dataclass
class Market:
    close: pd.DataFrame
    open: pd.DataFrame
    turnover: pd.DataFrame
    bench: pd.DataFrame

    @classmethod
    def load(cls, data_root: Path, refresh: bool = False) -> "Market":
        return cls(*dataset.load(data_root, refresh))


def run(market: Market, params: Params = Params(), costs: Costs = Costs(), capital: float = CAPITAL,
        start: str = WINDOWS[0][1], end: str = WINDOWS[0][2], whole_shares: bool = True) -> dict:
    """Simulate ``params`` from ``start`` to ``end``. Returns the daily equity, the closed trades and each month's picks."""
    days = market.close.loc[start:end].index
    decisions = {d for d in month_ends(market.close.index, params.rebalance_months) if days[0] <= d <= days[-1]}
    last_close = market.close.ffill()
    cash, shares, opened = capital, {}, {}
    equity, trades, picks, fees_paid, traded_value = [], [], [], 0.0, 0.0
    pending: Optional[List[str]] = None

    def value(day) -> float:
        return cash + sum(q * last_close.at[day, s] for s, q in shares.items())

    for day in days:
        if pending is not None:
            for name in [s for s in shares if s not in pending]:
                price = market.open.at[day, name]
                if np.isnan(price):
                    continue                                   # not trading today: try again next month
                qty = shares.pop(name)
                proceeds = qty * price * (1 - costs.slippage)
                fee = costs.fees(proceeds, buy=False)
                cash += proceeds - fee
                fees_paid, traded_value = fees_paid + fee, traded_value + proceeds
                buy_day, paid = opened.pop(name)
                trades.append({"symbol": name, "buy": buy_day.date().isoformat(), "sell": day.date().isoformat(),
                               "days": (day - buy_day).days, "net": proceeds - fee - paid, "return_pct": 100 * ((proceeds - fee) / paid - 1)})
            slot = value(day) / params.top_n
            for name in [s for s in pending if s not in shares]:
                price = market.open.at[day, name]
                if np.isnan(price):
                    continue
                price *= 1 + costs.slippage
                budget = min(slot, cash)
                qty = max(0.0, budget - costs.fees(budget, buy=True)) / price      # the slot pays for shares and fees
                if whole_shares:
                    qty = np.floor(qty)
                if qty <= 0:
                    continue
                fee = costs.fees(qty * price, buy=True)
                cash -= qty * price + fee
                fees_paid, traded_value = fees_paid + fee, traded_value + qty * price
                shares[name], opened[name] = qty, (day, qty * price + fee)
            pending = None
        equity.append(value(day))
        if day in decisions:
            names = universe(market.turnover, market.close, day, params)
            ranked = scores(market.close, day, names, params)
            if params.volume_rule != "none":
                ranked = volume_screen(ranked, volume_ratio(market.turnover, day, ranked.index), params)
            up = risk_on(market.bench["NIFTY"], day, params)
            slot = equity[-1] / params.top_n
            affordable = (lambda s: market.close.at[day, s] * 1.01 <= slot) if whole_shares else (lambda s: True)
            pending = select(ranked, list(shares), params, up, affordable)
            picks.append({"date": day.date().isoformat(), "risk_on": bool(up), "hold": list(pending), "top": ranked.index[:params.top_n].tolist()})
    final = days[-1]
    open_trades = [{"symbol": s, "buy": opened[s][0].date().isoformat(), "sell": None, "days": (final - opened[s][0]).days,
                    "net": q * last_close.at[final, s] - opened[s][1], "return_pct": 100 * (q * last_close.at[final, s] / opened[s][1] - 1)}
                   for s, q in shares.items()]
    return {"equity": pd.Series(equity, index=days), "trades": trades, "open_trades": open_trades, "picks": picks,
            "fees": fees_paid, "traded_value": traded_value, "capital": capital, "holdings": dict(shares)}


def stats(equity: pd.Series, capital: Optional[float] = None) -> dict:
    """CAGR, drawdown and Sharpe of a daily equity (or price) series."""
    equity = equity.dropna()
    first = capital if capital is not None else equity.iloc[0]
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    peak = np.maximum.accumulate(np.maximum(equity.to_numpy(), first))
    daily = equity.pct_change().dropna()
    yearly = equity.groupby(equity.index.year).last().pct_change()
    yearly.iloc[0] = equity.groupby(equity.index.year).last().iloc[0] / first - 1
    return {"cagr": round(100 * ((equity.iloc[-1] / first) ** (1 / years) - 1), 1), "final": round(equity.iloc[-1]),
            "max_dd": round(100 * float(((peak - equity.to_numpy()) / peak).max()), 1),
            "sharpe": round(float(daily.mean() / daily.std() * 252 ** 0.5), 2) if daily.std() else 0.0,
            "worst_year": round(100 * float(yearly.min()), 1), "best_year": round(100 * float(yearly.max()), 1),
            "losing_years": int((yearly < 0).sum()), "years": round(years, 1),
            "yearly": {int(y): round(100 * float(v), 1) for y, v in yearly.items()}}


def trade_stats(result: dict) -> dict:
    t = pd.DataFrame(result["trades"])
    if t.empty:
        return {"trades": 0}
    wins, losses = t[t["net"] > 0], t[t["net"] <= 0]
    equity = result["equity"]
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    invested = np.mean([len(p["hold"]) > 0 for p in result["picks"]]) if result["picks"] else 0.0
    return {"trades": len(t), "win_rate": round(100 * len(wins) / len(t), 1),
            "avg_win_pct": round(wins["return_pct"].mean(), 1) if len(wins) else 0.0,
            "avg_loss_pct": round(losses["return_pct"].mean(), 1) if len(losses) else 0.0,
            "profit_factor": round(wins["net"].sum() / -losses["net"].sum(), 2) if len(losses) and losses["net"].sum() else None,
            "avg_hold_days": round(t["days"].mean()), "trades_per_year": round(len(t) / years, 1),
            "fees": round(result["fees"]), "fees_pct_of_capital_per_year": round(100 * result["fees"] / result["capital"] / years, 1),
            "turnover_x_per_year": round(result["traded_value"] / 2 / equity.mean() / years, 1),
            "months_invested_pct": round(100 * invested, 1)}


def row(market: Market, params: Params, costs: Costs = Costs(), capital: float = CAPITAL, whole_shares: bool = True) -> dict:
    """CAGR / drawdown / Sharpe of one configuration in each window."""
    out = {}
    for name, lo, hi in WINDOWS:
        s = stats(run(market, params, costs, capital, lo, hi, whole_shares)["equity"], capital)
        out.update({f"{name}_cagr": s["cagr"], f"{name}_dd": s["max_dd"], f"{name}_sharpe": s["sharpe"]})
    return out


# ---------------------------------------------------------------- studies
def study_baseline(market: Market, params: Params = Params()) -> dict:
    out = {"windows": [], "yearly": {}}
    for name, lo, hi in WINDOWS:
        result = run(market, params, start=lo, end=hi)
        bench = market.bench["NIFTY"].loc[result["equity"].index[0]:result["equity"].index[-1]]
        s, b = stats(result["equity"], CAPITAL), stats(bench)
        out["windows"].append({"window": name, **{k: v for k, v in s.items() if k != "yearly"}, **trade_stats(result),
                               "nifty_cagr": b["cagr"], "nifty_dd": b["max_dd"], "nifty_sharpe": b["sharpe"]})
        if name == "full":
            out["yearly"] = {y: {"strategy": v, "nifty": b["yearly"].get(y)} for y, v in s["yearly"].items()}
            monthly = result["equity"].groupby([result["equity"].index.year, result["equity"].index.month]).last().pct_change().dropna()
            out["months_up_pct"] = round(100 * float((monthly > 0).mean()), 1)
            out["worst_month_pct"] = round(100 * float(monthly.min()), 1)
    return out


VARIANTS: Dict[str, dict] = {
    "baseline: 6 stocks, index score, exit below 200-day": {},
    "4 stocks": {"top_n": 4}, "8 stocks": {"top_n": 8}, "10 stocks": {"top_n": 10},
    "score: 12-month skipping the last": {"score": "12-1"}, "score: 6-month": {"score": "6m"},
    "regime: ignore": {"regime": "none"}, "regime: no new buys": {"regime": "no_new"},
    "regime: 100-day average": {"regime_ma": 100},
    "no holding buffer": {"hold_buffer": 1.0}, "holding buffer 3x": {"hold_buffer": 3.0},
    "universe: top 100": {"universe_size": 100}, "universe: top 300": {"universe_size": 300},
    "rebalance every 3 months": {"rebalance_months": 3},
}


def study_variants(market: Market) -> List[dict]:
    return [{"variant": name, **row(market, replace(Params(), **change))} for name, change in VARIANTS.items()]


VOLUME_RULES: Dict[str, dict] = {
    "no volume rule (A1 as tested)": {},
    "skip stocks trading above 2x their norm (V2 as specified)": {"volume_rule": "skip_surge", "volume_level": 2.0},
    "skip above 1.5x": {"volume_rule": "skip_surge", "volume_level": 1.5},
    "skip above 3x": {"volume_rule": "skip_surge", "volume_level": 3.0},
    "only stocks at or below their normal volume": {"volume_rule": "quiet", "volume_level": 1.0},
    "only stocks at 1.2x their norm or more (the opposite)": {"volume_rule": "rising", "volume_level": 1.2},
    "only stocks at 2x their norm or more": {"volume_rule": "rising", "volume_level": 2.0},
}


def study_volume(market: Market) -> List[dict]:
    """V2: the rotation with each volume rule applied to the ranked list before the six are chosen."""
    return [{"volume_rule": name, **row(market, replace(Params(), **change))} for name, change in VOLUME_RULES.items()]


def study_volume_events(market: Market, params: Params = Params(), top: int = 30) -> List[dict]:
    """The top ``top`` momentum stocks each month-end, split into thirds by volume ratio: return over the next
    month (close to close), minus the average of all ``top``. No account, no costs."""
    ends = [d for d in month_ends(market.close.index) if d >= pd.Timestamp("2010-12-01")]
    records = []
    for at, nxt in zip(ends, ends[1:]):
        ranked = scores(market.close, at, universe(market.turnover, market.close, at, params), params).head(top)
        if len(ranked) < top:
            continue
        ratio = volume_ratio(market.turnover, at, ranked.index)
        forward = market.close.loc[nxt, ranked.index] / market.close.loc[at, ranked.index] - 1.0
        frame = pd.DataFrame({"ratio": ratio, "excess": forward - forward.mean()}).dropna().sort_values("ratio")
        third = len(frame) // 3
        for label, part in (("lowest third (quiet)", frame.iloc[:third]), ("middle third", frame.iloc[third:len(frame) - third]),
                            ("highest third (heavy)", frame.iloc[len(frame) - third:])):
            records.append({"date": at, "group": label, "excess": float(part["excess"].mean()), "ratio": float(part["ratio"].median())})
    events = pd.DataFrame(records)
    rows = []
    for window, lo, hi in WINDOWS:
        sub = events[(events["date"] >= lo) & (events["date"] <= hi)]
        for label, x in sub.groupby("group", sort=False):
            e = x["excess"]
            rows.append({"window": window, "group": label, "months": len(e), "median_volume_ratio": round(float(x["ratio"].median()), 2),
                         "avg_excess_pct_a_month": round(100 * float(e.mean()), 2), "months_ahead_pct": round(100 * float((e > 0).mean()), 1),
                         "t_stat": round(float(e.mean() / (e.std(ddof=1) / len(e) ** 0.5)), 2)})
    return rows


def study_account(market: Market, params: Params = Params()) -> List[dict]:
    """What account size and the broker's delivery fee do to the same rules."""
    rows = [{"account": f"Rs {int(c):,}, Rs 20 an order", **row(market, params, capital=c)} for c in (50_000.0, 65_000.0, 80_000.0, 500_000.0)]
    rows.append({"account": "Rs 65,000, no brokerage", **row(market, params, replace(Costs(), brokerage_per_order=0.0))})
    rows.append({"account": "Rs 65,000, 0.5% brokerage", **row(market, params, replace(Costs(), brokerage_pct=0.005))})
    rows.append({"account": "Rs 65,000, slippage 0.3% a side", **row(market, params, replace(Costs(), slippage=0.003))})
    rows.append({"account": "no costs, fractional shares", **row(market, params, Costs(0, 0, 0, 0, 0, 0, 0, 0), 1e7, whole_shares=False)})
    return rows


def study_bias(market: Market) -> dict:
    """Survivorship check. NSE's Nifty200 Momentum 30 is rebuilt from this data (30 stocks, half-yearly, no regime
    filter, no costs) and set against what the fund houses publish for the real index: 22% a year for the 15
    years to mid-2026. The same universe held equal-weight is set against the Nifty 500."""
    free = Costs(0, 0, 0, 0, 0, 0, 0, 0)
    replica = replace(Params(), top_n=30, regime="none", rebalance_months=6, hold_buffer=1.0)
    out = {}
    eq = run(market, replica, free, 1e8, "2011-07-01", "2026-06-30", whole_shares=False)["equity"]
    out["replica_15y"] = {"cagr": stats(eq)["cagr"], "max_dd": stats(eq)["max_dd"], "published_index_tri_cagr": 22.0}
    etf = market.bench["MOM30ETF"].dropna()
    eq = run(market, replica, free, 1e8, f"{etf.index[0]:%Y-%m-%d}", f"{etf.index[-1]:%Y-%m-%d}", whole_shares=False)["equity"]
    out["replica_vs_etf"] = {"from": f"{etf.index[0]:%Y-%m-%d}", "replica_cagr": stats(eq)["cagr"], "etf_cagr": stats(etf)["cagr"]}
    # every stock of the point-in-time top-200, equal weight, monthly
    days = market.close.loc["2011-01-01":].index
    ret, weights = market.close.pct_change(), None
    series = []
    ends = set(month_ends(market.close.index))
    for day in days:
        series.append(float((ret.loc[day, weights].fillna(0)).mean()) if weights else 0.0)
        if day in ends:
            weights = universe(market.turnover, market.close, day, Params())
    ew = (1 + pd.Series(series, index=days)).cumprod()
    out["universe_equal_weight"] = {"cagr": stats(ew)["cagr"], "nifty500_cagr": stats(market.bench["NIFTY500"].loc["2011-01-01":])["cagr"],
                                    "nifty50_cagr": stats(market.bench["NIFTY"].loc["2011-01-01":])["cagr"]}
    out["members_with_prices_by_year"] = {int(y): int(n) for y, n in
                                          market.close.groupby(market.close.index.year).apply(lambda g: int(g.notna().any().sum())).items()}
    return out


def study_picks(market: Market, data_root: Path, params: Params = Params()) -> dict:
    """What the rules hold: sectors, liquidity rank, share price, how long, and the list as of the last month-end."""
    result = run(market, params)
    industry = dataset.members(data_root).set_index("Symbol")["Industry"]
    held = pd.Series([s for p in result["picks"] for s in p["hold"]])
    months = len([p for p in result["picks"] if p["hold"]])
    trades = pd.DataFrame(result["trades"])
    ranks, prices = [], []
    for p in result["picks"]:
        if p["hold"]:
            day = pd.Timestamp(p["date"])
            order = universe(market.turnover, market.close, day, params)
            ranks += [order.index(s) + 1 for s in p["hold"] if s in order]
            prices += [float(market.close.at[day, s]) for s in p["hold"]]
    last = result["picks"][-1]
    day = pd.Timestamp(last["date"])
    ranked = scores(market.close, day, universe(market.turnover, market.close, day, params), params)
    best = trades.nlargest(8, "return_pct")[["symbol", "buy", "sell", "return_pct"]].round(1).to_dict("records")
    worst = trades.nsmallest(8, "return_pct")[["symbol", "buy", "sell", "return_pct"]].round(1).to_dict("records")
    return {
        "sector_share_pct": (100 * held.map(industry).value_counts(normalize=True)).round(1).head(12).to_dict(),
        "most_held": held.value_counts().head(15).to_dict(), "months_with_holdings": months, "distinct_stocks": int(held.nunique()),
        "liquidity_rank_median": float(np.median(ranks)), "liquidity_rank_share_top50_pct": round(100 * float(np.mean(np.array(ranks) <= 50)), 1),
        "share_price_median": round(float(np.median(prices))), "hold_days_median": float(trades["days"].median()),
        "hold_days_quartiles": [float(trades["days"].quantile(q)) for q in (0.25, 0.75)],
        "best_trades": best, "worst_trades": worst,
        "as_of": last["date"], "risk_on": last["risk_on"], "would_hold": last["hold"],
        "top_ranked_now": [{"symbol": s, "industry": industry.get(s), "score": round(float(v), 2), "close": round(float(market.close.at[day, s]), 1)}
                           for s, v in ranked.head(12).items()],
        "time_in_cash_pct": round(100 * float(np.mean([not p["hold"] for p in result["picks"]])), 1)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--study", choices=("baseline", "variants", "account", "bias", "picks", "volume", "volume_events", "all"), default="baseline")
    ap.add_argument("--data-root", type=Path, default=dataset.DEFAULT_DATA_ROOT)
    ap.add_argument("--refresh", action="store_true", help="download prices again first")
    ap.add_argument("--out", type=Path, help="write the results as JSON (default: <data-root>/backtests/)")
    args = ap.parse_args()
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    market = Market.load(args.data_root, args.refresh)
    print(f"{market.close.shape[1]} stocks, {market.close.index[0]:%Y-%m-%d} -> {market.close.index[-1]:%Y-%m-%d}\n")
    results: dict = {"generated": datetime.now().isoformat(timespec="seconds"), "capital": CAPITAL, "params": asdict(Params()), "costs": asdict(Costs())}
    for study in (("baseline", "variants", "account", "bias", "picks") if args.study == "all" else (args.study,)):
        print(f"== {study} ==")
        if study == "baseline":
            res = study_baseline(market)
            print(pd.DataFrame(res["windows"]).T.to_string(header=False))
            print(pd.DataFrame(res["yearly"]).to_string())
        elif study == "variants":
            res = study_variants(market)
            print(pd.DataFrame(res).to_string(index=False))
        elif study == "account":
            res = study_account(market)
            print(pd.DataFrame(res).to_string(index=False))
        elif study in ("volume", "volume_events"):
            res = study_volume(market) if study == "volume" else study_volume_events(market)
            print(pd.DataFrame(res).to_string(index=False))
        elif study == "bias":
            res = study_bias(market)
            print(json.dumps(res, indent=1))
        else:
            res = study_picks(market, args.data_root)
            print(json.dumps(res, indent=1, default=str))
        results[study] = res
        print()
    out = args.out or Path(args.data_root) / "backtests" / f"momentum_rotation_{args.study}_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1, default=str))
    print(f"results written to {out}")


if __name__ == "__main__":
    main()
