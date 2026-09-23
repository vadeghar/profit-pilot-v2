"""Canonical market-data normalization layer.

Every data provider (yfinance / Breeze / Angel One) MUST convert its raw
responses into `NormalizedCandle` records via this module before returning
them, and the backtest engine consumes ONLY normalized candles — raw broker
payloads never reach the backtest loop (see `ensure_normalized_candles`).

All timestamps are tz-aware IST (Asia/Kolkata) datetimes.
"""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Union

import pandas as pd

from utils.timezone import IST, ensure_ist, parse_broker_timestamp

__all__ = [
    "CANONICAL_TIMEFRAMES",
    "TIMEFRAME_ALIASES",
    "normalize_timeframe",
    "timeframe_minutes",
    "normalize_timestamp",
    "NormalizedCandle",
    "candles_from_rows",
    "candles_from_dataframe",
    "candles_to_dataframe",
    "resample_normalized_candles",
    "validate_candles",
    "ensure_normalized_candles",
    "resample_candles",  # legacy DataFrame-based resampler (kept for compat)
]

# ---------------------------------------------------------------------------
# Canonical timeframes
# ---------------------------------------------------------------------------

# canonical name -> minutes per bar
CANONICAL_TIMEFRAMES: Dict[str, int] = {
    "1m": 1, "2m": 2, "3m": 3, "5m": 5, "10m": 10, "15m": 15,
    "30m": 30, "45m": 45, "1h": 60, "2h": 120, "3h": 180,
    "4h": 240, "90m": 90, "1d": 1440, "1w": 10080, "1mo": 43200,
}

# common alias -> canonical
TIMEFRAME_ALIASES: Dict[str, str] = {c: c for c in CANONICAL_TIMEFRAMES}
TIMEFRAME_ALIASES.update({
    "1min": "1m", "1minute": "1m",
    "2min": "2m", "2minute": "2m",
    "3min": "3m", "3minute": "3m",
    "5min": "5m", "5minute": "5m",
    "10min": "10m", "10minute": "10m",
    "15min": "15m", "15minute": "15m",
    "30min": "30m", "30minute": "30m",
    "45min": "45m", "45minute": "45m",
    "90min": "90m", "90minute": "90m",
    "60min": "1h", "60minute": "1h", "1hour": "1h", "1hr": "1h", "1H": "1h",
    "2hour": "2h", "2hr": "2h", "2H": "2h", "120min": "2h",
    "3hour": "3h", "3H": "3h", "180min": "3h",
    "4hour": "4h", "4hr": "4h", "4H": "4h", "240min": "4h",
    "1day": "1d", "day": "1d", "daily": "1d", "1D": "1d",
    "1week": "1w", "1wk": "1w", "week": "1w", "weekly": "1w", "1W": "1w",
    "1month": "1mo", "month": "1mo", "monthly": "1mo", "1M": "1mo",
})

_TF_RE = re.compile(
    r"^(\d+)\s*(m|min|mins|minute|minutes|h|hr|hour|hours|d|day|days|w|wk|week|weeks)$",
    re.IGNORECASE)
_UNIT_MAP = {"m": "m", "min": "m", "mins": "m", "minute": "m", "minutes": "m",
             "h": "h", "hr": "h", "hour": "h", "hours": "h",
             "d": "d", "day": "d", "days": "d",
             "w": "w", "wk": "w", "week": "w", "weeks": "w"}


def normalize_timeframe(timeframe: str) -> str:
    """Map any timeframe spelling to the canonical form ('15min' -> '15m',
    '60min' -> '1h', '1day' -> '1d', ...). Raises ValueError if unparseable."""
    if not timeframe:
        raise ValueError("Timeframe is required (e.g. '1m', '15min', '1h', '1d').")
    key = str(timeframe).strip()
    if key in TIMEFRAME_ALIASES:
        return TIMEFRAME_ALIASES[key]
    key_l = key.lower()
    if key_l in TIMEFRAME_ALIASES:
        return TIMEFRAME_ALIASES[key_l]
    match = _TF_RE.match(key_l)
    if match:
        n, unit = int(match.group(1)), _UNIT_MAP[match.group(2)]
        return f"{n}{unit}"
    raise ValueError(
        f"Unsupported timeframe {timeframe!r}. Supported canonical timeframes: "
        f"{sorted(CANONICAL_TIMEFRAMES)} (aliases like '15min'/'60min'/'1day' are accepted)."
    )


def timeframe_minutes(timeframe: str) -> int:
    """Minutes per bar for a canonical or alias timeframe."""
    return CANONICAL_TIMEFRAMES[normalize_timeframe(timeframe)]


# ---------------------------------------------------------------------------
# Timestamp normalization
# ---------------------------------------------------------------------------

