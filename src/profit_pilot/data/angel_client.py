"""Angel One SmartAPI historical candle provider — generic, multi-exchange.

Refactor of the original single-symbol/single-exchange provider. Adds:
  - one shared, cached login session across all symbols/calls (was: one
    session per instantiated object)
  - a token cache that includes NSE, NFO and MCX (was: NSE equity only,
    which silently broke any commodity/futures lookup)
  - a generic `get_candles(symbol, exchange, interval, start, end, ...)`
    function that any strategy can call directly, with no class to construct
  - expiry-aware token resolution for futures/commodity contracts, which have
    one row per expiry in the instrument master

`AngelMarketDataProvider` is kept below as a thin backward-compatible wrapper
so existing call sites (`.states()`, `.candles()`) don't need to change.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Iterable
from datetime import date, datetime, time
from pathlib import Path
from typing import Optional

from .models import MarketState

ANGEL_INSTRUMENT_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
DEFAULT_TOKEN_FILE = Path("data/angel_tokens.json")

# Exchange segments to keep in the local cache. Add "BFO"/"CDS" if you ever
# need BSE F&O or currency derivatives.
CACHED_EXCHANGES = {"NSE", "NFO", "MCX"}


class AngelConfigurationError(RuntimeError):
    pass


# ----------------------------------------------------------------------------
# Shared session — logged in once per process, reused by every symbol/call.
# ----------------------------------------------------------------------------
_CLIENT_SINGLETON = None


def _get_client(env: dict[str, str] | None = None):
    global _CLIENT_SINGLETON
    if _CLIENT_SINGLETON is not None:
        return _CLIENT_SINGLETON

    env = env or os.environ
    api_key = env.get("ANGEL_API_KEY")
    client_code = env.get("ANGEL_CLIENT_CODE")
    password = env.get("ANGEL_PASSWORD_OR_MPIN")
    totp_secret = env.get("ANGEL_TOTP_SECRET")
    if not api_key or not client_code or not password or not totp_secret:
        raise AngelConfigurationError(
            "ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PASSWORD_OR_MPIN, "
            "and ANGEL_TOTP_SECRET are required"
        )
    try:
        import pyotp
        totp = pyotp.TOTP(totp_secret).now()
    except ImportError as exc:
        raise AngelConfigurationError("Install pyotp with: python -m pip install pyotp") from exc
    try:
        from SmartApi import SmartConnect
    except ImportError as exc:
        raise AngelConfigurationError("Install SmartAPI with: python -m pip install smartapi-python") from exc

    client = SmartConnect(api_key=api_key)
    session = client.generateSession(client_code, password, totp)
    if not isinstance(session, dict) or not session.get("status", False):
        raise AngelConfigurationError(f"Angel session failed: {session}")
    _CLIENT_SINGLETON = client
    return client


# ----------------------------------------------------------------------------
# Token master — downloaded once, cached in-memory + on disk.
# ----------------------------------------------------------------------------
_TOKEN_MASTER_CACHE: Optional[list[dict]] = None


def _download_token_master(token_file: Path) -> None:
    """Download and cache the Angel instrument master, keeping NSE equity,
    NFO (stock/index futures & options) and MCX (commodity futures) rows.
    The original version cached NSE-EQ only, which is why MCX/futures lookups
    would fail with 'No Angel token mapping'."""
    try:
        with urllib.request.urlopen(ANGEL_INSTRUMENT_MASTER_URL, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise AngelConfigurationError(f"Unable to download Angel instrument master: {exc}") from exc

    records = payload.get("data", payload) if isinstance(payload, dict) else payload
    filtered = [
        row for row in records
        if isinstance(row, dict)
        and str(row.get("exch_seg", row.get("exchange", ""))).upper() in CACHED_EXCHANGES
    ]
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(json.dumps(filtered, indent=2), encoding="utf-8")


def _load_token_master(token_file: Path = DEFAULT_TOKEN_FILE, force_refresh: bool = False) -> list[dict]:
    global _TOKEN_MASTER_CACHE
    if _TOKEN_MASTER_CACHE is not None and not force_refresh:
        return _TOKEN_MASTER_CACHE
    if force_refresh or not token_file.exists():
        _download_token_master(token_file)
    payload = json.loads(token_file.read_text(encoding="utf-8"))
    records = payload.get("data", payload) if isinstance(payload, dict) else payload
    _TOKEN_MASTER_CACHE = records if isinstance(records, list) else [records]
    return _TOKEN_MASTER_CACHE


def resolve_token(
    symbol: str,
    exchange: str,
    instrument_type: Optional[str] = None,   # e.g. "EQ", "FUTCOM" (MCX), "FUTSTK"/"FUTIDX" (NFO)
    expiry_on_or_after: Optional[date] = None,  # for futures: picks nearest unexpired contract
    token_file: Path = DEFAULT_TOKEN_FILE,
) -> dict:
    """Generic symbol -> Angel token resolver. Same lookup path for NSE
    equities, NFO futures/options and MCX commodity futures — filters the
    cached instrument master on (symbol, exchange, instrument_type, expiry)
    instead of assuming NSE equity like the original `_token_record`."""
    symbol = symbol.upper()
    exchange = exchange.upper()
    records = _load_token_master(token_file)

    candidates = []
    for row in records:
        if str(row.get("exch_seg", row.get("exchange", ""))).upper() != exchange:
            continue
        names = {str(row.get(k, "")).upper() for k in ("symbol", "tradingsymbol", "name")}
        names |= {n.split("-")[0] for n in names}
        if symbol not in names:
            continue
        if instrument_type and str(row.get("instrumenttype", "")).upper() != instrument_type.upper():
            continue
        candidates.append(row)

    if not candidates:
        raise AngelConfigurationError(f"No Angel token mapping for {symbol} on {exchange}")

    # Equities: one unambiguous row, no expiry field.
    if not any(row.get("expiry") for row in candidates):
        return candidates[0]

    # Futures/commodities: multiple expiries share the symbol — pick the
    # nearest contract that expires on/after the requested date.
    cutoff = expiry_on_or_after or date.today()
    dated = []
    for row in candidates:
        try:
            exp = datetime.strptime(row.get("expiry", ""), "%d%b%Y").date()
        except (TypeError, ValueError):
            continue
        if exp >= cutoff:
            dated.append((exp, row))
    if not dated:
        raise AngelConfigurationError(f"No unexpired contract for {symbol} on {exchange} on/after {cutoff}")
    dated.sort(key=lambda pair: pair[0])
    return dated[0][1]


def _fmt(value: date, end: bool = False) -> str:
    return datetime.combine(value, time(15, 30) if end else time(9, 15)).strftime("%Y-%m-%d %H:%M")


# ----------------------------------------------------------------------------
# Generic entry point — the method you asked for.
# ----------------------------------------------------------------------------

def get_candles(
    symbol: str,
    exchange: str,
    interval: str = "ONE_DAY",
    start: date = None,
    end: date = None,
    instrument_type: Optional[str] = None,
    token_file: Path = DEFAULT_TOKEN_FILE,
    env: dict[str, str] | None = None,
) -> list[dict]:
    """symbol + exchange + interval + date range -> OHLCV candles, for any
    Angel-supported instrument. Resolves the token, reuses the shared login
    session, and calls getCandleData — strategy code never touches tokens.

    Equity:   get_candles("RELIANCE", "NSE", "FIVE_MINUTE", d1, d2)
    MCX:      get_candles("CRUDEOIL", "MCX", "ONE_DAY", d1, d2, instrument_type="FUTCOM")
    NFO fut:  get_candles("BANKNIFTY", "NFO", "ONE_DAY", d1, d2, instrument_type="FUTIDX")
    """
    row = resolve_token(symbol, exchange, instrument_type=instrument_type,
                         expiry_on_or_after=start, token_file=token_file)
    token = row.get("token") or row.get("symboltoken") or row.get("symbolToken")
    if not token:
        raise AngelConfigurationError(f"Token missing for {symbol}")
    tradingsymbol = row.get("tradingsymbol") or row.get("symbol") or symbol

    client = _get_client(env)
    response = client.getCandleData({
        "exchange": exchange.upper(),
        "symboltoken": str(token),
        "interval": interval.upper(),
        "fromdate": _fmt(start),
        "todate": _fmt(end, end=True),
    })
    if not isinstance(response, dict) or not response.get("status", False):
        raise RuntimeError(f"Angel candle request failed: {response}")

    return [
        {"datetime": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5],
         "symbol": symbol, "tradingsymbol": tradingsymbol, "exchange": exchange.upper()}
        for r in (response.get("data") or [])
    ]


# ----------------------------------------------------------------------------
# Backward-compatible class wrapper (original call sites keep working).
# ----------------------------------------------------------------------------

class AngelMarketDataProvider:
    """Thin wrapper over `get_candles` for existing code using
    AngelMarketDataProvider(...).candles()/.states(). New code should call
    `get_candles()` directly — it supports MCX/NFO, this wrapper defaults
    to NSE equity for compatibility with prior usage."""

    def __init__(
        self,
        symbol: str,
        interval: str = "ONE_DAY",
        exchange: str = "NSE",
        instrument_type: Optional[str] = None,
        token_file: str | Path = "data/angel_tokens.json",
        env: dict[str, str] | None = None,
    ):
        self.project_symbol = symbol.upper()
        self.interval = interval.upper()
        self.exchange = exchange.upper()
        self.instrument_type = instrument_type
        self.token_file = Path(token_file)
        self.env = env

    def candles(self, start: date, end: date) -> list[dict]:
        return get_candles(
            self.project_symbol, self.exchange, self.interval, start, end,
            instrument_type=self.instrument_type, token_file=self.token_file, env=self.env,
        )

    def states(self, start: date, end: date) -> Iterable[MarketState]:
        for row in self.candles(start, end):
            stamp = datetime.fromisoformat(str(row["datetime"]).replace("Z", "+00:00"))
            yield MarketState(
                timestamp=stamp,
                symbol=self.project_symbol,
                price=float(row["close"]),
                fields={"open": float(row["open"]), "high": float(row["high"]),
                        "low": float(row["low"]), "volume": float(row.get("volume") or 0)},
            )
