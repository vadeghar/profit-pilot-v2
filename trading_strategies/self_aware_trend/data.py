"""Candles for the Self-Aware Trend backtest: 5-minute history from Breeze, resampled to other timeframes.

``load`` returns the 5-minute candles of NIFTY or a stock. It reads the Breeze download under
``<data root>/research/self_aware_trend/breeze/`` when there is one (continuous from Jan-2024) and otherwise,
for NIFTY only, the older research cache built by ``nifty_afternoon_momentum.data`` (continuous from Jan-2026,
about 12 sessions a month before). NIFTY spot has no volume of its own; ``futures_volume`` attaches the
near-month future's.

Since 3-Aug-2026 the cash market stops continuous trading at 15:15 and what Breeze serves after that is the
closing auction: one or two bars several times the size of a normal one. Those bars are dropped, so sessions
from that date end with the 15:10 candle. Special sessions (Muhurat, Saturday drills) are dropped too.

Longer bars are built inside each session, anchored at 09:15 like a TradingView chart, so the last bar of a
session is short. Nothing finer than 5 minutes is stored.

    python -m trading_strategies.self_aware_trend.data --download HDFCBANK ICICIBANK RELIANCE NIFTY   # needs a live Breeze session
"""
from __future__ import annotations

import argparse
import json
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from trading_strategies.nifty_afternoon_momentum import data as dataset

DEFAULT_DATA_ROOT = dataset.DEFAULT_DATA_ROOT
SUBDIR = Path("research") / "self_aware_trend"
BASE_MINUTES = 5
SESSION_OPEN_MINUTE = 9 * 60 + 15
MIN_BARS_PER_SESSION = 70
CLOSING_AUCTION_FROM = date(2026, 8, 3)        # bars from 15:15 are auction prints from this session on
HISTORY_START = datetime(2024, 1, 1)
YAHOO = {5: ("5m", "60d", 72), 60: ("1h", "730d", 6)}      # base minutes -> (interval, period, fewest bars in a usable session)


def _sessions(candles: pd.DataFrame) -> pd.DataFrame:
    """Regular-session 5-minute candles on the grid, auction bars and short sessions removed."""
    candles = candles.dropna(subset=["o", "h", "l", "c"]).drop_duplicates("t").sort_values("t")
    clock = candles["t"].dt.strftime("%H:%M")
    auction = (candles["t"].dt.date >= CLOSING_AUCTION_FROM) & (clock >= "15:15")
    candles = candles[(clock >= "09:15") & (clock <= "15:25") & (candles["t"].dt.minute % BASE_MINUTES == 0) & ~auction]
    per_day = candles.groupby(candles["t"].dt.date)["t"].transform("size")
    return candles[per_day >= MIN_BARS_PER_SESSION].reset_index(drop=True)


def load(data_root: Path = DEFAULT_DATA_ROOT, symbol: str = "NIFTY", futures_volume: bool = False,
         refresh: bool = False) -> pd.DataFrame:
    """5-minute candles t, o, h, l, c, v for ``symbol``; ``v`` is zero for NIFTY unless ``futures_volume``."""
    path = Path(data_root) / SUBDIR / "breeze" / f"{symbol}_5m.json"
    if path.exists():
        candles = pd.DataFrame(json.loads(path.read_text())["candles"], columns=["t", "o", "h", "l", "c", "v"])
        candles["t"] = pd.to_datetime(candles["t"].astype(str).str.slice(0, 19))
        candles["v"] = pd.to_numeric(candles["v"], errors="coerce").fillna(0).clip(lower=0)
    elif symbol == "NIFTY":
        candles = dataset.load(data_root, refresh=refresh)[0][["t", "o", "h", "l", "c"]].assign(v=0.0)
    else:
        raise FileNotFoundError(f"no candles for {symbol}: run this module with --download {symbol}")
    candles = _sessions(candles)
    if futures_volume:
        future = pd.read_csv(Path(data_root) / SUBDIR / "breeze" / f"{symbol}_FUT_5m.csv", parse_dates=["t"])
        candles["v"] = candles["t"].map(future.drop_duplicates("t").set_index("t")["v"]).fillna(0).clip(lower=0).to_numpy()
    return candles


def load_vix(data_root: Path = DEFAULT_DATA_ROOT, refresh: bool = False) -> pd.Series:
    """India VIX daily close by date, from Yahoo, cached."""
    path = Path(data_root) / SUBDIR / "india_vix_1d.csv"
    if refresh or not path.exists():
        y = dataset._yahoo("^INDIAVIX", start="2023-12-01", interval="1d")[["t", "Close"]].dropna()
        y.columns = ["d", "vix_close"]
        path.parent.mkdir(parents=True, exist_ok=True)
        y.to_csv(path, index=False)
    vix = pd.read_csv(path, parse_dates=["d"])
    return pd.Series(vix["vix_close"].to_numpy(), index=vix["d"].dt.date)