def normalize_timestamp(value: Any) -> datetime:
    """Convert any timestamp representation to a tz-aware IST datetime.

    Handles: datetime (naive -> IST), pd.Timestamp, ISO/date strings,
    'YYYY-MM-DD HH:MM:SS' broker strings (incl. Breeze's IST-stamped 'Z'
    quirk via utils.timezone.parse_broker_timestamp) and epoch seconds/ms.
    """
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return ensure_ist(value)
    if value is None:
        raise ValueError("Candle timestamp is required.")
    if isinstance(value, (int, float)):
        ms = abs(float(value)) >= 1e12
        seconds = float(value) / 1000.0 if ms else float(value)
        return datetime.fromtimestamp(seconds, tz=IST)
    text = str(value).strip()
    # numeric string (epoch)
    try:
        num = float(text)
        ms = abs(num) >= 1e12
        return datetime.fromtimestamp(num / 1000.0 if ms else num, tz=IST)
    except (ValueError, OSError, OverflowError):
        pass
    try:
        return ensure_ist(parse_broker_timestamp(text))
    except Exception:
        pass
    # date-only / plain fallbacks
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return ensure_ist(datetime.strptime(text, fmt))
        except ValueError:
            continue
    raise ValueError(f"Unparseable candle timestamp: {value!r}")


# ---------------------------------------------------------------------------
# NormalizedCandle
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NormalizedCandle:
    """Canonical OHLCV record every provider must emit.

    Superset of core.models.Candle (same core attribute names) plus
    provenance/metadata attributes; safe to feed directly to strategies.
    """
    timestamp: datetime                      # tz-aware IST
    open: float
    high: float
    low: float
    close: float
    volume: float
    instrument: str
    timeframe: str                           # canonical, e.g. '1h'
    open_interest: Optional[float] = None
    vwap: Optional[float] = None
    trades_count: Optional[int] = None
    provider: Optional[str] = None           # yfinance / breeze / angel
    source_symbol: Optional[str] = None      # provider-native symbol used
    exchange: Optional[str] = None
    resampled: bool = False                  # True when aggregated from finer bars
    raw: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "timestamp", ensure_ist(self.timestamp))
        for name in ("open", "high", "low", "close"):
            v = float(getattr(self, name))
            if not math.isfinite(v):
                raise ValueError(f"NormalizedCandle.{name} must be finite, got {v}")
            if v <= 0:
                raise ValueError(f"NormalizedCandle.{name} must be > 0, got {v}")
            object.__setattr__(self, name, v)
        volume = float(self.volume or 0)
        if not math.isfinite(volume) or volume < 0:
            raise ValueError(f"NormalizedCandle.volume must be >= 0, got {volume}")
        object.__setattr__(self, "volume", volume)
        # OHLC envelope normalization: broker feeds occasionally violate the
        # strict high/low envelope by a tick (rounding), so repair instead of
        # rejecting — high := max(h,o,c), low := min(l,o,c). o/c are preserved.
        object.__setattr__(self, "high", max(self.high, self.open, self.close))
        object.__setattr__(self, "low", min(self.low, self.open, self.close))
        object.__setattr__(self, "timeframe", normalize_timeframe(self.timeframe))

    # -- conversions ---------------------------------------------------------
    def to_engine_candle(self):
        """Convert to the engine's core.models.Candle."""
        from core.models import Candle
        return Candle(
            timestamp=self.timestamp, open=self.open, high=self.high,
            low=self.low, close=self.close, volume=self.volume,
            instrument=self.instrument, timeframe=self.timeframe,
            open_interest=self.open_interest, vwap=self.vwap,
            trades_count=self.trades_count, provider=self.provider,
        )

    @classmethod
    def from_engine_candle(cls, candle, provider: Optional[str] = None,
                           source_symbol: Optional[str] = None) -> "NormalizedCandle":
        return cls(
            timestamp=candle.timestamp, open=candle.open, high=candle.high,
            low=candle.low, close=candle.close, volume=candle.volume,
            instrument=candle.instrument or "", timeframe=candle.timeframe or "1d",
            open_interest=getattr(candle, "open_interest", None),
            vwap=getattr(candle, "vwap", None),
            trades_count=getattr(candle, "trades_count", None),
            provider=provider or getattr(candle, "provider", None),
            source_symbol=source_symbol,
        )

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "open": self.open, "high": self.high,
            "low": self.low, "close": self.close,
            "volume": self.volume, "instrument": self.instrument,
            "timeframe": self.timeframe, "open_interest": self.open_interest,
            "vwap": self.vwap, "trades_count": self.trades_count,
            "provider": self.provider, "source_symbol": self.source_symbol,
            "exchange": self.exchange, "resampled": self.resampled,
        }

    def _replace_stamps(self, **kwargs) -> "NormalizedCandle":
        data = self.to_dict()
        data["raw"] = self.raw
        data.update(kwargs)
        return NormalizedCandle(**data)


