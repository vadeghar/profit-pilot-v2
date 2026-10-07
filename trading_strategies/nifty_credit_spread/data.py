"""Data for the NIFTY credit spread backtest: index candles, India VIX, and recorded weekly-option candles.

Cached under ``<data root>/research/nifty_credit_spread/``:

    NIFTY_5m_2020_2023.json   5-minute index candles from Breeze for 2020-2023 (2024 on comes from the
                              self_aware_trend download)
    nifty_1d.csv, vix_1d.csv  daily closes from Yahoo (for the trend average before 2020, and for strikes)
    options/<expiry>_<strike>_<right>.json   5-minute candles of one weekly contract from Breeze

Option files are fetched on demand when ``Quotes`` is given the path of a .env with a live Breeze session;
without one the cache is read-only and a missing contract counts as "no data". One request covers a whole
weekly cycle. Breeze allows about 5,000 requests a day.

    python -m trading_strategies.nifty_credit_spread.data --env-file .env     # index candles for 2020-2023
"""
from __future__ import annotations

import argparse
import json
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from trading_strategies.nifty_afternoon_momentum.data import _yahoo
from trading_strategies.self_aware_trend import data as sats

DEFAULT_DATA_ROOT = sats.DEFAULT_DATA_ROOT
SUBDIR = Path("research") / "nifty_credit_spread"
EARLY_START, EARLY_END = date(2019, 12, 2), date(2023, 12, 31)


def _client(env_path: str):
    from market_data.breeze_client import connect_breeze
    return connect_breeze(env_path=env_path)


def download_index(data_root: Path = DEFAULT_DATA_ROOT, env_path: Optional[str] = None) -> Path:
    """NIFTY 5-minute candles for Dec-2019 to Dec-2023 from Breeze, a fortnight a request."""
    client, rows, cursor = _client(env_path), [], EARLY_START
    while cursor <= EARLY_END:
        end = min(cursor + timedelta(days=14), EARLY_END)
        res = client.get_historical_data_v2(interval="5minute", from_date=f"{cursor}T09:00:00.000Z", to_date=f"{end}T16:00:00.000Z",
                                            stock_code="NIFTY", exchange_code="NSE", product_type="cash")
        rows += [[r["datetime"], r["open"], r["high"], r["low"], r["close"], 0] for r in (res or {}).get("Success") or []]
        cursor = end + timedelta(days=1)
        _time.sleep(0.4)
    path = Path(data_root) / SUBDIR / "NIFTY_5m_2020_2023.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"source": "breeze", "fetched_at": datetime.now().isoformat(), "candles": rows}))
    return path


def load_index(data_root: Path = DEFAULT_DATA_ROOT) -> pd.DataFrame:
    """Regular-session NIFTY 5-minute candles t, o, h, l, c from Dec-2019, auction bars removed."""
    early = pd.DataFrame(json.loads((Path(data_root) / SUBDIR / "NIFTY_5m_2020_2023.json").read_text())["candles"],
                         columns=["t", "o", "h", "l", "c", "v"])
    early["t"] = pd.to_datetime(early["t"].astype(str).str.slice(0, 19))
    late = sats.load(data_root)
    candles = pd.concat([sats._sessions(early), late[late["t"] > early["t"].max()]], ignore_index=True)
    return candles[["t", "o", "h", "l", "c"]]


def load_daily(data_root: Path = DEFAULT_DATA_ROOT, refresh: bool = False) -> tuple[pd.Series, pd.Series]:
    """(Nifty 50 daily close, India VIX daily close), each a Series by date, from Yahoo."""
    out = Path(data_root) / SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    series = []
    for name, symbol in (("nifty_1d.csv", "^NSEI"), ("vix_1d.csv", "^INDIAVIX")):
        path = out / name
        if refresh or not path.exists():
            y = _yahoo(symbol, start="2018-06-01", interval="1d")[["t", "Close"]].dropna()
            y.columns = ["d", "close"]
            y.to_csv(path, index=False)
        frame = pd.read_csv(path, parse_dates=["d"])
        series.append(pd.Series(frame["close"].to_numpy(), index=frame["d"].dt.date))
    return series[0], series[1]


class QuotaExceeded(RuntimeError):
    """Breeze refused a request (rate or daily limit); everything fetched so far is cached."""


class Quotes:
    """Recorded 5-minute closes of weekly NIFTY options, one cached file per contract."""

    def __init__(self, data_root: Path = DEFAULT_DATA_ROOT, env_path: Optional[str] = None):
        self.dir = Path(data_root) / SUBDIR / "options"
        self.env_path, self.client, self.requests = env_path, None, 0

    def closes(self, first_day: date, expiry: date, strike: int, right: str) -> pd.Series:
        """Close by candle start time from ``first_day`` to ``expiry``; empty when Breeze has nothing."""
        path = self.dir / f"{expiry:%Y%m%d}_{strike}_{right}.json"
        if not path.exists():
            if self.env_path is None:
                return pd.Series(dtype=float)
            self._fetch(path, first_day, expiry, strike, right)
        rows = json.loads(path.read_text())["candles"]
        if not rows:
            return pd.Series(dtype=float)
        s = pd.Series([r[1] for r in rows], index=pd.to_datetime([r[0] for r in rows]), dtype=float)
        return s[~s.index.duplicated()].sort_index()

    def _fetch(self, path: Path, first_day: date, expiry: date, strike: int, right: str) -> None:
        if self.client is None:
            self.client = _client(self.env_path)
        res = self.client.get_historical_data_v2(
            interval="5minute", from_date=f"{first_day}T09:00:00.000Z", to_date=f"{expiry}T16:00:00.000Z", stock_code="NIFTY",
            exchange_code="NFO", product_type="options", expiry_date=f"{expiry}T06:00:00.000Z",
            right="call" if right == "CE" else "put", strike_price=str(strike))
        self.requests += 1
        _time.sleep(0.35)
        if not res or res.get("Status") != 200:
            raise QuotaExceeded(f"Breeze refused {expiry} {strike} {right}: {(res or {}).get('Error')}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"from": first_day.isoformat(), "candles": [[r["datetime"], r["close"]] for r in res.get("Success") or []]}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    ap.add_argument("--env-file", help="the .env holding the Breeze session; downloads the 2020-2023 index candles")
    args = ap.parse_args()
    if args.env_file:
        print(download_index(args.data_root, args.env_file))
    c = load_index(args.data_root)
    days = c["t"].dt.date
    print(f"NIFTY: {len(c)} candles over {days.nunique()} sessions, {days.min()} -> {days.max()}")
    print("sessions per year:", days.drop_duplicates().map(lambda d: d.year).value_counts().sort_index().to_dict())
