"""OHLC data loading, timeframe interval mapping and resampling (§1, §1.1).

Data providers: Breeze (ICICI Direct, default), yfinance, ccxt, CSV.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .config import PANDAS_FREQ_MAP, TIMEFRAMES

OHLC_COLUMNS = ["open", "high", "low", "close"]

# yfinance intraday interval lookback limits (approximate, per provider docs)
YF_INTRADAY_LIMIT_DAYS = {"1m": 7, "5m": 59, "15m": 59, "30m": 59, "1h": 729}
YF_VALID_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    missing = [c for c in OHLC_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"OHLC dataframe missing columns: {missing}. Got {list(df.columns)}")
    df = df[OHLC_COLUMNS].apply(pd.to_numeric, errors="coerce")
    return df.dropna().sort_index()


def resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample OHLC data to the requested timeframe (§1.1)."""
    if timeframe not in PANDAS_FREQ_MAP:
        raise ValueError(f"Unsupported timeframe {timeframe!r}; choose from {TIMEFRAMES}")
    freq = PANDAS_FREQ_MAP[timeframe]
    return df.resample(freq).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()


def load_csv(path: str, timeframe: str) -> pd.DataFrame:
    from utils.timezone import IST, ensure_ist
    df = pd.read_csv(path)
    for col in ("datetime", "date", "timestamp", "time", "dt"):
        if col in [c.lower() for c in df.columns]:
            actual = [c for c in df.columns if c.lower() == col][0]
            if pd.api.types.is_numeric_dtype(df[actual]):
                unit = "ms" if df[actual].iloc[0] > 1e12 else "s"
                ts = pd.to_datetime(df[actual], unit=unit, utc=True)
            else:
                ts = pd.to_datetime(df[actual], utc=True, errors="coerce")
            # Normalize everything to IST wall-clock: naive stamps are IST,
            # aware stamps are converted. Index stays tz-aware (IST).
            ts = pd.DatetimeIndex(ts)
            if ts.tz is None:
                ts = ts.tz_localize(IST)
            else:
                ts = ts.tz_convert(IST)
            df.index = ts
            df = df.drop(columns=[actual])
            break
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("CSV must include a parseable datetime column to serve as index")
    df = _normalize_columns(df)
    return resample_ohlcv(df, timeframe) if timeframe != "1m" else df


def fetch_yfinance(ticker: str, timeframe: str, max_bars_back: int, period: str = None) -> pd.DataFrame:
    import yfinance as yf
    from utils.timezone import IST
    interval = "1h" if timeframe == "4h" else timeframe  # yfinance has no 4h; resample below
    if interval not in YF_VALID_INTERVALS:
        raise ValueError(f"yfinance does not support interval {interval!r}")
    if period is None:
        if interval in YF_INTRADAY_LIMIT_DAYS:
            period = f"{YF_INTRADAY_LIMIT_DAYS[interval]}d"
        elif timeframe == "1d":
            period = "10y"
        else:
            period = "max"
    df = yf.download(ticker, interval=interval, period=period,
                     auto_adjust=False, progress=False, multi_level_index=False)
    if df is None or df.empty:
        raise ValueError(f"yfinance returned no data for {ticker} @ {interval}")
    # yfinance index is usually UTC (or naive exchange time). Normalize to IST.
    if isinstance(df.index, pd.DatetimeIndex):
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC").tz_convert(IST)
        else:
            df.index = df.index.tz_convert(IST)
    df = _normalize_columns(df)
    if timeframe == "4h":
        df = resample_ohlcv(df, "4h")
    return df

