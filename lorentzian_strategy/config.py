"""Configuration for the Lorentzian Classification ML strategy (§2, §3, §15.7)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

# Pull default ticker from the global universe yaml when available.
# The universe helpers live in platform_config (a top-level `config` package
# would shadow the `config` module used internally by breeze_connect).
# Falls back to NSE:NIFTY if platform_config/universe.yaml is unavailable.
try:
    from platform_config import get_strategy_instruments
    _DEFAULT_TICKER = get_strategy_instruments("lorentzian_ml")[0]
except Exception:
    _DEFAULT_TICKER = "NSE:NIFTY"

TIMEFRAMES = ["1m", "5m", "10m", "15m", "30m", "1h", "4h", "1d", "1wk", "1mo"]
DATA_PROVIDERS = ["breeze", "yfinance", "ccxt", "csv"]
FEATURE_INDICATORS = ["RSI", "WT", "CCI", "ADX"]
SOURCES = ["open", "high", "low", "close", "hlc3", "ohlc4"]
MA_TYPES = ["WMA"]  # extensible enum (§15.3)

PANDAS_FREQ_MAP = {
    "1m": "1min", "5m": "5min", "10m": "10min", "15m": "15min", "30m": "30min",
    "1h": "1h", "4h": "4h", "1d": "1D", "1wk": "1W", "1mo": "1MS",
}


@dataclass
class Settings:
    # --- General (§2) ---
    source: str = "close"
    neighbors_count: int = 8
    max_bars_back: int = 2000
    feature_count: int = 5
    color_compression: int = 1          # display only — ignored in backtest
    show_exits: bool = False            # display only — exits are always computed
    use_dynamic_exits: bool = False
    use_worst_case: bool = False
    include_full_history: bool = False  # deprecated no-op (Pine always iterates from bar 0)
    # Feature indicator per slot and (paramA, paramB)
    feature_indicators: tuple = ("RSI", "WT", "CCI", "ADX", "RSI")
    feature_param_a: tuple = (14, 10, 20, 20, 9)
    feature_param_b: tuple = (1, 11, 1, 2, 1)

    # --- Filters (§6) ---
    use_volatility_filter: bool = True
    use_regime_filter: bool = True
    regime_threshold: float = -0.1
    use_adx_filter: bool = False
    adx_threshold: float = 20.0
    use_ema_filter: bool = False
    ema_period: int = 200
    use_sma_filter: bool = False
    sma_period: int = 200

    # --- Kernel regression (§8) ---
    use_kernel_filter: bool = True
    show_kernel_estimate: bool = True   # display only
    use_kernel_smoothing: bool = False
    h: int = 8
    r: float = 8.0
    x: int = 25
    lag: int = 2

    # --- Timeframe / data (§1.1) ---
    timeframe: str = "1h"
    data_provider: str = "breeze"  # "breeze" | "yfinance" | "ccxt" | "csv"
    ticker: str = _DEFAULT_TICKER
    csv_path: Optional[str] = None
    period: Optional[str] = None        # yfinance period override

    # --- Optional Bollinger Bands (§15, additive) ---
    use_bollinger_bands: bool = False
    bollinger_length: int = 19
    bollinger_mult: float = 2.36
    bollinger_offset: int = 0
    bollinger_ma_type: str = "WMA"
    bollinger_show_background: bool = False
    bollinger_show_upper: bool = False
    bollinger_show_lower: bool = False

    def validate(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}, got {self.source!r}")
        if not 1 <= self.neighbors_count <= 100:
            raise ValueError("neighbors_count must be in 1..100")
        if not 2 <= self.feature_count <= 5:
            raise ValueError("feature_count must be in 2..5")
        if self.max_bars_back < 10:
            raise ValueError("max_bars_back must be >= 10")
        if self.timeframe not in TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {TIMEFRAMES}, got {self.timeframe!r}")
        if self.data_provider not in DATA_PROVIDERS:
            raise ValueError(f"data_provider must be one of {DATA_PROVIDERS}")
        if self.data_provider == "csv" and not self.csv_path:
            raise ValueError("csv_path is required when data_provider == 'csv'")
        for i in range(self.feature_count):
            if self.feature_indicators[i] not in FEATURE_INDICATORS:
                raise ValueError(f"feature slot {i+1}: indicator must be one of {FEATURE_INDICATORS}")
        # §15.8 validation
        if self.use_bollinger_bands:
            if self.bollinger_length <= 0:
                raise ValueError("bollinger_length must be > 0")
            if self.bollinger_mult <= 0:
                raise ValueError("bollinger_mult must be > 0")
            if self.bollinger_ma_type not in MA_TYPES:
                raise ValueError(f"bollinger_ma_type must be one of {MA_TYPES}")
            if not isinstance(self.bollinger_offset, int):
                raise ValueError("bollinger_offset must be an integer")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["feature_indicators"] = list(self.feature_indicators)
        d["feature_param_a"] = list(self.feature_param_a)
        d["feature_param_b"] = list(self.feature_param_b)
        return d


# §3 custom data structures
@dataclass
class FeatureSeries:
    f1: float = 0.0
    f2: float = 0.0
    f3: float = 0.0
    f4: float = 0.0
    f5: float = 0.0


@dataclass
class FeatureArrays:
    f1: list = field(default_factory=list)
    f2: list = field(default_factory=list)
    f3: list = field(default_factory=list)
    f4: list = field(default_factory=list)
    f5: list = field(default_factory=list)


Direction = {"long": 1, "short": -1, "neutral": 0}
