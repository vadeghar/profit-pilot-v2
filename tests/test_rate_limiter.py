"""Tests for the shared per-provider rate limiter.

Note: the session-wide autouse fixture in tests/conftest.py disables rate
limiting for the whole suite (so other tests using fake providers aren't
slowed down) - these tests explicitly re-enable it around each assertion.
"""
import threading
import time

from market_data.rate_limiter import ProviderRateLimiter, get_limiter, reset_limiters, set_enabled


def test_get_limiter_returns_same_instance_for_same_provider():
    reset_limiters()
    a = get_limiter("breeze")
    b = get_limiter("breeze")
    assert a is b


def test_get_limiter_is_distinct_per_provider():
    reset_limiters()
    breeze = get_limiter("breeze")
    angel = get_limiter("angel")
    assert breeze is not angel


def test_unknown_provider_gets_the_fallback_config():
    reset_limiters()
    limiter = get_limiter("some_new_provider")
    assert limiter.min_interval_seconds > 0


def test_disabled_limiter_never_sleeps():
    set_enabled(False)
    try:
        limiter = ProviderRateLimiter(min_interval_seconds=5.0, max_concurrent=1)
        t0 = time.monotonic()
        with limiter:
            pass
        with limiter:
            pass
        assert time.monotonic() - t0 < 0.5
    finally:
        set_enabled(True)


def test_enabled_limiter_spaces_out_consecutive_calls():
    set_enabled(True)
    try:
        limiter = ProviderRateLimiter(min_interval_seconds=0.2, max_concurrent=2)
        t0 = time.monotonic()
        with limiter:
            pass
        with limiter:
            pass
        assert time.monotonic() - t0 >= 0.2
    finally:
        set_enabled(False)


def test_enabled_limiter_bounds_concurrency():
    set_enabled(True)
    try:
        limiter = ProviderRateLimiter(min_interval_seconds=0.0, max_concurrent=1)
        in_flight = []
        max_seen = [0]
        lock = threading.Lock()

        def worker():
            with limiter:
                with lock:
                    in_flight.append(1)
                    max_seen[0] = max(max_seen[0], len(in_flight))
                time.sleep(0.05)
                with lock:
                    in_flight.pop()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert max_seen[0] == 1  # never more than max_concurrent in flight at once
    finally:
        set_enabled(False)
