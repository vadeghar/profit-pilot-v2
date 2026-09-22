"""Breeze (ICICI Direct) data provider tests — mocked client, no network."""
import os
import sys
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lorentzian_strategy.data_loader import (
    BREEZE_CHUNK_DAYS,
    BREEZE_INTERVAL_MAP,
    breeze_lookback_days,
    breeze_rows_to_dataframe,
    fetch_breeze,
    load_breeze_env,
    parse_breeze_ticker,
)


class FakeBreezeClient:
    """Returns 30minute candles for requested date ranges (IST wall-clock, Z suffix)."""

    def __init__(self, bars_per_chunk=400):
        self.bars_per_chunk = bars_per_chunk
        self.calls = []

    def get_historical_data_v2(self, interval, from_date, to_date,
                               stock_code, exchange_code, product_type):
        self.calls.append(dict(interval=interval, stock_code=stock_code,
                               exchange_code=exchange_code, product_type=product_type,
                               from_date=from_date, to_date=to_date))
        start = pd.Timestamp(from_date.replace("T", " ").replace(".000Z", ""))
        end = pd.Timestamp(to_date.replace("T", " ").replace(".000Z", ""))
        n = int((end - start).total_seconds() // 1800)  # 30m bars
        n = min(n, self.bars_per_chunk)
        if n <= 0:
            return {"Status": 200, "Success": []}
        idx = pd.date_range(start, periods=n, freq="30min")
        base = 100 + 7 * len(self.calls)  # distinct values per chunk
        rows = [{"datetime": f"{ts:%Y-%m-%dT%H:%M:%S.000Z}",
                 "open": float(base + i * 0.01), "high": float(base + i * 0.01 + 1),
                 "low": float(base + i * 0.01 - 1), "close": float(base + i * 0.01 + 0.5),
                 "volume": 1000 + i}
                for i, ts in enumerate(idx)]
        return {"Status": 200, "Success": rows}


def test_breeze_interval_map():
    assert BREEZE_INTERVAL_MAP["1m"] == ("1minute", None)
    assert BREEZE_INTERVAL_MAP["1d"] == ("1day", None)
    # 1h/4h fetch at 30minute and resample (Breeze has no native 60minute)
    assert BREEZE_INTERVAL_MAP["1h"] == ("30minute", "1h")
    assert BREEZE_INTERVAL_MAP["4h"] == ("30minute", "4h")


def test_parse_breeze_ticker():
    assert parse_breeze_ticker("NSE:RELIANCE") == ("RELIANCE", "NSE", "cash")
    assert parse_breeze_ticker("RELIANCE") == ("RELIANCE", "NSE", "cash")
    assert parse_breeze_ticker("NSE:NIFTY") == ("NIFTY", "NSE", "index")
    assert parse_breeze_ticker("MCX:CRUDEOIL") == ("CRUDEOIL", "MCX", "cash")


def test_breeze_lookback_days_scales():
    d_1d = breeze_lookback_days("1d", 1000)
    d_1m = breeze_lookback_days("1m", 1000)
    assert d_1d >= 1000          # 1 bar/day
    assert 2 <= d_1m <= 60       # ~375 bars/day
    assert BREEZE_CHUNK_DAYS["1day"] > BREEZE_CHUNK_DAYS["1minute"]


def test_breeze_rows_to_dataframe_parses_and_dedupes():
    rows = [
        {"datetime": "2026-09-14T09:15:00.000Z", "open": 1, "high": 2, "low": 0.5,
         "close": 1.5, "volume": 10},
        {"datetime": "2026-09-14T09:15:00.000Z", "open": 1, "high": 2, "low": 0.5,
         "close": 1.5, "volume": 10},  # duplicate
        {"datetime": "2026-09-14T09:45:00.000Z", "open": 2, "high": 3, "low": 1.5,
         "close": 2.5, "volume": 20},
    ]
    df = breeze_rows_to_dataframe(rows)
    assert len(df) == 2
    assert df.index[0] == pd.Timestamp("2026-09-14 09:15:00", tz="Asia/Kolkata")  # IST kept
    assert str(df.index.tz) in ("Asia/Kolkata", "UTC+05:30")  # tz-aware IST
    assert df.index.is_monotonic_increasing


def test_fetch_breeze_with_mocked_client(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("BREEZE_API_KEY=k\nBREEZE_API_SECRET=s\nBREEZE_SESSION_TOKEN=t\n")
    client = FakeBreezeClient(bars_per_chunk=100000)
    df = fetch_breeze("NSE:NIFTY", "1h", max_bars_back=300, env_path=str(env_file), client=client)
    # paginated until enough bars, then resampled 30m -> 1h
    assert len(df) >= 350
    assert client.calls[0]["stock_code"] == "NIFTY"
    assert client.calls[0]["product_type"] == "index"
    assert all(c["interval"] == "30minute" for c in client.calls)
    # 1h resampling: all timestamps aligned to the hour
    assert (df.index.minute == 0).all()
    assert list(df.columns) == ["open", "high", "low", "close"]


def test_fetch_breeze_insufficient_history(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("BREEZE_API_KEY=k\nBREEZE_API_SECRET=s\nBREEZE_API_KEY2=x\n"
                        "BREEZE_SESSION_TOKEN=t\n")
    client = FakeBreezeClient(bars_per_chunk=10)  # starves the request
    with pytest.raises(ValueError, match="Insufficient history from Breeze"):
        fetch_breeze("NSE:RELIANCE", "1d", max_bars_back=300,
                     env_path=str(env_file), client=client)


def test_load_breeze_env_missing_credentials(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER=value\n")
    from lorentzian_strategy.data_loader import connect_breeze
    with pytest.raises(ValueError, match="Missing Breeze credentials"):
        connect_breeze(env_path=str(env_file))


def test_breeze_platform_provider_with_mock(tmp_path):
    """market_data.BreezeHistoricalDataProvider end-to-end with a mocked client.

    Injects a stub session verification (no network) — live auth is covered
    by the credential tests; here we verify normalized output + caching.
    """
    from datetime import datetime
    from market_data.breeze_data_provider import BreezeHistoricalDataProvider
    cache_dir = tmp_path / "historical"
    client = FakeBreezeClient(bars_per_chunk=100000)
    client.get_customer_details = lambda: {"Status": 200}
    provider = BreezeHistoricalDataProvider(client=client, cache_dir=str(cache_dir))
    start = datetime(2026, 8, 3, 9, 15)
    end = datetime(2026, 8, 7, 15, 30)
    candles = provider.get_historical_candles("NSE:NIFTY", "1h", start, end)
    assert len(candles) > 0
    c = candles[0]
    assert c.instrument == "NSE:NIFTY" and c.timeframe == "1h"
    assert c.provider == "breeze" and c.source_symbol == "NIFTY"
    assert c.high >= c.low and c.close > 0
    # second call must be served from the disk cache (no new API calls)
    calls_before = len(client.calls)
    candles2 = provider.get_historical_candles("NSE:NIFTY", "1h", start, end)
    assert len(candles2) == len(candles)
    assert len(client.calls) == calls_before
    assert (cache_dir / "NSE_NIFTY_1h.json").exists()


def test_lorentzian_ml_registered_in_registry():
    """lorentzian_strategy package is registered in the platform StrategyRegistry."""
    from strategies import StrategyRegistry
    names = StrategyRegistry.list_strategies()
    assert "lorentzian_ml" in names
    assert "lorentzian" in names


def test_lorentzian_ml_adapter_signals():
    """Platform adapter replays causal history and emits registry-compatible signals."""
    import numpy as np
    import pandas as pd
    from strategies import StrategyRegistry
    from core.models import Candle, OrderSide

    rng = np.random.default_rng(123)
    n = 1200
    t = np.linspace(0, 40 * np.pi, n)
    ret = 0.0004 + 0.004 * np.sin(t) + rng.normal(0, 0.006, n)
    close = 100 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, n)))
    idx = pd.date_range("2025-06-01", periods=n, freq="1h")

    strat = StrategyRegistry.create(
        "lorentzian_ml", "lorentz_test_01",
        {"timeframe": "1h", "neighbors_count": 8, "feature_count": 5,
         "use_volatility_filter": True, "use_regime_filter": False,
         "use_kernel_filter": True, "min_history_bars": 60, "quantity": 2},
    )
    strat.initialize()
    signals = []
    ts = [t.to_pydatetime() for t in idx]
    for i in range(n):
        sig = strat.on_candle(Candle(
            instrument="NSE:NIFTY", timeframe="1h",
            open=float(close[i - 1] if i else close[0]), high=float(high[i]),
            low=float(low[i]), close=float(close[i]), volume=1000, timestamp=ts[i]))
        if sig is not None:
            signals.append(sig)

    # registry-compatible: Signal objects with expected fields
    for sig in signals:
        assert sig.strategy_id == "lorentz_test_01"
        assert sig.action in (OrderSide.BUY, OrderSide.SELL)
        assert sig.quantity == 2
        assert "reason" in sig.metadata
    # entry signals (new long/short) must appear; warmup period must be silent
    reasons = [s.metadata["reason"] for s in signals]
    assert any(r in ("lorentzian_new_long", "lorentzian_new_short") for r in reasons)
