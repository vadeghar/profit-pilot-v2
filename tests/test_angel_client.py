import json
from datetime import date

import pytest

from profit_pilot.data import angel_client
from profit_pilot.data.angel_client import AngelMarketDataProvider, resolve_token


@pytest.fixture(autouse=True)
def reset_module_globals():
    """_CLIENT_SINGLETON and _TOKEN_MASTER_CACHE are process-wide by design
    (one login, one token-master download, shared across every symbol/call).
    That means state leaks between tests unless reset every time."""
    angel_client._CLIENT_SINGLETON = None
    angel_client._TOKEN_MASTER_CACHE = None
    yield
    angel_client._CLIENT_SINGLETON = None
    angel_client._TOKEN_MASTER_CACHE = None


class _FakeAngel:
    def getCandleData(self, params):
        assert params["symboltoken"] == "123"
        assert params["exchange"] == "NSE"
        return {"status": True, "data": [["2026-01-01T09:15:00+00:00", 100, 102, 99, 101, 500]]}


def test_angel_token_resolution_and_state_conversion(tmp_path, monkeypatch):
    token_file = tmp_path / "angel_tokens.json"
    token_file.write_text(json.dumps([
        {"symbol": "RELIANCE", "tradingsymbol": "RELIANCE-EQ", "token": "123", "exchange": "NSE"}
    ]))
    monkeypatch.setattr(angel_client, "_get_client", lambda env=None: _FakeAngel())

    provider = AngelMarketDataProvider("RELIANCE", token_file=token_file, env={})
    rows = provider.candles(date(2026, 1, 1), date(2026, 1, 2))
    assert rows[0]["close"] == 101

    state = list(provider.states(date(2026, 1, 1), date(2026, 1, 2)))[0]
    assert state.symbol == "RELIANCE" and state.price == 101


def test_angel_uses_example_config_names(tmp_path):
    token_file = tmp_path / "angel_tokens.json"
    token_file.write_text(json.dumps([{"symbol": "RELIANCE", "token": "123", "exchange": "NSE"}]))
    provider = AngelMarketDataProvider("RELIANCE", token_file=token_file, env={
        "ANGEL_API_KEY": "key",
        "ANGEL_CLIENT_CODE": "client",
        "ANGEL_PASSWORD_OR_MPIN": "pin",
        "ANGEL_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
    })
    assert provider.env["ANGEL_TOTP_SECRET"] == "JBSWY3DPEHPK3PXP"


def test_missing_token_file_downloads_and_caches(tmp_path, monkeypatch):
    token_file = tmp_path / "nested" / "angel_tokens.json"

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return json.dumps([
                {"token": "123", "symbol": "RELIANCE", "exch_seg": "NSE", "instrumenttype": "EQ"}
            ]).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response())
    row = resolve_token("RELIANCE", "NSE", token_file=token_file)
    assert row["token"] == "123"
    assert token_file.exists()


def test_resolve_token_filters_by_exchange_for_mcx(tmp_path):
    """Same symbol name can exist on two exchanges (e.g. an equity 'GOLD'
    ETF on NSE vs the GOLD futures contract on MCX) — exchange must
    disambiguate, not just the symbol string."""
    token_file = tmp_path / "angel_tokens.json"
    token_file.write_text(json.dumps([
        {"symbol": "GOLD", "tradingsymbol": "GOLD-EQ", "token": "1",
         "exch_seg": "NSE", "instrumenttype": "EQ"},
        {"symbol": "GOLD", "tradingsymbol": "GOLD25DECFUT", "token": "2",
         "exch_seg": "MCX", "instrumenttype": "FUTCOM", "expiry": "30DEC2026"},
    ]))
    row = resolve_token("GOLD", "MCX", instrument_type="FUTCOM",
                         expiry_on_or_after=date(2026, 1, 1), token_file=token_file)
    assert row["token"] == "2"
    assert row["exch_seg"] == "MCX"


def test_resolve_token_picks_nearest_unexpired_contract(tmp_path):
    """Futures/commodities have one row per expiry in the instrument master —
    resolve_token must pick the nearest contract that hasn't expired yet
    relative to the requested date, not just the first row that matches."""
    token_file = tmp_path / "angel_tokens.json"
    token_file.write_text(json.dumps([
        {"symbol": "CRUDEOIL", "tradingsymbol": "CRUDEOIL25NOVFUT", "token": "1",
         "exch_seg": "MCX", "instrumenttype": "FUTCOM", "expiry": "19NOV2026"},
        {"symbol": "CRUDEOIL", "tradingsymbol": "CRUDEOIL25DECFUT", "token": "2",
         "exch_seg": "MCX", "instrumenttype": "FUTCOM", "expiry": "19DEC2026"},
    ]))
    row = resolve_token("CRUDEOIL", "MCX", instrument_type="FUTCOM",
                         expiry_on_or_after=date(2026, 11, 20), token_file=token_file)
    assert row["token"] == "2"  # Nov contract has already expired by the cutoff
