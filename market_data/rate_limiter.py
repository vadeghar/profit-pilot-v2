"""Process-wide per-provider rate limiting.

``TradingDayFetcher`` (and each paper trader) already throttles its own
sequential requests, but that throttle is per-instance: when multiple
strategies run concurrently - two paper traders, or a paper trader alongside
a live backtest - each one throttles independently, and nothing stops them
from collectively exceeding the provider's real rate limit. This is the
shared gate every provider call goes through regardless of which fetcher or
paper trader issues it, keyed by provider name (``"breeze"``, ``"angel"``,
``"yfinance"``) so unrelated providers never block each other.
"""
from __future__ import annotations

import threading
import time
from typing import Dict

DEFAULT_LIMITS = {
    "breeze": {"min_interval_seconds": 0.6, "max_concurrent": 2},
    "angel": {"min_interval_seconds": 0.3, "max_concurrent": 3},
    "yfinance": {"min_interval_seconds": 0.1, "max_concurrent": 4},
}
_FALLBACK_LIMIT = {"min_interval_seconds": 0.3, "max_concurrent": 2}


_enabled = True


def set_enabled(enabled: bool) -> None:
    """Global on/off switch. Tests disable this (see tests/conftest.py) so a
    suite exercising dozens of fake-provider calls isn't throttled by a real
    inter-request delay meant for an actual broker API."""
    global _enabled
    _enabled = enabled


def is_enabled() -> bool:
    return _enabled


class ProviderRateLimiter:
    """Bounds concurrent in-flight requests and spaces consecutive ones out."""

    def __init__(self, min_interval_seconds: float = 0.3, max_concurrent: int = 2):
        self.min_interval_seconds = min_interval_seconds
        self._semaphore = threading.Semaphore(max_concurrent)
        self._spacing_lock = threading.Lock()
        self._last_call = 0.0

    def acquire(self) -> None:
        if not _enabled:
            return
        self._semaphore.acquire()
        with self._spacing_lock:
            wait = self._last_call + self.min_interval_seconds - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def release(self) -> None:
        if not _enabled:
            return
        self._semaphore.release()

    def __enter__(self) -> "ProviderRateLimiter":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


_limiters: Dict[str, ProviderRateLimiter] = {}
_registry_lock = threading.Lock()


def get_limiter(provider_name: str) -> ProviderRateLimiter:
    """The process-wide limiter for one provider (lazily created, cached)."""
    with _registry_lock:
        limiter = _limiters.get(provider_name)
        if limiter is None:
            cfg = DEFAULT_LIMITS.get(provider_name, _FALLBACK_LIMIT)
            limiter = ProviderRateLimiter(**cfg)
            _limiters[provider_name] = limiter
        return limiter


def reset_limiters() -> None:
    """Test/debug helper: drop all cached limiters (state, not config)."""
    with _registry_lock:
        _limiters.clear()


__all__ = ["ProviderRateLimiter", "get_limiter", "reset_limiters", "DEFAULT_LIMITS",
          "set_enabled", "is_enabled"]
