"""Shared, provider-agnostic candle cache (day granularity).

Cache key: ``(provider, instrument_key, timeframe, trading_day)``.

A trading day strictly before "today" (IST) is treated as immutable: once an
exchange session has closed, its candles - including an expired option
contract's - never change, so a hit for a past day skips the network
entirely and is cached forever. "Today" is never written to or served from
the cache: the session is still forming, so every call for today's date is
always a live fetch.

Backend: a small SQLite index (``data/cache/candles/index.sqlite3``) tracks
which ``(provider, instrument_key, timeframe, day)`` rows exist and where
their payload lives; the actual candles are stored as one flat JSON file per
day under ``data/cache/candles/<provider>/<instrument_key>/<timeframe>/<day>.json``.
SQLite gives fast existence checks and easy pruning without a database
server; keeping payloads as plain files keeps them easy to inspect by hand.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, List, Optional

import platform_config
from market_data.normalize import NormalizedCandle

DEFAULT_CACHE_DIR = platform_config.DATA_DIR / "cache" / "candles"


def instrument_cache_key(instrument: Any) -> str:
    """Stable, filesystem-safe key for a plain symbol or a Breeze option dict."""
    if isinstance(instrument, dict):
        parts = [str(instrument.get(k, "")) for k in
                 ("stock_code", "exchange_code", "expiry_date", "strike_price", "right")]
        return "OPT_" + "_".join(p.replace(":", "").replace("/", "-").replace(" ", "") for p in parts)
    return str(instrument).upper().replace(":", "_").replace("/", "-")


class CandleCache:
    """Day-granularity cache for normalized candles. Thread-safe."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)
        self.db_path = self.cache_dir / "index.sqlite3"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candle_days (
                    provider TEXT NOT NULL, instrument_key TEXT NOT NULL,
                    timeframe TEXT NOT NULL, day TEXT NOT NULL,
                    path TEXT NOT NULL, cached_at TEXT NOT NULL,
                    PRIMARY KEY (provider, instrument_key, timeframe, day)
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=30)

    def _path_for(self, provider: str, instrument_key: str, timeframe: str, day: date) -> Path:
        return self.cache_dir / provider / instrument_key / timeframe / f"{day.isoformat()}.json"

    def get_day(self, provider: str, instrument: Any, timeframe: str, day: date,
               today: Optional[date] = None) -> Optional[List[NormalizedCandle]]:
        """A full day's cached candles, or ``None`` on a miss.

        ``day >= today`` is always a miss by design - an in-progress or
        future session is never served from (or written to) the cache.
        """
        if day >= (today or datetime.now().date()):
            return None
        key = instrument_cache_key(instrument)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT path FROM candle_days WHERE provider=? AND instrument_key=? "
                "AND timeframe=? AND day=?",
                (provider, key, timeframe, day.isoformat()),
            ).fetchone()
        if not row:
            self.misses += 1
            return None
        path = Path(row[0])
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self.misses += 1
            return None
        self.hits += 1
        return [NormalizedCandle(**{**r, "timestamp": datetime.fromisoformat(r["timestamp"])})
               for r in rows]

    def put_day(self, provider: str, instrument: Any, timeframe: str, day: date,
               candles: List[NormalizedCandle], today: Optional[date] = None) -> None:
        """Persist a full day's candles. A no-op for today/future days."""
        if day >= (today or datetime.now().date()):
            return
        key = instrument_cache_key(instrument)
        path = self._path_for(provider, key, timeframe, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([c.to_dict() for c in candles]), encoding="utf-8")
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO candle_days VALUES (?, ?, ?, ?, ?, ?)",
                (provider, key, timeframe, day.isoformat(), str(path), datetime.now().isoformat()),
            )

    def stats(self) -> dict:
        return {"hits": self.hits, "misses": self.misses}

    def prune_older_than(self, days: int, today: Optional[date] = None) -> int:
        """Drop cache entries older than ``days`` (disk hygiene). Returns rows removed."""
        cutoff = (today or datetime.now().date()).toordinal() - days
        removed = 0
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT provider, instrument_key, timeframe, day, path FROM candle_days").fetchall()
            for provider, key, timeframe, day, path in rows:
                if date.fromisoformat(day).toordinal() < cutoff:
                    Path(path).unlink(missing_ok=True)
                    conn.execute(
                        "DELETE FROM candle_days WHERE provider=? AND instrument_key=? "
                        "AND timeframe=? AND day=?",
                        (provider, key, timeframe, day),
                    )
                    removed += 1
        return removed


_default_cache: Optional[CandleCache] = None


def default_cache() -> CandleCache:
    """Process-wide default cache instance (lazy singleton)."""
    global _default_cache
    if _default_cache is None:
        _default_cache = CandleCache()
    return _default_cache


__all__ = ["CandleCache", "instrument_cache_key", "default_cache", "DEFAULT_CACHE_DIR"]
