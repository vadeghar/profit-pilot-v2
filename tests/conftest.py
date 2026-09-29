"""Session-wide test fixtures."""
import pytest

from market_data.rate_limiter import set_enabled


@pytest.fixture(autouse=True, scope="session")
def _disable_provider_rate_limiting():
    """The shared per-provider rate limiter (market_data/rate_limiter.py)
    exists to space out real API calls in production; tests exercise dozens
    of fake-provider calls per file and must not pay that real-world delay.
    """
    set_enabled(False)
    yield
    set_enabled(True)