def load_yahoo(symbol: str, base_minutes: int, data_root: Path = DEFAULT_DATA_ROOT, refresh: bool = False) -> pd.DataFrame:
    """Candles t, o, h, l, c, v for a Yahoo ``symbol`` (``HDFCBANK.NS``, ``^NSEI``), complete past sessions only.

    Yahoo serves 5-minute candles for the last 60 days and hourly candles for about three years."""
    interval, period, min_bars = YAHOO[base_minutes]
    path = Path(data_root) / SUBDIR / f"{symbol.strip('^').replace('.', '_')}_{interval}.csv"
    if refresh or not path.exists():
        y = dataset._yahoo(symbol, period=period, interval=interval)
        candles = pd.DataFrame({"t": pd.to_datetime(y["t"]).dt.tz_localize(None), "o": y["Open"], "h": y["High"],
                                "l": y["Low"], "c": y["Close"], "v": y["Volume"]}).dropna().sort_values("t")
        candles = candles[candles["t"].dt.date < pd.Timestamp.now().date()]
        per_day = candles.groupby(candles["t"].dt.date)["t"].transform("size")
        path.parent.mkdir(parents=True, exist_ok=True)
        candles[per_day >= min_bars].to_csv(path, index=False)
    return pd.read_csv(path, parse_dates=["t"])


def resample(candles: pd.DataFrame, minutes: int, base_minutes: int = BASE_MINUTES) -> Tuple[pd.DataFrame, np.ndarray]:
    """(bars of ``minutes`` built from the ``base_minutes`` ``candles``, index of the candle each one closes on)."""
    if minutes == base_minutes:
        return candles.copy(), np.arange(len(candles))
    t = candles["t"]
    bucket = (t.dt.hour * 60 + t.dt.minute - SESSION_OPEN_MINUTE) // minutes
    grouped = candles.assign(_i=np.arange(len(candles))).groupby([t.dt.date, bucket], sort=True)
    columns = dict(t=("t", "first"), o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), _i=("_i", "last"))
    if "v" in candles:
        columns["v"] = ("v", "sum")
    bars = grouped.agg(**columns)
    return bars.drop(columns="_i").reset_index(drop=True), bars["_i"].to_numpy()


# ---------------------------------------------------------------- Breeze download
def download(symbols: Sequence[str], data_root: Path = DEFAULT_DATA_ROOT, env_path: Optional[str] = None,
             start: datetime = HISTORY_START) -> None:
    """Fetch 5-minute history for ``symbols`` from Breeze into the cache ``load`` reads (existing files are kept)."""
    from market_data.breeze_data_provider import BreezeHistoricalDataProvider
    out = Path(data_root) / SUBDIR / "breeze"
    provider = BreezeHistoricalDataProvider(cache_dir=str(out), env_path=env_path)
    provider.verify_once = True
    for symbol in symbols:
        candles = provider.get_historical_candles(symbol, "5m", start, datetime.now())
        print(f"{symbol}: {len(candles)} candles, {candles[0].timestamp:%Y-%m-%d} -> {candles[-1].timestamp:%Y-%m-%d}")


def download_nifty_futures(data_root: Path = DEFAULT_DATA_ROOT, env_path: Optional[str] = None,
                           start: date = HISTORY_START.date()) -> Path:
    """Near-month NIFTY futures 5-minute candles stitched into one series (t, c, v, expiry), for the volume column."""
    from market_data.breeze_client import connect_breeze
    from market_data.expiries import monthly_expiry_from_calendar
    client = connect_breeze(env_path=env_path)
    stamp = lambda d, clock: f"{d:%Y-%m-%d}T{clock}:00.000Z"

    def fetch(expiry: date, lo: date, hi: date) -> list:
        rows, cursor = [], lo
        while cursor <= hi:
            end = min(cursor + timedelta(days=14), hi)
            res = client.get_historical_data_v2(
                interval="5minute", from_date=stamp(cursor, "09:00"), to_date=stamp(end, "16:00"), stock_code="NIFTY",
                exchange_code="NFO", product_type="futures", expiry_date=stamp(expiry, "07:00"), right="others", strike_price="0")
            rows += (res or {}).get("Success") or []
            cursor = end + timedelta(days=1)
            _time.sleep(0.4)
        return rows

    frames, previous, today = [], start - timedelta(days=4), date.today()
    month = start.replace(day=1)
    while month <= today:
        nominal = monthly_expiry_from_calendar(month.year, month.month)
        for expiry in (nominal, nominal - timedelta(days=1), nominal - timedelta(days=2)):   # holiday-shifted expiries
            rows = fetch(expiry, previous + timedelta(days=1), min(expiry, today))
            if rows:
                frames.append(pd.DataFrame(rows)[["datetime", "close", "volume"]].assign(expiry=expiry.isoformat()))
                previous = expiry
                break
        month = (month + timedelta(days=32)).replace(day=1)
    out = pd.concat(frames, ignore_index=True)
    out["datetime"] = pd.to_datetime(out["datetime"].str.slice(0, 19))
    out = out.drop_duplicates("datetime").sort_values("datetime").rename(columns={"datetime": "t", "close": "c", "volume": "v"})
    path = Path(data_root) / SUBDIR / "breeze" / "NIFTY_FUT_5m.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--download", nargs="*", metavar="SYMBOL", help="fetch 5-minute history from Breeze for these symbols")
    ap.add_argument("--futures", action="store_true", help="also fetch NIFTY near-month futures (for volume)")
    ap.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    ap.add_argument("--env-file", help="the .env holding the Breeze session")
    args = ap.parse_args()
    if args.download:
        download(args.download, args.data_root, args.env_file)
    if args.futures:
        print(download_nifty_futures(args.data_root, args.env_file))
    for name in args.download or ["NIFTY"]:
        c = load(args.data_root, name)
        days = c["t"].dt.date
        print(f"{name}: {len(c)} candles over {days.nunique()} sessions, {days.min()} -> {days.max()}")
