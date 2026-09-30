"""Market data for the strategy audit (real exchange data, cached on disk).

* Daily/weekly NSE bars: yfinance (split-adjusted OHLC, unadjusted for
  dividends) - no broker login, no API quota.
* Intraday NSE bars (15m/1h/4h): ICICI Breeze 5-minute / 30-minute history,
  resampled with the platform's own session-anchored resampler and split-
  adjusted with yfinance's split history (broker history is unadjusted, so a
  1:1 bonus would otherwise look like a 50% crash).
* MCX: neither Breeze (no MCX segment) nor Angel (only live contracts) serves
  multi-year continuous MCX futures, so MCX Trend Rider runs on a proxy:
  COMEX/NYMEX continuous futures x USD/INR x Indian import duty.
"""
from __future__ import annotations

import json
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from core.models import Candle
from market_data.normalize import candles_from_rows, resample_normalized_candles
from platform_config import get_all_instruments
from tools.strategy_audit.common import AUDIT_DATA_DIR, ENV_FILE
from utils.timezone import IST, breeze_utc_window_for_ist_day_chunk, ensure_ist

RAW_DIR = AUDIT_DATA_DIR / "raw"
EXCLUDED = {"NSE:TATAMOTORS": "Oct-2025 demerger (TMPV/TMCV) breaks price continuity"}
SESSION_START, SESSION_END = time(9, 15), time(15, 30)


def universe() -> list[dict]:
    return [i for i in get_all_instruments() if i["symbol"] not in EXCLUDED]


def instrument(symbol: str) -> dict:
    return next(i for i in get_all_instruments() if i["symbol"] == symbol)


def _to_candles(df: pd.DataFrame, symbol: str, timeframe: str, provider: str) -> list[Candle]:
    out = []
    for ts, r in df.iterrows():
        out.append(Candle(timestamp=ts.to_pydatetime(), open=float(r["open"]), high=float(r["high"]),
                          low=float(r["low"]), close=float(r["close"]), volume=float(r.get("volume", 0) or 0),
                          instrument=symbol, timeframe=timeframe, provider=provider))
    return out


# ---------------------------------------------------------------- yfinance ---
def _yf_daily(ticker: str, start: date, end: date) -> pd.DataFrame:
    path = RAW_DIR / "yf" / f"{ticker.replace('^', '_').replace('=', '_')}_{start}_{end}.csv"
    if path.exists():
        return pd.read_csv(path, index_col=0, parse_dates=True)
    import yfinance as yf
    h = yf.Ticker(ticker).history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                                  interval="1d", auto_adjust=False)
    if h.empty:
        raise RuntimeError(f"yfinance returned no data for {ticker}")
    df = h.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df.index = pd.DatetimeIndex([d.date() for d in df.index])
    df = df[~df.index.duplicated(keep="last")].dropna(subset=["open", "high", "low", "close"])
    df = df[df["close"] > 0]
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    return df


def daily(symbol: str, start: date, end: date) -> list[Candle]:
    df = _yf_daily(instrument(symbol)["sym_yfinance"], start, end)
    df.index = pd.DatetimeIndex([datetime.combine(d.date(), time(15, 30), IST) for d in df.index])
    return _to_candles(df, symbol, "1d", "yfinance")


def weekly(symbol_or_candles, start: date = None, end: date = None) -> list[Candle]:
    bars = daily(symbol_or_candles, start, end) if isinstance(symbol_or_candles, str) else symbol_or_candles
    df = pd.DataFrame([{"ts": c.timestamp, "open": c.open, "high": c.high, "low": c.low, "close": c.close,
                        "volume": c.volume} for c in bars]).set_index("ts")
    df["week"] = [ts.to_period("W-FRI") for ts in pd.DatetimeIndex(df.index).tz_convert(None)]
    rows = []
    for _, g in df.groupby("week", sort=True):
        rows.append({"ts": g.index[-1], "open": g["open"].iloc[0], "high": g["high"].max(),
                     "low": g["low"].min(), "close": g["close"].iloc[-1], "volume": g["volume"].sum()})
    out = pd.DataFrame(rows).set_index("ts")
    return _to_candles(out, bars[0].instrument, "1w", bars[0].provider)


