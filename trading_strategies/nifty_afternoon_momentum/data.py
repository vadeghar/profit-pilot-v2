"""Research dataset for the NIFTY intraday studies: 5-minute index candles, daily closes and India VIX.

Built from what is already on disk plus Yahoo Finance, and cached under ``<data root>/research/nifty_intraday/``:

    nifty_5m.csv    t,o,h,l,c,src     09:15-15:25 candle starts, complete sessions only
    nsei_1d.csv     d,open,high,low,close
    vix_1d.csv      d,vix_open,vix_close

5-minute sources, first one wins on overlap: ``historical/NSE_NIFTY_5m.json`` (Breeze), the spot candles in
``breeze_nifty_ratio_2025/`` (Breeze, only the days that study downloaded), Yahoo ``^NSEI`` (last 60 days).

    python -m trading_strategies.nifty_afternoon_momentum.data [--data-root data] [--refresh]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
SUBDIR = Path("research") / "nifty_intraday"
MIN_BARS_PER_DAY = 73


def _breeze_json(root: Path) -> pd.DataFrame:
    path = root / "historical" / "NSE_NIFTY_5m.json"
    if not path.exists():
        return pd.DataFrame()
    df = pd.DataFrame(json.loads(path.read_text())["candles"], columns=["t", "o", "h", "l", "c", "v"]).drop(columns="v")
    df["t"] = pd.to_datetime(df["t"])
    return df.assign(src="breeze_json")


def _breeze_ratio_spot(root: Path) -> pd.DataFrame:
    base = root / "breeze_nifty_ratio_2025"
    manifest = base / "manifest.json"
    if not manifest.exists():
        return pd.DataFrame()
    frames = []
    for req in json.loads(manifest.read_text())["requests"].values():
        if "spot" not in req.get("label", "") or not req.get("rows") or not req.get("path"):
            continue
        path = base / req["path"].replace("\\", "/")
        if path.exists():
            frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame()
    raw = pd.concat(frames, ignore_index=True)
    raw = raw[raw["exchange_code"] == "NSE"]
    return pd.DataFrame({"t": pd.to_datetime(raw["datetime"]), "o": raw["open"], "h": raw["high"], "l": raw["low"],
                         "c": raw["close"], "src": "breeze_ratio"})


def _yahoo(symbol: str, **kwargs) -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(symbol, progress=False, auto_adjust=False, **kwargs)
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.reset_index()
    return df.rename(columns={df.columns[0]: "t"})


def build(data_root: Path = DEFAULT_DATA_ROOT) -> Path:
    out = Path(data_root) / SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    y = _yahoo("^NSEI", period="60d", interval="5m")
    yahoo_5m = pd.DataFrame({"t": pd.to_datetime(y["t"]).dt.tz_localize(None), "o": y["Open"], "h": y["High"],
                             "l": y["Low"], "c": y["Close"], "src": "yahoo"})
    candles = pd.concat([_breeze_json(Path(data_root)), _breeze_ratio_spot(Path(data_root)), yahoo_5m], ignore_index=True)
    candles = candles.dropna().drop_duplicates("t", keep="first").sort_values("t")
    clock = candles["t"].dt.strftime("%H:%M")
    candles = candles[(clock >= "09:15") & (clock <= "15:25")]
    per_day = candles.groupby(candles["t"].dt.date)["t"].transform("size")
    candles[per_day >= MIN_BARS_PER_DAY].to_csv(out / "nifty_5m.csv", index=False)

    d = _yahoo("^NSEI", start="2024-12-01", interval="1d")[["t", "Open", "High", "Low", "Close"]].dropna()
    d.columns = ["d", "open", "high", "low", "close"]
    d.to_csv(out / "nsei_1d.csv", index=False)
    v = _yahoo("^INDIAVIX", start="2024-12-01", interval="1d")[["t", "Open", "Close"]].dropna()
    v.columns = ["d", "vix_open", "vix_close"]
    v.to_csv(out / "vix_1d.csv", index=False)
    return out


def load(data_root: Path = DEFAULT_DATA_ROOT, refresh: bool = False):
    """(5-minute candles, daily close Series by date, VIX close Series by date); builds the cache if missing."""
    out = Path(data_root) / SUBDIR
    if refresh or not all((out / f).exists() for f in ("nifty_5m.csv", "nsei_1d.csv", "vix_1d.csv")):
        build(data_root)
    candles = pd.read_csv(out / "nifty_5m.csv", parse_dates=["t"])
    daily = pd.read_csv(out / "nsei_1d.csv", parse_dates=["d"])
    vix = pd.read_csv(out / "vix_1d.csv", parse_dates=["d"])
    daily["d"], vix["d"] = daily["d"].dt.date, vix["d"].dt.date
    return candles, daily.set_index("d")["close"], vix.set_index("d")["vix_close"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    c, dc, vx = load(args.data_root, refresh=args.refresh)
    days = c["t"].dt.date
    print(f"5m candles: {len(c)} over {days.nunique()} sessions, {days.min()} -> {days.max()}")
    print(c.groupby("src")["t"].apply(lambda s: s.dt.date.nunique()).to_string())
    print(f"daily closes: {len(dc)}   VIX closes: {len(vx)} (median {vx.median():.2f})")
