"""
ICICI Breeze Historical Data Provider for the Backtest Engine
Mirrors AngelHistoricalDataProvider conventions: local disk caching under
./data/historical/ and .env-based auto-initialization (BREEZE_* credentials).
"""

import json
import os
from datetime import datetime, timedelta
from typing import List, Optional

from .base import HistoricalDataProvider
from .normalize import (
    NormalizedCandle, candles_from_rows, normalize_timeframe,
    resample_normalized_candles,
)
from utils import Logger
from utils.timezone import (
    IST, breeze_utc_window_for_ist_day_chunk, ensure_ist, now_ist,
    parse_broker_timestamp,
)
import platform_config

from lorentzian_strategy.data_loader import (
    BREEZE_CHUNK_DAYS,
    BREEZE_INTERVAL_MAP,
    breeze_lookback_days,
    connect_breeze,
)


class BreezeHistoricalDataProvider(HistoricalDataProvider):
    """Historical data provider using ICICI Breeze get_historical_data_v2 with disk cache.

    Returns normalized candles (market_data.normalize) only.
    """

    name = "breeze"

    # canonical timeframes Breeze can serve — keys of the shared
    # BREEZE_INTERVAL_MAP (fetch interval + optional resample), e.g.
    # '15m' fetches 5-minute bars and resamples to 15m; '1wk' from 1day.
    @property
    def supported_timeframes(self) -> List[str]:
        from lorentzian_strategy.data_loader import BREEZE_INTERVAL_MAP
        return sorted(BREEZE_INTERVAL_MAP)

    def __init__(self, client=None, cache_dir: Optional[str] = None, env_path: Optional[str] = None):
        self.client = client
        self.env_path = env_path
        self.cache_dir = cache_dir or str(platform_config.HISTORICAL_DATA_DIR)
        self.logger = Logger("data_provider.breeze")
        os.makedirs(self.cache_dir, exist_ok=True)

    def _session_token(self) -> Optional[str]:
        """BREEZE_SESSION_TOKEN from the configured .env (used for verification)."""
        from lorentzian_strategy.data_loader import load_breeze_env
        env = load_breeze_env(self.env_path or str(platform_config.ENV_FILE))
        return env.get("BREEZE_SESSION_TOKEN")

    def ensure_authenticated(self) -> None:
        """Authenticate the Breeze session (no silent fallback tolerated).

        Raises RuntimeError listing the missing BREEZE_* keys or the exact
        connect failure, and verifies the live session — never logs-and-None.
        """
        if self.client is None:
            try:
                self.client = connect_breeze(env_path=self.env_path or str(platform_config.ENV_FILE))
            except Exception as e:
                raise RuntimeError(
                    f"Breeze authentication failed: {e}"
                ) from e
        # Verify the session is alive (identify the account holder). The SDK's
        # get_customer_details() must receive the api_session token explicitly —
        # called bare it answers {'Status': 500, 'Error': 'API Session cannot be
        # empty'} *without raising*, which would let a dead/expired session pass
        # this gate silently.
        token = self._session_token()
        try:
            try:
                details = self.client.get_customer_details(api_session=token or "")
            except TypeError:
                # Stub/fake clients taking no argument (unit tests).
                details = self.client.get_customer_details()
        except Exception as e:
            self.client = None
            raise RuntimeError(
                f"Breeze session verification failed for "
                f"{self.env_path or platform_config.ENV_FILE}: {e}"
            ) from e
        if not (isinstance(details, dict) and details.get("Status") == 200):
            err = details.get("Error") if isinstance(details, dict) else details
            self.client = None
            raise RuntimeError(
                f"Breeze session verification failed for "
                f"{self.env_path or platform_config.ENV_FILE}: {err or 'no response'} "
                f"— refresh BREEZE_SESSION_TOKEN via tools/breeze/breeze_auto_login.py."
            )

    def normalize_symbol(self, symbol: str) -> str:
        """Convert a generic symbol ('NSE:NIFTY', 'RELIANCE') to Breeze's
        native 'stock_code' using the shared ticker parser."""
        from platform_config import resolve_provider_symbol
        mapped = resolve_provider_symbol(symbol, self.name)
        if mapped:
            return mapped["provider_symbol"]
        try:
            from lorentzian_strategy.data_loader import parse_breeze_ticker
            stock_code, exchange_code, product_type = parse_breeze_ticker(symbol)
            return stock_code
        except Exception:
            return symbol.replace("NSE:", "").replace("BSE:", "").upper()

    def get_historical_candles(
        self,
        instrument: str,
        timeframe: str = "1d",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[NormalizedCandle]:
        """Fetch historical candles with authenticated session and local cache.

        Authentication is verified BEFORE any cache read: a backtest may
        never proceed on stale cache when broker auth is failing.
        """
        timeframe = normalize_timeframe(timeframe)
        self.ensure_authenticated()
        from lorentzian_strategy.data_loader import BREEZE_INTERVAL_MAP
        if timeframe not in BREEZE_INTERVAL_MAP:
            raise ValueError(
                f"Unsupported Breeze timeframe {timeframe!r}; supported: "
                f"{sorted(BREEZE_INTERVAL_MAP)}"
            )
        clean_key = instrument.upper().replace(":", "_")
        cache_path = os.path.join(self.cache_dir, f"{clean_key}_{timeframe}.json")


        if os.path.exists(cache_path):
            self.logger.info(f"Loading {clean_key} candles from local cache: {cache_path}")
            try:
                with open(cache_path) as f:
                    candles_raw = json.load(f).get("candles", [])
                return self._parse_rows(instrument, timeframe, candles_raw, start_date, end_date)
            except Exception as e:
                self.logger.warning(f"Error reading cache for {clean_key}: {e}. Will fetch live.")

        if self.client is None:
            try:
                self.client = connect_breeze(env_path=self.env_path or str(platform_config.ENV_FILE))
            except Exception as e:
                raise RuntimeError(
                    f"Could not initialize Breeze client for {instrument!r}: {e}. "
                    f"Check BREEZE_* credentials in "
                    f"{self.env_path or platform_config.ENV_FILE}."
                ) from e

        rows = self._fetch_rows(instrument, timeframe, start_date, end_date)
        if not rows:
            raise RuntimeError(
                f"No Breeze data returned for {instrument!r} @ {timeframe} "
                f"({start_date}..{end_date}). Check the stock code / product type; "
                f"backtesting on fabricated data is not allowed."
            )

        try:
            with open(cache_path, "w") as f:
                json.dump({"source": "breeze", "fetched_at": now_ist().isoformat(),
                           "candles": rows}, f)
        except Exception as e:
            self.logger.warning(f"Failed writing Breeze cache for {clean_key}: {e}")

        return self._parse_rows(instrument, timeframe, rows, start_date, end_date)

    def _fetch_rows(self, instrument: str, timeframe: str,
                    start_date: Optional[datetime], end_date: Optional[datetime]) -> list:
        if timeframe not in BREEZE_INTERVAL_MAP:
            raise ValueError(f"Unsupported Breeze timeframe {timeframe!r}; "
                             f"supported: {sorted(BREEZE_INTERVAL_MAP)}")
        interval = BREEZE_INTERVAL_MAP[timeframe][0]
        from lorentzian_strategy.data_loader import parse_breeze_ticker, resolve_breeze_stock_code
        mapped = platform_config.resolve_provider_symbol(instrument, self.name)
        source_instrument = mapped["provider_symbol"] if mapped else instrument
        stock_code, exchange_code, product_type = parse_breeze_ticker(source_instrument)
        # Historical data is served under the scrip master's ShortName
        # (RELIANCE -> RELIND); the raw NSE ticker yields `Success: []`.
        raw_code = stock_code
        stock_code = resolve_breeze_stock_code(stock_code, exchange_code)
        if stock_code != raw_code:
            self.logger.info(
                f"Resolved {raw_code} -> {stock_code} ({exchange_code}) via scrip master"
            )

        end_dt = ensure_ist(end_date) if end_date else now_ist()
        if start_date:
            start_dt = ensure_ist(start_date)
        else:
            start_dt = end_dt - timedelta(days=breeze_lookback_days(timeframe, 500))
        chunk = timedelta(days=BREEZE_CHUNK_DAYS[interval])

        rows = []
        cursor = start_dt
        while cursor < end_dt:
            chunk_end = min(cursor + chunk, end_dt)
            from_str, to_str = breeze_utc_window_for_ist_day_chunk(cursor, chunk_end)
            try:
                res = self.client.get_historical_data_v2(
                    interval=interval,
                    from_date=from_str,
                    to_date=to_str,
                    stock_code=stock_code,
                    exchange_code=exchange_code,
                    product_type=product_type,
                )
                if res and res.get("Status") == 200 and res.get("Success"):
                    batch = res["Success"]
                    if isinstance(batch, dict):
                        batch = batch.get("candles", batch.get("data", []))
                    # normalize to [ts, o, h, l, c, v] arrays for the cache/parser
                    rows.extend([
                        [r.get("datetime"), r.get("open"), r.get("high"), r.get("low"),
                         r.get("close"), r.get("volume")] if isinstance(r, dict) else r
                        for r in batch
                    ])
            except Exception as e:
                self.logger.error(f"Breeze fetch failed for {stock_code} "
                                  f"[{cursor:%Y-%m-%d}..{chunk_end:%Y-%m-%d}]: {e}")
            cursor = chunk_end + timedelta(seconds=1)
            if cursor < end_dt:
                import time
                time.sleep(0.4)
        return rows

    def _parse_rows(self, instrument: str, timeframe: str, rows: list,
                    start_date: Optional[datetime], end_date: Optional[datetime]) -> List[NormalizedCandle]:
        """Rows are [timestamp, open, high, low, close, volume] arrays (or dicts
        from legacy disk cache) -> normalized IST candles."""
        fetch_interval, resample_to = BREEZE_INTERVAL_MAP[timeframe]
        source_timeframe = {
            "1minute": "1m", "5minute": "5m", "30minute": "30m", "1day": "1d",
        }[fetch_interval]
        candles = candles_from_rows(
            rows, instrument=instrument, timeframe=source_timeframe,
            provider=self.name, source_symbol=self.normalize_symbol(instrument),
            exchange="NSE",
        )
        if resample_to:
            candles = resample_normalized_candles(candles, timeframe)
        if start_date:
            start_bound = ensure_ist(start_date)
            candles = [c for c in candles if c.timestamp >= start_bound]
        if end_date:
            end_bound = ensure_ist(end_date)
            candles = [c for c in candles if c.timestamp <= end_bound]
        return candles