BREEZE_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY"}
# (breeze_interval, resample_to or None) per strategy timeframe.
# Breeze natively supports 1minute/5minute/30minute/1day; anything else is resampled.
BREEZE_INTERVAL_MAP = {
    "1m": ("1minute", None),
    "5m": ("5minute", None),
    "10m": ("5minute", "10min"),
    "15m": ("5minute", "15min"),
    "30m": ("30minute", None),
    "1h": ("30minute", "1h"),
    "4h": ("30minute", "4h"),
    "1d": ("1day", None),
    "1mo": ("1day", "1M"),
}
# Approximate tradeable bars per IST trading day (NSE ~09:15-15:30) for lookback sizing
BREEZE_BARS_PER_DAY = {"1minute": 375, "5minute": 75, "30minute": 13, "1day": 1}
# Max calendar days per Breeze historical API request (conservative chunking)
BREEZE_CHUNK_DAYS = {"1minute": 5, "5minute": 15, "30minute": 60, "1day": 366}
BREEZE_MAX_LOOKBACK_DAYS = 2200


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_breeze_env(env_path: str = None) -> dict:
    """Load Breeze credentials from the platform .env (BREEZE_* keys)."""
    path = env_path or os.path.join(project_root(), ".env")
    env = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env


def connect_breeze(env: dict = None, env_path: str = None):
    """Create an authenticated breeze_connect client (lazy import)."""
    env = env if env is not None else load_breeze_env(env_path)
    api_key = env.get("BREEZE_API_KEY")
    api_secret = env.get("BREEZE_API_SECRET")
    session_token = env.get("BREEZE_SESSION_TOKEN")
    missing = [k for k, v in (("BREEZE_API_KEY", api_key), ("BREEZE_API_SECRET", api_secret),
                              ("BREEZE_SESSION_TOKEN", session_token)) if not v]
    if missing:
        raise ValueError(
            f"Missing Breeze credentials in .env: {', '.join(missing)}. "
            "Run tools/breeze/breeze_auto_login.py to obtain a fresh session token."
        )
    try:
        from utils.breeze_sdk import import_breeze_connect
        BreezeConnect = import_breeze_connect()
    except ImportError:
        # Platform `utils` package unavailable (standalone package use) —
        # import the SDK directly.
        try:
            from breeze_connect import BreezeConnect  # noqa: F811
        except Exception as e2:
            raise ValueError(f"breeze_connect package not importable: {e2}") from e2
    except Exception:
        # breeze_connect hits the network at import time (security-master
        # download). Python's default OpenSSL CA bundle may be stale/empty and
        # fail TLS verification against api.icicidirect.com's newer GlobalSign
        # roots — retry the import with certifi's bundle instead.
        try:
            import importlib
            import urllib.request
            import certifi
            from utils.breeze_sdk import import_breeze_connect
            os.environ["SSL_CERT_FILE"] = certifi.where()
            os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
            # rebuild urllib's globally cached opener (it was created with the
            # old, untrusted CA context during the failed first import)
            urllib.request._opener = None
            for mod in list(sys.modules):
                if mod.startswith("breeze_connect") or mod == "config":
                    del sys.modules[mod]
            BreezeConnect = import_breeze_connect()
        except Exception as e2:
            raise ValueError(f"breeze_connect package not importable: {e2}") from e2
    client = BreezeConnect(api_key=api_key)
    client.generate_session(api_secret=api_secret, session_token=session_token)
    return client

def parse_breeze_ticker(ticker: str):
    """'NSE:RELIANCE' / 'RELIANCE' / 'MCX:CRUDEOIL' -> (stock, exchange, product_type).

    Index symbols (NIFTY, BANKNIFTY, ...) map to product_type='index'; everything
    else defaults to exchange NSE / product_type 'cash'.
    """
    if ":" in ticker:
        exchange_code, stock_code = ticker.split(":", 1)
    else:
        exchange_code, stock_code = "NSE", ticker
    exchange_code = exchange_code.upper().replace("_EQ", "").replace("_FUT", "")
    stock_code = stock_code.upper()
    product_type = "index" if stock_code in BREEZE_INDEX_SYMBOLS else "cash"
    return stock_code, exchange_code, product_type