def splits(symbol: str) -> list[tuple[date, float]]:
    path = RAW_DIR / "yf" / f"splits_{symbol.replace(':', '_')}.json"
    if path.exists():
        return [(date.fromisoformat(d), r) for d, r in json.loads(path.read_text())]
    import yfinance as yf
    s = yf.Ticker(instrument(symbol)["sym_yfinance"]).splits
    out = [(ts.date(), float(r)) for ts, r in s.items() if float(r) not in (0.0, 1.0)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([(d.isoformat(), r) for d, r in out]))
    return out


# --------------------------------------------------------------- MCX proxy ---
TROY_OZ_G = 31.1034768
MCX_PROXY = {
    # name: (yfinance ticker, INR price-unit multiplier on USD/oz or USD/bbl, duty applies)
    "MCX_GOLDM": ("GC=F", 10 / TROY_OZ_G, True),     # Rs per 10 g
    "MCX_SILVERM": ("SI=F", 1000 / TROY_OZ_G, True),  # Rs per kg
    "MCX_CRUDEOIL": ("CL=F", 1.0, False),            # Rs per barrel
}
DUTY_CUT = date(2024, 7, 23)  # Union Budget: gold/silver import duty 15% -> 6%


def mcx_proxy(name: str, start: date, end: date) -> list[Candle]:
    ticker, unit, dutiable = MCX_PROXY[name]
    fut = _yf_daily(ticker, start, end)
    fx = _yf_daily("USDINR=X", start, end)["close"].reindex(fut.index).ffill().bfill()
    duty = pd.Series([(1.15 if d.date() < DUTY_CUT else 1.06) if dutiable else 1.0 for d in fut.index],
                     index=fut.index)
    mult = fx * unit * duty
    df = fut.copy()
    for col in ("open", "high", "low", "close"):
        df[col] = fut[col] * mult
    df.index = pd.DatetimeIndex([datetime.combine(d.date(), time(23, 30), IST) for d in df.index])
    return _to_candles(df, name, "1d", "proxy:yfinance")


# ------------------------------------------------------------------ Breeze ---
_BREEZE = None
BREEZE_CHUNK = {"5minute": 15, "30minute": 60}


def _breeze():
    global _BREEZE
    if _BREEZE is None:
        from market_data.breeze_data_provider import BreezeHistoricalDataProvider
        p = BreezeHistoricalDataProvider(env_path=str(ENV_FILE), persist_cache=False)
        p.verify_once = True
        p.ensure_authenticated()
        _BREEZE = p
    return _BREEZE


def _breeze_code(symbol: str) -> tuple[str, str, str]:
    from lorentzian_strategy.data_loader import parse_breeze_ticker, resolve_breeze_stock_code
    code = instrument(symbol)["sym_breeze"]
    stock, exch, product = parse_breeze_ticker(code)
    if instrument(symbol)["type"] == "index":
        product = "cash"
    return resolve_breeze_stock_code(stock, exch), exch, product


def _breeze_chunk(symbol: str, interval: str, lo: date, hi: date) -> list:
    stock, exch, product = _breeze_code(symbol)
    path = RAW_DIR / "breeze" / f"{stock}_{interval}_{lo}_{hi}.json"
    if path.exists():
        return json.loads(path.read_text())
    from market_data.rate_limiter import get_limiter
    frm, to = breeze_utc_window_for_ist_day_chunk(datetime.combine(lo, time(0, 0), IST),
                                                  datetime.combine(hi, time(23, 59), IST))
    last_err = None
    for attempt in range(4):
        try:
            with get_limiter("breeze"):
                res = _breeze().client.get_historical_data_v2(
                    interval=interval, from_date=frm, to_date=to, stock_code=stock,
                    exchange_code=exch, product_type=product)
            if res and res.get("Status") == 200:
                rows = res.get("Success") or []
                rows = [[r.get("datetime"), r.get("open"), r.get("high"), r.get("low"), r.get("close"),
                         r.get("volume")] for r in rows]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(rows))
                return rows
            last_err = res
        except Exception as e:  # network / throttling: retry with backoff
            last_err = e
        _time.sleep(2 + attempt * 3)
    raise RuntimeError(f"Breeze {stock} {interval} {lo}..{hi} failed: {last_err}")


