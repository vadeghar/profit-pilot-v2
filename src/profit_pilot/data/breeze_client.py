"""Breeze historical candle provider.

Uses ICICI Direct's official ``breeze-connect`` SDK and exposes the same
``MarketDataProvider`` shape used by the backtest engine.  This module only
reads historical candles; it never places orders.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timezone
from collections.abc import Iterable

from .models import MarketState

BREEZE_STOCK_CODES = {
    "RELIANCE": "RELIND",
    "HDFCBANK": "HDFBAN",
    "ICICIBANK": "ICIBAN",
    "AXISBANK": "AXIBAN",
    "BAJFINANCE": "BAJFIN",
    "SBIN": "STATEB",
    "ITC": "ITC",
    "LT": "LARTOU",
}


class BreezeConfigurationError(RuntimeError):
    pass


class BreezeMarketDataProvider:
    """Read NSE cash candles from Breeze historical-data-v2."""

    def __init__(self, symbol: str, interval: str = "1day", env: dict[str, str] | None = None):
        self.symbol = BREEZE_STOCK_CODES.get(symbol, symbol)
        self.interval = interval
        self.env = env or os.environ
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        api_key = self.env.get("BREEZE_API_KEY")
        api_secret = self.env.get("BREEZE_API_SECRET")
        session_token = self.env.get("BREEZE_SESSION_TOKEN")
        if not api_key or not api_secret or not session_token:
            raise BreezeConfigurationError(
                "BREEZE_API_KEY, BREEZE_API_SECRET, and BREEZE_SESSION_TOKEN are required"
            )
        try:
            from breeze_connect import BreezeConnect
        except ImportError as exc:
            raise BreezeConfigurationError(
                "Install the Breeze SDK with: python -m pip install breeze-connect"
            ) from exc
        client = BreezeConnect(api_key=api_key)
        response = client.generate_session(api_secret=api_secret, session_token=session_token)
        if isinstance(response, dict) and response.get("Error"):
            raise BreezeConfigurationError(f"Breeze session failed: {response['Error']}")
        self._client = client
        return client

    @staticmethod
    def _iso(value: date, end: bool = False) -> str:
        stamp = datetime.combine(value, time(23, 59, 59) if end else time(0, 0), tzinfo=timezone.utc)
        return stamp.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def candles(self, start: date, end: date) -> list[dict]:
        response = self._get_client().get_historical_data_v2(
            interval=self.interval,
            from_date=self._iso(start),
            to_date=self._iso(end, end=True),
            stock_code=self.symbol,
            exchange_code="NSE",
            product_type="cash",
        )
        if not isinstance(response, dict):
            raise RuntimeError(f"Unexpected Breeze response: {type(response).__name__}")
        if response.get("Error"):
            raise RuntimeError(f"Breeze historical-data error: {response['Error']}")
        return response.get("Success") or []

    def states(self, start: date, end: date) -> Iterable[MarketState]:
        for row in self.candles(start, end):
            stamp = datetime.fromisoformat(str(row["datetime"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            yield MarketState(
                timestamp=stamp,
                symbol=self.symbol,
                price=float(row["close"]),
                fields={
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "volume": float(row.get("volume") or 0),
                },
            )
