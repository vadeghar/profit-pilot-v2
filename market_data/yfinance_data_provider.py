import os
import pandas as pd
import yfinance as yf
from datetime import datetime
from typing import List, Optional
from .base import HistoricalDataProvider
from .normalize import (
    NormalizedCandle,
    candles_from_dataframe,
    normalize_timeframe,
)
from utils.timezone import IST

class YFinanceDataProvider(HistoricalDataProvider):
    """yfinance adapter — returns normalized candles only."""

    name = "yfinance"

    def __init__(self, cache_dir: Optional[str] = None):
        import platform_config
        self.cache_dir = cache_dir or str(platform_config.HISTORICAL_DATA_DIR)
        os.makedirs(self.cache_dir, exist_ok=True)

    # Yahoo index symbols for Indian market indices (tickers with ".NS"/".BO"
    # suffixes don't exist for indices on Yahoo).
    _INDEX_SYMBOLS = {
        ("NSE", "NIFTY"): "^NSEI",
        ("NSE", "NIFTY50"): "^NSEI",
        ("NSE", "BANKNIFTY"): "^NSEBANK",
        ("NSE", "FINNIFTY"): "NIFTY_FIN_SERVICE.NS",
        ("BSE", "SENSEX"): "^BSESN",
    }

    # canonical timeframe -> yfinance interval
    _INTERVAL_MAP = {
        "5m": "5m", "10m": "5m", "15m": "15m", "30m": "30m",
        "1h": "1h", "4h": "1h", "1d": "1d", "1mo": "1mo",
    }

    @property
    def supported_timeframes(self) -> List[str]:
        return list(self._INTERVAL_MAP)

    def normalize_symbol(self, symbol: str) -> str:
        """
        Convert generic symbol to yfinance format.
        Example: 'NSE:RELIANCE' -> 'RELIANCE.NS', 'NSE:NIFTY' -> '^NSEI'
        """
        from platform_config import resolve_provider_symbol
        mapped = resolve_provider_symbol(symbol, self.name)
        if mapped:
            return mapped["provider_symbol"]
        if ":" in symbol:
            exchange, ticker = symbol.split(":", 1)
            exchange = exchange.upper()
            ticker = ticker.upper()
            index_symbol = self._INDEX_SYMBOLS.get((exchange, ticker))
            if index_symbol:
                return index_symbol
            if exchange == "NSE":
                return f"{ticker}.NS"
            if exchange == "BSE":
                return f"{ticker}.BO"
        bare = symbol.upper()
        index_symbol = self._INDEX_SYMBOLS.get(("NSE", bare))
        if index_symbol:
            return index_symbol
        return f"{bare}.NS" # Default to NSE

    def get_historical_candles(self, symbol: str, timeframe: str,
                               start_date, end_date) -> List[NormalizedCandle]:
        yf_symbol = self.normalize_symbol(symbol)
        canonical_tf = normalize_timeframe(timeframe)
        yf_interval = self._INTERVAL_MAP[canonical_tf]

        # Convert date strings to datetime objects
        start_dt = datetime.strptime(start_date, "%Y-%m-%d") if isinstance(start_date, str) else start_date
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") if isinstance(end_date, str) else end_date

        # Check cache first
        clean_key = yf_symbol.replace(".", "_").replace("^", "_")
        cache_path = os.path.join(self.cache_dir, f"{clean_key}_{yf_interval}.csv")
        if os.path.exists(cache_path):
            df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            # The cached index is tz-aware (IST) but callers may pass tz-naive
            # bounds — localize them to the index tz or pandas slicing raises
            # "Cannot compare tz-naive and tz-aware datetime-like objects".
            start_bound, end_bound = pd.Timestamp(start_dt), pd.Timestamp(end_dt)
            if df.index.tz is not None:
                if start_bound.tz is None:
                    start_bound = start_bound.tz_localize(df.index.tz)
                if end_bound.tz is None:
                    end_bound = end_bound.tz_localize(df.index.tz)
            # Filter by date range (inclusive of the whole end day)
            df = df.loc[start_bound:(end_bound + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))]
        else:
            # Fetch from yfinance
            df = yf.download(
                yf_symbol,
                start=start_dt.strftime("%Y-%m-%d"),
                end=end_dt.strftime("%Y-%m-%d"),
                interval=yf_interval,
                auto_adjust=False,
                progress=False,
                multi_level_index=False
            )

            if df.empty:
                raise ValueError(f"No data found for {yf_symbol} on yfinance.")

            # Normalize index to IST
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC").tz_convert(IST)
            else:
                df.index = df.index.tz_convert(IST)

            # Clean columns
            df.columns = [c.lower() for c in df.columns]
            df = df[['open', 'high', 'low', 'close', 'volume']]

            # Save to cache
            df.to_csv(cache_path)

        if canonical_tf in {"10m", "4h"}:
            rule = {"10m": "10min", "4h": "4h"}[canonical_tf]
            df = df.resample(rule, origin="start_day").agg({
                "open": "first", "high": "max", "low": "min",
                "close": "last", "volume": "sum",
            }).dropna(subset=["open", "high", "low", "close"])

        candles = candles_from_dataframe(
            df, instrument=symbol, timeframe=canonical_tf,
            provider=self.name, source_symbol=yf_symbol,
            exchange="NSE",
        )
        if not candles:
            raise ValueError(
                f"No valid candles after normalization for {yf_symbol} "
                f"({start_dt:%Y-%m-%d}..{end_dt:%Y-%m-%d})."
            )
        return candles
