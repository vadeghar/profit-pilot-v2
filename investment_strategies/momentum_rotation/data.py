"""Daily prices for the momentum rotation backtest: today's Nifty 500 members and a few benchmarks, from Yahoo.

Cached under ``<data root>/research/momentum_rotation/``:

    constituents.csv   Company Name, Industry, Symbol, Series, ISIN Code   (NSE's Nifty 500 list on the day of download)
    prices.parquet     d, symbol, o, h, l, c, v      adjusted for splits and dividends, so returns are total returns
    benchmarks.parquet d, symbol, c                  ^NSEI, NIFTYBEES.NS (Nifty 50 with dividends), a momentum ETF

The member list is *today's*. A stock that was dropped from the index or delisted is not in it, which
flatters any backtest built on it (survivorship bias); backtest.py measures how much.

    python -m investment_strategies.momentum_rotation.data [--data-root data] [--refresh]
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path
from typing import Tuple

import pandas as pd

DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
SUBDIR = Path("research") / "momentum_rotation"
CONSTITUENTS_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
START = "2009-01-01"
BENCHMARKS = {"NIFTY": "^NSEI", "NIFTYBEES": "NIFTYBEES.NS", "MOM30ETF": "MOM30IETF.NS", "NIFTY500": "^CRSLDX"}
BATCH = 50


def _constituents(out: Path, refresh: bool) -> pd.DataFrame:
    path = out / "constituents.csv"
    if refresh or not path.exists():
        import requests
        res = requests.get(CONSTITUENTS_URL, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
            "Referer": "https://www.nseindia.com/"})
        res.raise_for_status()
        members = pd.read_csv(io.StringIO(res.text))
        if len(members) < 400:
            raise RuntimeError(f"NSE returned {len(members)} rows for the Nifty 500 list")
        members.to_csv(path, index=False)
    return pd.read_csv(path)


def _download(tickers: list, start: str) -> pd.DataFrame:
    """Long frame d, ticker, o, h, l, c, v of adjusted daily candles."""
    import yfinance as yf
    frames = []
    for i in range(0, len(tickers), BATCH):
        raw = yf.download(tickers[i:i + BATCH], start=start, interval="1d", auto_adjust=True, progress=False,
                          group_by="ticker", threads=True)
        for ticker in tickers[i:i + BATCH]:
            if ticker not in raw.columns.get_level_values(0):
                continue
            one = raw[ticker].dropna(subset=["Close"])
            if len(one):
                frames.append(pd.DataFrame({"d": one.index.tz_localize(None), "ticker": ticker, "o": one["Open"].to_numpy(),
                                            "h": one["High"].to_numpy(), "l": one["Low"].to_numpy(),
                                            "c": one["Close"].to_numpy(), "v": one["Volume"].to_numpy()}))
    return pd.concat(frames, ignore_index=True)


def build(data_root: Path = DEFAULT_DATA_ROOT, refresh_members: bool = False) -> Path:
    out = Path(data_root) / SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    members = _constituents(out, refresh_members)
    prices = _download([f"{s}.NS" for s in members["Symbol"]], START)
    prices["symbol"] = prices.pop("ticker").str.replace(".NS", "", regex=False)
    prices[["d", "symbol", "o", "h", "l", "c", "v"]].to_parquet(out / "prices.parquet", index=False)
    bench = _download(list(BENCHMARKS.values()), START)
    bench["symbol"] = bench.pop("ticker").map({v: k for k, v in BENCHMARKS.items()})
    bench[["d", "symbol", "c"]].to_parquet(out / "benchmarks.parquet", index=False)
    return out


def load(data_root: Path = DEFAULT_DATA_ROOT, refresh: bool = False) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(close, open, turnover in rupees) as date x symbol frames, plus benchmark closes and the member list
    is available through ``members``. Builds the cache when it is missing."""
    out = Path(data_root) / SUBDIR
    if refresh or not (out / "prices.parquet").exists():
        build(data_root, refresh_members=refresh)
    prices = pd.read_parquet(out / "prices.parquet")
    close = prices.pivot(index="d", columns="symbol", values="c").sort_index()
    open_ = prices.pivot(index="d", columns="symbol", values="o").sort_index()
    turnover = (prices.assign(t=prices["c"] * prices["v"]).pivot(index="d", columns="symbol", values="t").sort_index())
    bench = pd.read_parquet(out / "benchmarks.parquet").pivot(index="d", columns="symbol", values="c").sort_index()
    return close, open_, turnover, bench


def members(data_root: Path = DEFAULT_DATA_ROOT) -> pd.DataFrame:
    return pd.read_csv(Path(data_root) / SUBDIR / "constituents.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    close, _, turnover, bench = load(args.data_root, refresh=args.refresh)
    print(f"{close.shape[1]} stocks, {len(close)} days, {close.index[0]:%Y-%m-%d} -> {close.index[-1]:%Y-%m-%d}")
    print("stocks with a price, by year:", close.groupby(close.index.year).apply(lambda g: int(g.notna().any().sum())).to_dict())
    print("benchmarks:", {k: f"{bench[k].first_valid_index():%Y-%m-%d}" for k in bench})