# ---------------------------------------------------------------------------
# Rows / DataFrame -> NormalizedCandle converters (the ONLY accepted entrances)
# ---------------------------------------------------------------------------

_ROW_ALIASES = {
    "timestamp": ("timestamp", "ts", "datetime", "date", "time", "0"),
    "open": ("open", "o", "open_price", "1"),
    "high": ("high", "h", "2"),
    "low": ("low", "l", "3"),
    "close": ("close", "c", "close_price", "4"),
    "volume": ("volume", "v", "vol", "5"),
    "open_interest": ("open_interest", "oi", "6"),
    "vwap": ("vwap", "7"),
    "trades_count": ("trades", "trade_count", "trades_count", "8"),
}


def _row_get(row: Any, keys) -> Any:
    if isinstance(row, dict):
        for k in keys:
            if k in row and row[k] is not None:
                return row[k]
        return None
        # sequence row ([ts, o, h, l, c, v, ...]) — try each key as a positional index
    for k in keys:
        try:
            idx = int(k)
        except (TypeError, ValueError):
            continue
        if idx < len(row) and row[idx] is not None:
            return row[idx]
    return None


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def candles_from_rows(rows: Iterable[Any], instrument: str, timeframe: str,
                      provider: Optional[str] = None,
                      source_symbol: Optional[str] = None,
                      exchange: Optional[str] = None) -> List[NormalizedCandle]:
    """Convert provider rows (dicts or [ts,o,h,l,c,v,(oi),...] arrays) into
    NormalizedCandles. Invalid rows are skipped (count reported)."""
    out: List[NormalizedCandle] = []
    skipped = 0
    for row in rows:
        ts_raw = _row_get(row, _ROW_ALIASES["timestamp"])
        o = _num(_row_get(row, _ROW_ALIASES["open"]))
        h = _num(_row_get(row, _ROW_ALIASES["high"]))
        l = _num(_row_get(row, _ROW_ALIASES["low"]))
        c = _num(_row_get(row, _ROW_ALIASES["close"]))
        v = _num(_row_get(row, _ROW_ALIASES["volume"]), default=0.0)
        oi = _num(_row_get(row, _ROW_ALIASES["open_interest"]))
        vw = _num(_row_get(row, _ROW_ALIASES["vwap"]))
        tc = _num(_row_get(row, _ROW_ALIASES["trades_count"]))
        if ts_raw is None or None in (o, h, l, c):
            skipped += 1
            continue
        try:
            out.append(NormalizedCandle(
                timestamp=normalize_timestamp(ts_raw),
                open=o, high=h, low=l, close=c, volume=v or 0.0,
                instrument=instrument, timeframe=timeframe,
                open_interest=oi, vwap=vw,
                trades_count=int(tc) if tc is not None else None,
                provider=provider, source_symbol=source_symbol,
                exchange=exchange, raw=row,
            ))
        except (ValueError, OverflowError):
            skipped += 1
    if skipped:
        from utils import Logger
        Logger("market_data.normalize").warning(
            f"skipped {skipped} invalid row(s) for {instrument}"
        )
    return out


def candles_from_dataframe(df: pd.DataFrame, instrument: str, timeframe: str,
                           provider: Optional[str] = None,
                           source_symbol: Optional[str] = None,
                           exchange: Optional[str] = None) -> List[NormalizedCandle]:
    """Convert an OHLCV DataFrame (DatetimeIndex + open/high/low/close[/volume/
    open_interest...]) into NormalizedCandles."""
    if df is None or df.empty:
        return []
    rows = []
    for ts, r in df.iterrows():
        rows.append({
            "timestamp": ts,
            "open": r.get("open"),
            "high": r.get("high"),
            "low": r.get("low"),
            "close": r.get("close"),
            "volume": r.get("volume", 0) or 0,
            "open_interest": r.get("open_interest"),
            "vwap": r.get("vwap"),
            "trades": r.get("trades"),
        })
    return candles_from_rows(rows, instrument, timeframe, provider,
                             source_symbol, exchange)


def candles_to_dataframe(candles: Iterable[NormalizedCandle]) -> pd.DataFrame:
    """NormalizedCandles -> standard OHLCV DataFrame (IST DatetimeIndex)."""
    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    data = [{
        "open": c.open, "high": c.high, "low": c.low, "close": c.close,
        "volume": c.volume,
        **({"open_interest": c.open_interest} if c.open_interest is not None else {}),
    } for c in candles]
    df = pd.DataFrame(data)
    df.index = pd.DatetimeIndex([c.timestamp for c in candles], name="timestamp")
    return df


