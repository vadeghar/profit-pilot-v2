"""ICICI Breeze client helpers shared by the Breeze data provider, the ICICI broker and option symbols.

Credentials (.env), an authenticated ``breeze_connect`` client, ticker parsing, the scrip-master
ShortName lookup Breeze's historical API needs, and the timeframe -> native interval map.
"""
import os
import sys

import pandas as pd

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