# ------------------------------------------------------------------- Angel ---
_ANGEL = None
ANGEL_INTERVAL = {"15m": ("FIFTEEN_MINUTE", 200), "1h": ("ONE_HOUR", 400)}


def _angel():
    global _ANGEL
    if _ANGEL is None:
        from market_data.angel_data_provider import AngelHistoricalDataProvider
        p = AngelHistoricalDataProvider()
        p.ensure_authenticated()
        _ANGEL = p
    return _ANGEL


def _angel_chunk(symbol: str, interval: str, lo: date, hi: date) -> list:
    inst = instrument(symbol)
    path = RAW_DIR / "angel" / f"{inst['sym_angel']}_{interval}_{lo}_{hi}.json"
    if path.exists():
        return json.loads(path.read_text())
    last_err = None
    for attempt in range(6):
        _time.sleep(1.0 + attempt * 5)  # SmartAPI rejects bursts ("exceeding access rate")
        try:
            res = _angel().broker.client.getCandleData({
                "exchange": inst["exchange"], "symboltoken": str(inst["sym_angel"]), "interval": interval,
                "fromdate": f"{lo:%Y-%m-%d} 09:00", "todate": f"{hi:%Y-%m-%d} 15:30"})
            if isinstance(res, dict) and res.get("status"):
                rows = res.get("data") or []
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(rows))
                return rows
            last_err = res
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Angel {symbol} {interval} {lo}..{hi} failed: {last_err}")


def intraday(symbol: str, timeframe: str, start: date, end: date, source: str = "angel") -> list[Candle]:
    """15m / 1h (native) and 4h (from 1h) bars from Angel One, or 15m (from 5m) /
    1h / 4h (from 30m) from Breeze; session-filtered and split-adjusted."""
    if source == "angel":
        interval, span = ANGEL_INTERVAL["15m" if timeframe == "15m" else "1h"]
        src_tf = "15m" if timeframe == "15m" else "1h"
        chunk = lambda lo, hi: _angel_chunk(symbol, interval, lo, hi)
        source_symbol = str(instrument(symbol)["sym_angel"])
    else:
        interval = "5minute" if timeframe == "15m" else "30minute"
        span = BREEZE_CHUNK[interval]
        src_tf = "5m" if interval == "5minute" else "30m"
        chunk = lambda lo, hi: _breeze_chunk(symbol, interval, lo, hi)
        source_symbol = _breeze_code(symbol)[0]
    rows, cur = [], start
    while cur <= end:
        hi = min(cur + timedelta(days=span - 1), end)
        rows += chunk(cur, hi)
        cur = hi + timedelta(days=1)
    norm = candles_from_rows(rows, instrument=symbol, timeframe=src_tf, provider=source,
                             source_symbol=source_symbol, exchange=instrument(symbol)["exchange"])
    norm = sorted({c.timestamp: c for c in norm
                   if SESSION_START <= ensure_ist(c.timestamp).time() < SESSION_END}.values(),
                  key=lambda c: c.timestamp)
    if timeframe != src_tf:
        norm = resample_normalized_candles(norm, timeframe)
    bars = [Candle(timestamp=ensure_ist(c.timestamp), open=c.open, high=c.high, low=c.low, close=c.close,
                   volume=c.volume or 0.0, instrument=symbol, timeframe=timeframe, provider=source)
            for c in norm]
    return split_adjust(symbol, bars)


def split_adjust(symbol: str, bars: list[Candle]) -> list[Candle]:
    events = [(d, r) for d, r in splits(symbol) if bars and bars[0].timestamp.date() < d <= bars[-1].timestamp.date()]
    for d, r in events:
        for c in bars:
            if c.timestamp.date() < d:
                c.open, c.high, c.low, c.close = c.open / r, c.high / r, c.low / r, c.close / r
                c.volume = c.volume * r
    return bars


def coverage(bars: list[Candle]) -> dict:
    if not bars:
        return {"bars": 0}
    days = {c.timestamp.date() for c in bars}
    return {"bars": len(bars), "first": bars[0].timestamp.isoformat(), "last": bars[-1].timestamp.isoformat(),
            "trading_days": len(days)}