# --- ICICI scrip-code resolution --------------------------------------------------
# Breeze resolves the instrument against the scrip master's *ShortName*, not the
# NSE/BSE ticker held in `ExchangeCode`:
#     RELIANCE -> RELIND, SBIN -> STABAN, INFY -> INFTEC, HDFCBANK -> HDFBAN
# Passing the plain NSE ticker answers HTTP 200 with `Success: []` (zero rows) for
# every equity whose ShortName differs from its NSE symbol. Only the handful where
# the two coincide (TCS, ITC, WIPRO, MARUTI, NTPC, ONGC, NIFTY) worked by accident,
# which is why the failure looked sporadic instead of universal.
_BREEZE_SCRIP_FILES = {"nse": "NSEScripMaster.txt", "bse": "BSEScripMaster.txt"}
_BREEZE_SCRIP_SYMBOL_COL = {"nse": "ExchangeCode", "bse": "ScripID"}
# exchange -> {UPPER ticker: ICICI ShortName}; built once per process
_BREEZE_SHORTNAME_MAPS: dict = {}


def breeze_scrip_short_names(exchange_code: str = "NSE") -> dict:
    """{UPPER ticker: ICICI ShortName} read from the Breeze security master.

    Reuses the zip the breeze_connect SDK already downloads at import time
    (``breeze_connect.breeze_connect.zip``), so this costs no extra network call.
    Returns ``{}`` when the SDK is not loaded (mocked clients / offline unit tests)
    or the master cannot be read — callers then keep the symbol exactly as given,
    so scrip resolution can never break a fetch.
    """
    key = str(exchange_code or "NSE").lower()
    if key in _BREEZE_SHORTNAME_MAPS:
        return _BREEZE_SHORTNAME_MAPS[key]
    filename = _BREEZE_SCRIP_FILES.get(key)
    if filename is None:
        _BREEZE_SHORTNAME_MAPS[key] = {}   # exchange has no scrip master (e.g. MCX)
        return {}
    bcmod = sys.modules.get("breeze_connect.breeze_connect")
    zipf = getattr(bcmod, "zip", None) if bcmod is not None else None
    if zipf is None:
        # SDK not imported yet (its security master never downloaded) — return
        # without caching so the next call, after connect_breeze(), resolves it.
        return {}
    mapping: dict = {}
    try:
        if filename in zipf.namelist():
            import csv as _csv
            from io import StringIO
            text = zipf.open(filename).read().decode("utf-8", "replace")
            rows = _csv.reader(StringIO(text))
            header = next(rows, None)
            if header:
                # headers arrive quoted/padded: ' "ShortName"', ' "ExchangeCode"'
                col = {h.strip().strip('"').strip(): i for i, h in enumerate(header)}
                sym_i = col.get(_BREEZE_SCRIP_SYMBOL_COL[key])
                short_i = col.get("ShortName")
                series_i = col.get("Series")
                if sym_i is not None and short_i is not None:
                    by_series, by_any = {}, {}
                    for row in rows:
                        if len(row) <= max(sym_i, short_i):
                            continue
                        sym = row[sym_i].strip().strip('"').strip().upper()
                        short = row[short_i].strip().strip('"').strip()
                        if not sym or not short:
                            continue
                        by_any.setdefault(sym, short)
                        series = (row[series_i].strip().strip('"').strip().upper()
                                  if series_i is not None and len(row) > series_i else "")
                        if series in ("EQ", "0", ""):   # cash series (indices use "0")
                            by_series.setdefault(sym, short)
                    mapping = dict(by_any)
                    mapping.update(by_series)            # EQ series wins
                    # a symbol that is already a ShortName must still resolve
                    for short in list(mapping.values()):
                        mapping.setdefault(short.upper(), short)
    except Exception:
        mapping = {}
    _BREEZE_SHORTNAME_MAPS[key] = mapping
    return mapping


def resolve_breeze_stock_code(stock_code, exchange_code: str = "NSE"):
    """Translate a ticker to the scrip code Breeze's historical API expects.

    ``RELIANCE`` -> ``RELIND`` on NSE/BSE; unchanged when no master entry exists
    (indices such as BANKNIFTY, other exchanges such as MCX, or no SDK loaded).
    """
    if not stock_code:
        return stock_code
    names = breeze_scrip_short_names(exchange_code)
    if not names:
        return stock_code
    try:
        key = str(stock_code).strip().strip('"').strip().upper()
        return names.get(key) or stock_code
    except Exception:
        return stock_code


