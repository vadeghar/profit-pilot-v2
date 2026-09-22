"""
Angel One Real Historical Data Provider for Backtesting Engine
Caches data locally in ./data/historical/ to minimize API hits
and strictly respects Angel One API rate limits (<= 3 req/sec with exponential backoff).
"""

import os
import json
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from .base import HistoricalDataProvider
from .normalize import NormalizedCandle, candles_from_rows, normalize_timeframe
from utils import Logger
from utils.timezone import IST, angel_request_str, ensure_ist, now_ist, parse_broker_timestamp
import platform_config


class AngelHistoricalDataProvider(HistoricalDataProvider):
    """Historical data provider utilizing Angel One SmartAPI with disk caching and rate limiter.

    Returns normalized candles (market_data.normalize) only.
    """

    name = "angel"

    # canonical timeframe -> SmartAPI getCandleData interval
    _INTERVAL_MAP = {
        "1m": "ONE_MINUTE", "3m": "THREE_MINUTE", "5m": "FIVE_MINUTE",
        "10m": "TEN_MINUTE", "15m": "FIFTEEN_MINUTE", "30m": "THIRTY_MINUTE",
        "1h": "ONE_HOUR", "1d": "ONE_DAY",
    }

    def __init__(self, broker=None, cache_dir: Optional[str] = None):
        self.broker = broker
        self.cache_dir = cache_dir or str(platform_config.HISTORICAL_DATA_DIR)
        self.logger = Logger("data_provider.angel_one")
        os.makedirs(self.cache_dir, exist_ok=True)

        # Mapping for MCX instruments to tokens and symbols
        self.mcx_token_map = {
            'MCX_CRUDEOIL': {'token': '565899', 'symbol': 'CRUDEOIL21SEP26FUT', 'exchange': 'MCX', 'point_value': 100},
            'CRUDEOIL': {'token': '565899', 'symbol': 'CRUDEOIL21SEP26FUT', 'exchange': 'MCX', 'point_value': 100},
            'MCX:CRUDEOIL': {'token': '565899', 'symbol': 'CRUDEOIL21SEP26FUT', 'exchange': 'MCX', 'point_value': 100},

            'MCX_GOLD': {'token': '483079', 'symbol': 'GOLD05OCT26FUT', 'exchange': 'MCX', 'point_value': 1},
            'GOLD': {'token': '483079', 'symbol': 'GOLD05OCT26FUT', 'exchange': 'MCX', 'point_value': 1},
            'MCX:GOLD': {'token': '483079', 'symbol': 'GOLD05OCT26FUT', 'exchange': 'MCX', 'point_value': 1},

            'MCX_GOLDM': {'token': '569003', 'symbol': 'GOLDM05OCT26FUT', 'exchange': 'MCX', 'point_value': 10},
            'GOLDM': {'token': '569003', 'symbol': 'GOLDM05OCT26FUT', 'exchange': 'MCX', 'point_value': 10},
            'MCX:GOLDM': {'token': '569003', 'symbol': 'GOLDM05OCT26FUT', 'exchange': 'MCX', 'point_value': 10},

            'MCX_SILVER': {'token': '495214', 'symbol': 'SILVER04DEC26FUT', 'exchange': 'MCX', 'point_value': 30},
            'SILVER': {'token': '495214', 'symbol': 'SILVER04DEC26FUT', 'exchange': 'MCX', 'point_value': 30},
            'MCX:SILVER': {'token': '495214', 'symbol': 'SILVER04DEC26FUT', 'exchange': 'MCX', 'point_value': 30},

            'MCX_SILVERM': {'token': '483080', 'symbol': 'SILVERM30NOV26FUT', 'exchange': 'MCX', 'point_value': 5},
            'SILVERM': {'token': '483080', 'symbol': 'SILVERM30NOV26FUT', 'exchange': 'MCX', 'point_value': 5},
            'MCX:SILVERM': {'token': '483080', 'symbol': 'SILVERM30NOV26FUT', 'exchange': 'MCX', 'point_value': 5},
        }

    @property
    def supported_timeframes(self) -> List[str]:
        return list(self._INTERVAL_MAP)

    def normalize_symbol(self, symbol: str) -> str:
        """Convert a generic symbol ('NSE:RELIANCE', 'RELIANCE') to the
        SmartAPI trading symbol (bare uppercase ticker)."""
        if ":" in symbol:
            return symbol.split(":", 1)[1].upper()
        return symbol.upper()

    def ensure_authenticated(self) -> None:
        """Authenticate the Angel One SmartAPI session (fail fast, never None).

        Verifies cached session tokens if present, else connects from the
        .env ANGEL_* credentials. Raises RuntimeError on missing credentials
        or failed login — the caller (web_app / backtest gate) must abort.
        """
        broker = self._connect_from_env()
        if broker is None or getattr(broker, "client", None) is None:
            raise RuntimeError(
                "Angel One authentication failed: missing ANGEL_API_KEY / "
                "ANGEL_CLIENT_CODE / ANGEL_PASSWORD_OR_MPIN / ANGEL_TOTP_SECRET "
                f"in {platform_config.ENV_FILE}, or login rejected."
            )
        # Lightweight live check: the session must list a profile.
        try:
            profile = broker.client.getProfile(refreshToken=broker.refresh_token)
            if not (isinstance(profile, dict) and profile.get("status")):
                raise RuntimeError(str(profile)[:300])
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Angel One session verification failed: {e}") from e

    def get_historical_candles(
        self,
        instrument: str,
        timeframe: str = "1d",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[NormalizedCandle]:
        """Fetch historical candles with authenticated session and local cache.

        Authentication is verified BEFORE any cache read: a backtest may
        never proceed on stale cache when broker auth is failing.
        """
        timeframe = normalize_timeframe(timeframe)
        self.ensure_authenticated()
        clean_key = instrument.upper().replace(':', '_')
        cache_path = os.path.join(self.cache_dir, f"{clean_key}_{timeframe}.json")


        # Also check MCX subfolder
        mcx_cache_path = os.path.join(self.cache_dir, "mcx", f"{clean_key}.json")
        if os.path.exists(mcx_cache_path):
            cache_path = mcx_cache_path

        # 1. Check if cached data exists
        if os.path.exists(cache_path):
            self.logger.info(f"Loading {clean_key} candles from local cache: {cache_path}")
            try:
                with open(cache_path, 'r') as f:
                    cache_data = json.load(f)
                    candles_raw = cache_data.get('candles', [])
                    return self._parse_angel_candles(instrument, timeframe, candles_raw, start_date, end_date)
            except Exception as e:
                self.logger.warning(f"Error reading cache for {clean_key}: {e}. Will fetch live.")

        # 2. Broker is already authenticated (ensure_authenticated ran first);
        # resolve + fetch live bars.
        if self.broker and hasattr(self.broker, 'client') and self.broker.client:
            inst_info = self._resolve_instrument(instrument)

            interval = self._INTERVAL_MAP[timeframe]
            now = now_ist()
            to_str = angel_request_str(end_date or now)
            from_str = angel_request_str(start_date or (now - timedelta(days=700)))

            # Respect Angel rate limit (sleep at least 2.5s)
            time.sleep(2.5)

            try:
                self.logger.info(f"Fetching {clean_key} ({inst_info['token']}) from Angel One API...")
                res = self.broker.client.getCandleData({
                    'exchange': inst_info['exchange'],
                    'symboltoken': inst_info['token'],
                    'interval': interval,
                    'fromdate': from_str,
                    'todate': to_str
                })
                data = res.get('data') or []
                if data:
                    # Save to cache
                    with open(cache_path, 'w') as f:
                        json.dump({
                            'instrument': instrument,
                            'token': inst_info['token'],
                            'symbol': inst_info.get('symbol'),
                            'interval': interval,
                            'count': len(data),
                            'candles': data
                        }, f, indent=2)
                    return self._parse_angel_candles(instrument, timeframe, data,
                                                     inst_info, start_date, end_date)
            except Exception as ex:
                self.logger.error(f"Failed to fetch Angel One data for {instrument}: {ex}")
                raise RuntimeError(
                    f"Angel One fetch failed for {instrument!r} @ {timeframe}: {ex}"
                ) from ex

        raise RuntimeError(
            f"No Angel One broker available for {instrument!r}; credentials missing "
            f"or authentication failed. Backtesting on fabricated data is not allowed."
        )

    # SmartAPI tokens for Indian indices (stable instrument tokens)
    _KNOWN_EXCHANGES = {
        ("NSE", "NIFTY"): {'exchange': 'NSE', 'token': '99926000', 'symbol': 'Nifty 50'},
        ("NSE", "NIFTY50"): {'exchange': 'NSE', 'token': '99926000', 'symbol': 'Nifty 50'},
        ("NSE", "BANKNIFTY"): {'exchange': 'NSE', 'token': '260105', 'symbol': 'Nifty Bank'},
        ("NSE", "FINNIFTY"): {'exchange': 'NSE', 'token': '99926037', 'symbol': 'Nifty Fin Service'},
        ("BSE", "SENSEX"): {'exchange': 'BSE', 'token': '99926017', 'symbol': 'SENSEX'},
    }

    def _connect_from_env(self):
        """Build + authenticate an AngelOneBroker from the .env ANGEL_* keys.

        Returns the broker on success, None when credentials are missing or
        login fails (the caller converts None into a loud RuntimeError).
        """
        try:
            env_file_path = str(platform_config.ENV_FILE)
            if not os.path.exists(env_file_path):
                return None
            env_map = {}
            with open(env_file_path) as ef:
                for line in ef:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env_map[k.strip()] = v.strip()
            if not env_map.get("ANGEL_API_KEY"):
                return None
            from brokers.angel_one import AngelOneBroker
            cfg = {
                "api_key": env_map.get("ANGEL_API_KEY"),
                "client_id": env_map.get("ANGEL_CLIENT_CODE"),
                "password": env_map.get("ANGEL_PASSWORD_OR_MPIN"),
                "totp_secret": env_map.get("ANGEL_TOTP_SECRET"),
            }
            broker = AngelOneBroker(cfg)
            if broker.authenticate():
                self.broker = broker
                return broker
            return None
        except Exception as e:
            self.logger.warning(f"Angel One auto-connect failed: {e}")
            return None

    def _resolve_instrument(self, instrument: str) -> dict:
        """Map a generic instrument string to {exchange, token, symbol}.

        Resolution order: MCX static map -> known index tokens -> Angel One
        searchScrip lookup. Raises ValueError when nothing matches (never
        guesses a token, since a wrong token silently returns wrong data).
        """
        clean = instrument.upper().replace(":", "_")
        if clean in self.mcx_token_map:
            return self.mcx_token_map[clean]
        if instrument in self.mcx_token_map:
            return self.mcx_token_map[instrument]
        if ":" in instrument:
            exchange, ticker = instrument.split(":", 1)
            exchange, ticker = exchange.upper(), ticker.upper()
        else:
            exchange, ticker = "NSE", instrument.upper()
        known = self._KNOWN_EXCHANGES.get((exchange, ticker))
        if known:
            return known
        client = getattr(self.broker, "client", None) if self.broker else None
        if client is not None:
            try:
                res = client.searchScrip(exchange=exchange, searchsymbol=ticker)
                matches = (res.get("data") or []) if isinstance(res, dict) else []
                for m in matches:
                    if (m.get("symbol") or "").upper() == ticker or \
                            (m.get("tradingSymbol") or "").upper() == ticker:
                        return {"exchange": exchange,
                                "token": str(m.get("symboltoken")),
                                "symbol": m.get("symbol") or ticker}
            except Exception as e:
                self.logger.warning(f"Angel searchScrip failed for {ticker}: {e}")
        raise ValueError(
            f"Cannot resolve Angel One instrument token for {instrument!r}. "
            f"Use an 'EXCHANGE:TICKER' symbol present in the instrument master "
            f"(e.g. 'NSE:RELIANCE', 'MCX:CRUDEOIL')."
        )

    def _parse_angel_candles(
        self,
        instrument: str,
        timeframe: str,
        raw_candles: List[Any],
        inst_info: dict,
        start_date: Optional[datetime],
        end_date: Optional[datetime]
    ) -> List[NormalizedCandle]:
        """Raw Angel rows ([ts, o, h, l, c, v]) -> normalized candles."""
        rows = []
        for row in raw_candles:
            rows.append({
                "timestamp": row[0],
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5] if len(row) > 5 else 0,
            })
        return candles_from_rows(
            rows,
            instrument=instrument,
            timeframe=timeframe,
            provider=self.name,
            source_symbol=inst_info.get("symbol"),
            exchange=inst_info.get("exchange"),
            start_date=start_date,
            end_date=end_date,
        )