def resample_normalized_candles(candles: List[NormalizedCandle],
                                target_timeframe: str) -> List[NormalizedCandle]:
    """Aggregate normalized candles to a higher (coarser) timeframe."""
    if not candles:
        return []
    target_tf = normalize_timeframe(target_timeframe)
    base_min = timeframe_minutes(candles[0].timeframe)
    tgt_min = CANONICAL_TIMEFRAMES[target_tf]
    if tgt_min <= base_min:
        return list(candles)
    out: List[NormalizedCandle] = []
    cur = None
    bucket = -1
    session_open = timedelta(hours=9, minutes=15)
    session_minutes = 9 * 60 + 15
    for c in candles:
        ts = ensure_ist(c.timestamp)
        if tgt_min < 1440:
            day_start = ts.replace(hour=0, minute=0, second=0, microsecond=0)
            elapsed = (ts - day_start).total_seconds() / 60 - session_minutes
            bucket_minutes = int(elapsed // tgt_min) * tgt_min
            bucket_ts = day_start + session_open + timedelta(minutes=bucket_minutes)
            b = int(bucket_ts.timestamp() // 60)
        else:
            b = (int(ts.timestamp()) // 60 // tgt_min) * tgt_min
        if b != bucket:
            if cur is not None:
                out.append(cur)
            bucket = b
            cur = NormalizedCandle(
                timestamp=datetime.fromtimestamp(b * 60, tz=IST),
                open=c.open, high=c.high, low=c.low, close=c.close,
                volume=c.volume, instrument=c.instrument, timeframe=target_tf,
                open_interest=c.open_interest, vwap=c.vwap,
                trades_count=c.trades_count,
                provider=c.provider or "normalized",
                source_symbol=c.source_symbol, exchange=c.exchange,
                resampled=True,
            )
        else:
            tc_sum = ((cur.trades_count or 0) + (c.trades_count or 0)
                      if (cur.trades_count is not None or c.trades_count is not None)
                      else None)
            cur = NormalizedCandle(
                timestamp=cur.timestamp, open=cur.open,
                high=max(cur.high, c.high), low=min(cur.low, c.low),
                close=c.close, volume=(cur.volume or 0) + (c.volume or 0),
                instrument=cur.instrument, timeframe=cur.timeframe,
                open_interest=c.open_interest if c.open_interest is not None else cur.open_interest,
                vwap=c.vwap if c.vwap is not None else cur.vwap,
                trades_count=tc_sum,
                provider=cur.provider, source_symbol=cur.source_symbol,
                exchange=cur.exchange, resampled=True,
            )
    if cur is not None:
        out.append(cur)
    return out


def validate_candles(candles: List[NormalizedCandle]) -> List[NormalizedCandle]:
    """Sort by timestamp and drop exact-duplicate timestamps (first wins)."""
    ordered = sorted(candles, key=lambda c: c.timestamp)
    seen = set()
    out = []
    for c in ordered:
        if c.timestamp in seen:
            continue
        seen.add(c.timestamp)
        out.append(c)
    return out


def ensure_normalized_candles(candles: Any, context: str = "") -> List[NormalizedCandle]:
    """Hard gate: the backtest engine consumes ONLY normalized candles.

    Accepts List[NormalizedCandle] (validated/sorted/deduped) or engine
    core.models.Candle objects (auto-wrapped). Raw DataFrames/dicts/provider
    payloads are rejected with TypeError — they must go through
    candles_from_rows / candles_from_dataframe at the provider boundary.
    """
    if candles is None:
        return []
    if isinstance(candles, (pd.DataFrame, dict)):
        raise TypeError(
            f"Raw {type(candles).__name__} is not allowed in the backtest engine"
            f"{f' ({context})' if context else ''}; normalize first via "
            f"market_data.normalize."
        )
    out: List[NormalizedCandle] = []
    for c in candles:
        if isinstance(c, NormalizedCandle):
            out.append(c)
        elif type(c).__module__.startswith("core.models"):
            out.append(NormalizedCandle.from_engine_candle(c))
        else:
            raise TypeError(
                f"Unnormalized candle object {type(c).__name__} is not allowed "
                f"in the backtest engine{f' ({context})' if context else ''}."
            )
    return validate_candles(out)


def resample_candles(df: pd.DataFrame, target_timeframe: str) -> pd.DataFrame:
    """Legacy DataFrame resampler (kept for compat) using canonical aggregation."""
    tf = normalize_timeframe(target_timeframe)
    minutes = CANONICAL_TIMEFRAMES[tf]
    if minutes < 1440:
        freq = f"{minutes}min"
    elif minutes == 1440:
        freq = "1D"
    else:
        freq = "1W"
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df.columns:
        agg["volume"] = "sum"
    cols = [c for c in agg if c in df.columns]
    resampled = df[cols].resample(freq).agg(agg).dropna(how="any")
    if "open_interest" in df.columns:
        resampled["open_interest"] = df["open_interest"].resample(freq).last()
    return resampled





