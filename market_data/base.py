"""Abstract base class for all historical data providers.

Contract (v2 — normalized):
- `get_historical_candles` MUST return `List[NormalizedCandle]` built via
  `market_data.normalize` helpers (candles_from_rows / candles_from_dataframe).
  Raw broker payloads, DataFrames or engine Candle lists must never escape a
  provider — the backtest engine consumes normalized candles only.
- `normalize_symbol` / `supported_timeframes` have concrete defaults so
  subclasses only override what they need.
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from .normalize import (
    CANONICAL_TIMEFRAMES,
    NormalizedCandle,
    normalize_timeframe,
)


class HistoricalDataProvider(ABC):
    """Abstract Base Class for all historical data providers (normalized)."""

    #: provider name used in NormalizedCandle.provider ("yfinance"/"breeze"/"angel")
    name: str = "unknown"

    @abstractmethod
    def get_historical_candles(self, symbol: str, timeframe: str,
                               start_date, end_date) -> List[NormalizedCandle]:
        """Fetch historical OHLCV data as normalized candles (sorted, IST)."""
        raise NotImplementedError

    def normalize_symbol(self, symbol: str) -> str:
        """Convert a generic symbol to the provider's native format.
        Default: pass-through (providers should override)."""
        return symbol

    @property
    def supported_timeframes(self) -> List[str]:
        """Timeframes natively supported by the provider (canonical names).
        Default: all canonical timeframes; providers should narrow this."""
        return sorted(CANONICAL_TIMEFRAMES)

    def supports_timeframe(self, timeframe: str) -> bool:
        try:
            canonical = normalize_timeframe(timeframe)
        except ValueError:
            return False
        return canonical in {normalize_timeframe(tf) for tf in self.supported_timeframes}

    def ensure_authenticated(self) -> None:
        """Verify broker credentials/session before any data access.

        Called by the web layer right after construction (fail fast) and at
        the top of every `get_historical_candles` call — including cache
        hits — so a backtest can never proceed on stale cache when the
        broker authentication is failing. Providers without auth (yfinance)
        inherit this no-op. Must raise on failure, never return False.
        """
        return None

    @property
    def can_resample(self) -> bool:
        """Whether the provider supports resampling (default True)."""
        return True

    def resample(self, candles: List[NormalizedCandle],
                 target_timeframe: str) -> List[NormalizedCandle]:
        """Resample normalized candles to a coarser timeframe (shared impl)."""
        from .normalize import resample_normalized_candles
        return resample_normalized_candles(candles, target_timeframe)

