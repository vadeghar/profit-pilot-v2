"""Angel One SmartAPI historical candle provider.

Instrument/token resolution is deliberately kept in the data layer. The
provider accepts the project symbol (for example ``RELIANCE``), resolves its
Angel symbol token from ``data/angel_tokens.json`` by default, and returns the
same ``MarketState`` stream as the Breeze provider.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import date, datetime, time
from pathlib import Path

from .models import MarketState


class AngelConfigurationError(RuntimeError):
    pass


class AngelMarketDataProvider:
    def __init__(
        self,
        symbol: str,
        interval: str = "ONE_DAY",
        token_file: str | Path = "data/angel_tokens.json",
        env: dict[str, str] | None = None,
    ):
        self.project_symbol = symbol.upper()
        self.interval = interval.upper()
        self.token_file = Path(token_file)
        self.env = env or os.environ
        self._client = None

    def _token_record(self) -> dict:
        if not self.token_file.exists():
            raise AngelConfigurationError(f"Angel token file not found: {self.token_file}")
        payload = json.loads(self.token_file.read_text(encoding="utf-8"))
        records = payload.get("data", payload) if isinstance(payload, dict) else payload
        if isinstance(records, dict):
            records = [records]
        for row in records:
            if not isinstance(row, dict):
                continue
            names = {
                str(row.get(key, "")).upper()
                for key in ("symbol", "tradingsymbol", "name", "project_symbol")
            }
            if self.project_symbol in names:
                return row
        raise AngelConfigurationError(f"No Angel token mapping for {self.project_symbol}")

    def _get_client(self):
        if self._client is not None:
            return self._client
        api_key = self.env.get("ANGEL_API_KEY")
        client_code = self.env.get("ANGEL_CLIENT_CODE")
        password = self.env.get("ANGEL_PASSWORD_OR_MPIN")
        totp_secret = self.env.get("ANGEL_TOTP_SECRET")
        if not api_key or not client_code or not password or not totp_secret:
            raise AngelConfigurationError(
                "ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PASSWORD_OR_MPIN, "
                "and ANGEL_TOTP_SECRET are required"
            )
        try:
            import pyotp
            totp = pyotp.TOTP(totp_secret).now()
        except ImportError as exc:
            raise AngelConfigurationError(
                "Install pyotp with: python -m pip install pyotp"
            ) from exc
        try:
            from SmartApi import SmartConnect
        except ImportError as exc:
            raise AngelConfigurationError(
                "Install SmartAPI with: python -m pip install smartapi-python"
            ) from exc
        client = SmartConnect(api_key=api_key)
        session = client.generateSession(client_code, password, totp)
        if not isinstance(session, dict) or not session.get("status", False):
            raise AngelConfigurationError(f"Angel session failed: {session}")
        self._client = client
        return client

    @staticmethod
    def _date_time(value: date, end: bool = False) -> str:
        return datetime.combine(value, time(15, 30) if end else time(9, 15)).strftime("%Y-%m-%d %H:%M")

    def candles(self, start: date, end: date) -> list[dict]:
        row = self._token_record()
        token = row.get("token") or row.get("symboltoken") or row.get("symbolToken")
        if not token:
            raise AngelConfigurationError(f"Token missing for {self.project_symbol}")
        exchange = row.get("exchange", "NSE")
        tradingsymbol = row.get("tradingsymbol") or row.get("symbol") or self.project_symbol
        response = self._get_client().getCandleData({
            "exchange": exchange,
            "symboltoken": str(token),
            "interval": self.interval,
            "fromdate": self._date_time(start),
            "todate": self._date_time(end, end=True),
        })
        if not isinstance(response, dict) or not response.get("status", False):
            raise RuntimeError(f"Angel candle request failed: {response}")
        return [
            {"datetime": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5], "symbol": tradingsymbol}
            for r in (response.get("data") or [])
        ]

    def states(self, start: date, end: date) -> Iterable[MarketState]:
        for row in self.candles(start, end):
            stamp = datetime.fromisoformat(str(row["datetime"]).replace("Z", "+00:00"))
            yield MarketState(
                timestamp=stamp,
                symbol=self.project_symbol,
                price=float(row["close"]),
                fields={"open": float(row["open"]), "high": float(row["high"]), "low": float(row["low"]), "volume": float(row.get("volume") or 0)},
            )