def breeze_lookback_days(timeframe: str, bars_needed: int) -> int:
    """Calendar days to walk back for `bars_needed` bars at the native interval."""
    interval = BREEZE_INTERVAL_MAP[timeframe][0]
    days = int(bars_needed / BREEZE_BARS_PER_DAY[interval] * 1.6) + 7  # holiday safety
    return min(days, BREEZE_MAX_LOOKBACK_DAYS)


def breeze_rows_to_dataframe(rows) -> pd.DataFrame:
    """Convert Breeze historical rows to OHLCV with tz-aware IST index.

    Breeze stamps IST market times with a literal 'Z' suffix
    (e.g. '2026-09-14 09:15:00.000Z' == 09:15 IST), so we parse naive and
    attach IST instead of treating it as UTC.
    """
    from utils.timezone import IST, parse_broker_timestamp
    if not rows:
        df0 = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df0.index = pd.DatetimeIndex([], tz=IST)
        return df0
    recs = [{"datetime": r.get("datetime"), "open": r.get("open"), "high": r.get("high"),
             "low": r.get("low"), "close": r.get("close"), "volume": r.get("volume")}
            for r in rows]
    df = pd.DataFrame(recs)
    ts = pd.DatetimeIndex([parse_broker_timestamp(v) for v in df["datetime"].astype(str)])
    # normalize anything odd back to IST
    if ts.tz is None:
        ts = ts.tz_localize(IST)
    else:
        ts = ts.tz_convert(IST)
    df.index = ts
    df = df[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
    df = df.dropna().sort_index()
    return df[~df.index.duplicated(keep="last")]


def fetch_breeze(ticker: str, timeframe: str, max_bars_back: int,
                 env_path: str = None, client=None) -> pd.DataFrame:
    """Fetch OHLCV from Breeze get_historical_data_v2 (§1.1).

    Paginates backwards in date chunks until maxBarsBack + 50 bars are collected,
    then resamples to the requested timeframe when Breeze has no native interval.
    """
    if timeframe not in BREEZE_INTERVAL_MAP:
        raise ValueError(f"timeframe {timeframe!r} not supported by the Breeze provider; "
                         f"choose from {sorted(BREEZE_INTERVAL_MAP)}")
    interval, resample_to = BREEZE_INTERVAL_MAP[timeframe]
    stock_code, exchange_code, product_type = parse_breeze_ticker(ticker)

    if client is None:
        client = connect_breeze(env_path=env_path)

    # The historical endpoint keys off the scrip master's ShortName, so translate
    # the ticker now that the SDK (and its security master) is loaded.
    stock_code = resolve_breeze_stock_code(stock_code, exchange_code)

    bars_needed = max_bars_back + 50
    from utils.timezone import IST, breeze_utc_window_for_ist_day_chunk, now_ist
    end_dt = now_ist()
    cursor = end_dt - timedelta(days=breeze_lookback_days(timeframe, bars_needed))
    chunk = timedelta(days=BREEZE_CHUNK_DAYS[interval])

    rows = []
    df = breeze_rows_to_dataframe(rows)
    while cursor < end_dt and (len(rows) < bars_needed or (resample_to and len(df) < bars_needed)):
        chunk_end = min(cursor + chunk, end_dt)
        from_str, to_str = breeze_utc_window_for_ist_day_chunk(cursor, chunk_end)
        try:
            res = client.get_historical_data_v2(
                interval=interval,
                from_date=from_str,
                to_date=to_str,
                stock_code=stock_code,
                exchange_code=exchange_code,
                product_type=product_type,
            )
        except Exception as e:
            raise ValueError(f"Breeze get_historical_data_v2 failed for {stock_code} "
                             f"@ {interval} [{cursor:%Y-%m-%d}..{chunk_end:%Y-%m-%d}]: {e}") from e
        if res and res.get("Status") == 200 and res.get("Success"):
            batch = res["Success"]
            if isinstance(batch, dict):  # some responses wrap rows in a sub-key
                batch = batch.get("candles", batch.get("data", []))
            rows.extend(batch)
        elif res and res.get("Status") != 200 and not rows:
            raise ValueError(f"Breeze returned error for {stock_code} ({exchange_code}, "
                             f"{product_type}) @ {interval}: {res.get('Error', 'unknown')}")
        cursor = chunk_end + timedelta(seconds=1)
        # re-evaluate sufficiency AFTER resampling (e.g. 400 x 30m bars -> 200 x 1h bars)
        df = breeze_rows_to_dataframe(rows)
        if resample_to:
            df = resample_ohlcv(df, resample_to)
        if cursor < end_dt:
            import time
            time.sleep(0.4)  # respect Breeze rate limits

    if df.empty:
        raise ValueError(
            f"Breeze returned no candles for {stock_code} on {exchange_code} "
            f"(product_type={product_type}, interval={interval}). Check the symbol/exchange."
        )
    if len(df) < bars_needed:
        raise ValueError(
            f"Insufficient history from Breeze at timeframe {timeframe!r} for {ticker}: "
            f"got {len(df)} bars, need {bars_needed} (maxBarsBack {max_bars_back} + 50). "
            f"Shortfall: {bars_needed - len(df)} bars. Reduce --max-bars-back or choose a "
            f"larger timeframe."
        )
    if resample_to:
        df = resample_ohlcv(df, resample_to)
    return df


def fetch_ccxt(exchange_id: str, symbol: str, timeframe: str, max_bars_back: int) -> pd.DataFrame:
    import ccxt
    exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    if timeframe not in exchange.timeframes:
        raise ValueError(f"Exchange {exchange_id} does not support timeframe {timeframe!r}; "
                         f"supported: {sorted(exchange.timeframes)}")
    since = exchange.milliseconds() - (max_bars_back + 50) * exchange.parse_timeframe(timeframe) * 1000
    rows = []
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        since = batch[-1][0] + 1
        if len(batch) < 1000:
            break
    if not rows:
        raise ValueError(f"No OHLCV returned for {symbol} on {exchange_id}")
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    from utils.timezone import IST
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert(IST)
    return _normalize_columns(df.set_index(df.index).drop(columns=["ts"]))


def load_data(settings) -> pd.DataFrame:
    """Load OHLC data at the requested timeframe; fail fast on short history (§1.1)."""
    provider = settings.data_provider
    min_bars = settings.max_bars_back + 50
    if provider == "csv":
        df = load_csv(settings.csv_path, settings.timeframe)
    elif provider == "breeze":
        df = fetch_breeze(settings.ticker, settings.timeframe, settings.max_bars_back)
    elif provider == "yfinance":
        df = fetch_yfinance(settings.ticker, settings.timeframe, settings.max_bars_back, settings.period)
    elif provider == "ccxt":
        exch, symbol = (settings.ticker.split(":", 1) if ":" in settings.ticker
                        else ("binance", settings.ticker))
        df = fetch_ccxt(exch, symbol, settings.timeframe, settings.max_bars_back)
    else:
        raise ValueError(f"Unknown data provider {provider!r}")

    if len(df) < min_bars:
        raise ValueError(
            f"Insufficient history at timeframe {settings.timeframe!r}: got {len(df)} bars, "
            f"need maxBarsBack({settings.max_bars_back}) + 50 = {min_bars} bars. "
            f"Shortfall: {min_bars - len(df)} bars. Reduce --max-bars-back, choose a larger "
            f"timeframe, or supply more history."
        )
    return df


def describe_data(df: pd.DataFrame, settings) -> str:
    return (f"Loaded {len(df)} bars | ticker={settings.ticker} timeframe={settings.timeframe} "
            f"range=[{df.index[0]} .. {df.index[-1]}]")


def source_series(df: pd.DataFrame, source: str) -> pd.Series:
    if source == "hlc3":
        return (df["high"] + df["low"] + df["close"]) / 3.0
    if source == "ohlc4":
        return (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
    return df[source].astype(float)


def check_no_gaps(df: pd.DataFrame) -> int:
    """Return number of index gaps (informational only; strategy tolerates them)."""
    diffs = np.diff(df.index.values).astype("timedelta64[s]").astype(np.int64)
    if len(diffs) == 0:
        return 0
    return int((diffs > 1.5 * np.median(diffs)).sum())
