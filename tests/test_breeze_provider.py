"""Breeze (ICICI Direct) data provider tests — mocked client, no network."""
import os
import sys
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_data.breeze_client import (
    BREEZE_CHUNK_DAYS,
    BREEZE_INTERVAL_MAP,
    breeze_lookback_days,
    breeze_rows_to_dataframe,
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




def test_load_breeze_env_missing_credentials(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER=value\n")
    from market_data.breeze_client import connect_breeze
    with pytest.raises(ValueError, match="Missing Breeze credentials"):
        connect_breeze(env_path=str(env_file))


def test_resolve_breeze_stock_code_uses_scrip_master_short_name(monkeypatch):
    """Breeze historical data is keyed by the scrip master's *ShortName*.

    Passing the raw NSE ticker (RELIANCE) answers HTTP 200 with `Success: []`
    for every equity whose ShortName differs from its NSE symbol, so the ticker
    must be translated before calling get_historical_data_v2.
    """
    import io
    import sys
    import types
    from market_data import breeze_client as dl

    nse_csv = (
        'Token, "ShortName", "Series", "CompanyName", "ExchangeCode"\n'
        '1, "RELIND", "EQ", "RELIANCE INDUSTRIES", "RELIANCE"\n'
        '2, "TCS", "EQ", "TATA CONSULTANCY SERVICES", "TCS"\n'
        '3, "HDFBAN", "EQ", "HDFC BANK", "HDFCBANK"\n'
        '4, "NIFTY", "0", "NIFTY 50", "NIFTY 50"\n'
    )
    bse_csv = (
        "Token,ShortName,ScripID,ScripCode\n"
        "1,RELIND,RELIANCE,500325\n"
        "2,TCS,TCS,532540\n"
    )

    class FakeZip:
        def namelist(self):
            return ["NSEScripMaster.txt", "BSEScripMaster.txt"]

        def open(self, name):
            return io.BytesIO((nse_csv if name.startswith("NSE") else bse_csv)
                              .encode("utf-8"))

    fake_sdk = types.ModuleType("breeze_connect.breeze_connect")
    fake_sdk.zip = FakeZip()
    monkeypatch.setitem(sys.modules, "breeze_connect.breeze_connect", fake_sdk)
    monkeypatch.setattr(dl, "_BREEZE_SHORTNAME_MAPS", {})

    names = dl.breeze_scrip_short_names("NSE")
    assert names["RELIANCE"] == "RELIND"
    assert names["HDFCBANK"] == "HDFBAN"
    assert names["TCS"] == "TCS"          # ShortName == NSE symbol
    assert names["NIFTY"] == "NIFTY"      # already a ShortName (ExchangeCode is "NIFTY 50")

    assert dl.resolve_breeze_stock_code("RELIANCE", "NSE") == "RELIND"
    assert dl.resolve_breeze_stock_code("HDFCBANK", "NSE") == "HDFBAN"
    assert dl.resolve_breeze_stock_code("TCS", "NSE") == "TCS"
    assert dl.resolve_breeze_stock_code("NIFTY", "NSE") == "NIFTY"
    # already-translated codes must pass through unchanged
    assert dl.resolve_breeze_stock_code("RELIND", "NSE") == "RELIND"
    # no master entry (BANKNIFTY is an index absent from the scrip master) -> unchanged
    assert dl.resolve_breeze_stock_code("BANKNIFTY", "NSE") == "BANKNIFTY"
    # other exchanges have no master entry -> unchanged
    assert dl.resolve_breeze_stock_code("CRUDEOIL", "MCX") == "CRUDEOIL"
    # BSE resolves through ScripID the same way
    assert dl.resolve_breeze_stock_code("RELIANCE", "BSE") == "RELIND"


def test_resolve_breeze_stock_code_without_master_passes_through(monkeypatch):
    """Security master unavailable -> symbol unchanged and no exception raised."""
    import sys
    from market_data import breeze_client as dl

    monkeypatch.delitem(sys.modules, "breeze_connect.breeze_connect", raising=False)
    monkeypatch.setattr(dl, "_BREEZE_SHORTNAME_MAPS", {})

    # SDK not loaded: empty map, and the miss must NOT be cached — a later call
    # after connect_breeze() has to still be able to resolve.
    assert dl.breeze_scrip_short_names("NSE") == {}
    assert dl._BREEZE_SHORTNAME_MAPS == {}
    assert dl.resolve_breeze_stock_code("RELIANCE", "NSE") == "RELIANCE"
    assert dl.resolve_breeze_stock_code(None, "NSE") is None


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
    # The provider cache key is the normalized source symbol, without the
    # exchange prefix (the log above also reports NIFTY_1h.json).
    assert (cache_dir / "NIFTY_1h.json").exists()
