"""Option-symbol builder tests - offline, golden strings verified live (Sep-2026).

The Angel tradingsymbols below were read verbatim from the live
OpenAPIScripMaster.json and ``AAPL260923C00245000`` from the live Yahoo AAPL
option chain:

    NFO : NIFTY06OCT2621400PE, RELIANCE23NOV261140CE, IEX27OCT26117.5CE
    MCX : GOLD25SEP26153000CE, SILVER27OCT26283000PE           (OPTFUT)
    BFO : SENSEX26SEP85000CE (monthly), SENSEX26O0884600PE (weekly),
          360ONE26NOV1260CE  (master row ships an empty ``name``)

Run: python -m pytest tests/test_option_symbol.py -q
"""
import os
import sys
from datetime import date, datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_data import option_symbol as osm
from market_data.option_symbol import (
    AngelContract, _num, _to_date, _to_strike, _to_type, angel_expiries,
    angel_lookup, angel_strikes, angel_symbol, breeze_feed_params,
    breeze_params, breeze_stock_code, clear_angel_cache, get_option_symbol,
    normalize_broker, yfinance_symbol,
)

# Mirrors the live master's row shape (strike = strike x100 decimal string).
FAKE_MASTER = [
    {"token": "40637", "symbol": "NIFTY06OCT2621400PE", "name": "NIFTY",
     "expiry": "06OCT2026", "strike": "2140000.000000", "lotsize": "65",
     "instrumenttype": "OPTIDX", "exch_seg": "NFO"},
    {"token": "40638", "symbol": "NIFTY06OCT2621400CE", "name": "NIFTY",
     "expiry": "06OCT2026", "strike": "2140000.000000", "lotsize": "65",
     "instrumenttype": "OPTIDX", "exch_seg": "NFO"},
    {"token": "40640", "symbol": "NIFTY29OCT2621400PE", "name": "NIFTY",
     "expiry": "29OCT2026", "strike": "2140000.000000", "lotsize": "65",
     "instrumenttype": "OPTIDX", "exch_seg": "NFO"},
    {"token": "125981", "symbol": "RELIANCE23NOV261140CE", "name": "RELIANCE",
     "expiry": "23NOV2026", "strike": "114000.000000", "lotsize": "500",
     "instrumenttype": "OPTSTK", "exch_seg": "NFO"},
    {"token": "102595", "symbol": "IEX27OCT26117.5CE", "name": "IEX",
     "expiry": "27OCT2026", "strike": "11750.000000", "lotsize": "4350",
     "instrumenttype": "OPTSTK", "exch_seg": "NFO"},
    {"token": "556445", "symbol": "GOLD25SEP26153000CE", "name": "GOLD",
     "expiry": "25SEP2026", "strike": "15300000.000000", "lotsize": "1",
     "instrumenttype": "OPTFUT", "exch_seg": "MCX"},
    {"token": "1100042", "symbol": "SENSEX26O0884600PE", "name": "SENSEX",
     "expiry": "08OCT2026", "strike": "8460000.000000", "lotsize": "20",
     "instrumenttype": "OPTIDX", "exch_seg": "BFO"},
    {"token": "1100100", "symbol": "SENSEX26SEP85000CE", "name": "SENSEX",
     "expiry": "24SEP2026", "strike": "8500000.000000", "lotsize": "20",
     "instrumenttype": "OPTIDX", "exch_seg": "BFO"},
    {"token": "1100200", "symbol": "360ONE26NOV1260CE", "name": "",
     "expiry": "26NOV2026", "strike": "126000.000000", "lotsize": "500",
     "instrumenttype": "OPTSTK", "exch_seg": "BFO"},
    # non-option rows (futures) must never resolve as contracts
    {"token": "35000", "symbol": "NIFTY28OCT26FUT", "name": "NIFTY",
     "expiry": "28OCT2026", "strike": "-1.000000", "lotsize": "65",
     "instrumenttype": "FUTIDX", "exch_seg": "NFO"},
    {"token": "483079", "symbol": "GOLD05OCT26FUT", "name": "GOLD",
     "expiry": "05OCT2026", "strike": "-1.000000", "lotsize": "1",
     "instrumenttype": "FUTCOM", "exch_seg": "MCX"},
]


# ------------------------------------------------------------------ helpers
def test_date_parsing_formats():
    assert _to_date("2026-10-06") == date(2026, 10, 6)
    assert _to_date("06-OCT-2026") == date(2026, 10, 6)
    assert _to_date("06OCT2026") == date(2026, 10, 6)
    assert _to_date("20261006") == date(2026, 10, 6)
    assert _to_date(datetime(2026, 10, 6, 15, 30)) == date(2026, 10, 6)
    assert _to_date(date(2026, 10, 6)) == date(2026, 10, 6)
    with pytest.raises(ValueError):
        _to_date("next tuesday")


def test_option_type_and_strike_normalisation():
    assert _to_type("ce") == _to_type("Call") == _to_type("C") == "CE"
    assert _to_type("put") == _to_type("P") == "PE"
    with pytest.raises(ValueError):
        _to_type("XX")
    assert _num(25000.0) == "25000"
    assert _num(117.5) == "117.5"
    assert _to_strike("25,000") == 25000.0
    assert _to_strike(117.5) == 117.5
    with pytest.raises(ValueError):
        _to_strike("-5")


# ------------------------------------------------------------------ angel
@pytest.mark.parametrize("underlying,expiry,strike,option_type,exchange,expected", [
    ("NIFTY", "2026-10-06", 21400, "PE", None, "NIFTY06OCT2621400PE"),
    ("RELIANCE", "2026-11-23", 1140, "CE", None, "RELIANCE23NOV261140CE"),
    ("IEX", "2026-10-27", 117.5, "CE", None, "IEX27OCT26117.5CE"),
    ("MCX:GOLD", "2026-09-25", 153000, "CE", None, "GOLD25SEP26153000CE"),
    ("SILVER", "2026-10-27", 283000, "PE", "MCX", "SILVER27OCT26283000PE"),
    ("SENSEX", "2026-09-24", 85000, "CE", "BFO", "SENSEX26SEP85000CE"),
    ("SENSEX", "2026-10-08", 84600, "PE", "BFO", "SENSEX26O0884600PE"),
    ("SENSEX", "2026-11-05", 67700, "CE", "BFO", "SENSEX26N0567700CE"),
    ("BSE:SENSEX", "2026-10-15", 82200, "CE", None, "SENSEX26O1582200CE"),
    ("BANKEX", "2026-09-24", 56900, "PE", "BFO", "BANKEX26SEP56900PE"),
])
def test_angel_symbol_verified_formats(underlying, expiry, strike, option_type, exchange, expected):
    assert angel_symbol(underlying, expiry, strike, option_type, exchange) == expected


def test_bse_monthly_detection_uses_last_expiry_of_month():
    # 31-Dec-2026 is the last weekly of the month -> monthly (spelled-out) symbol
    assert angel_symbol("SENSEX", "2026-12-31", 70000, "PE", exchange="BFO") == "SENSEX26DEC70000PE"
    # 24-Dec-2026 is a mid-month weekly -> compact YY+letter+DD symbol
    assert angel_symbol("SENSEX", "2026-12-24", 70000, "PE", exchange="BFO") == "SENSEX26D2470000PE"


def test_angel_lookup_returns_token_and_contract():
    contract = angel_lookup("NIFTY", "2026-10-06", 21400, "PE", master=FAKE_MASTER)
    assert isinstance(contract, AngelContract)
    assert contract == AngelContract(token="40637", symbol="NIFTY06OCT2621400PE",
                                     exchange="NFO", lotsize=65, name="NIFTY",
                                     expiry="06OCT2026", strike=21400.0)


def test_angel_lookup_accepts_prefix_and_string_strike():
    contract = angel_lookup("NSE:NIFTY", "06OCT2026", "21,400", "call", master=FAKE_MASTER)
    assert (contract.token, contract.symbol) == ("40638", "NIFTY06OCT2621400CE")


def test_angel_lookup_mcx_bse_and_derived_name():
    mcx = angel_lookup("MCX:GOLD", "2026-09-25", 153000, "CE", master=FAKE_MASTER)
    assert (mcx.token, mcx.exchange, mcx.lotsize) == ("556445", "MCX", 1)

    bse = angel_lookup("BSE:SENSEX", "2026-10-08", 84600, "PE", master=FAKE_MASTER)
    assert (bse.token, bse.exchange, bse.symbol) == ("1100042", "BFO", "SENSEX26O0884600PE")

    # BFO OPTSTK row with an empty `name` -> underlying derived from the symbol
    derived = angel_lookup("360ONE", "2026-11-26", 1260, "CE", exchange="BFO", master=FAKE_MASTER)
    assert (derived.token, derived.name, derived.symbol) == ("1100200", "360ONE", "360ONE26NOV1260CE")


def test_angel_lookup_fractional_strike():
    contract = angel_lookup("IEX", "2026-10-27", 117.5, "CE", master=FAKE_MASTER)
    assert (contract.symbol, contract.strike, contract.lotsize) == ("IEX27OCT26117.5CE", 117.5, 4350)


def test_angel_lookup_error_lists_expected_symbol_and_expiries():
    with pytest.raises(LookupError) as excinfo:
        angel_lookup("NIFTY", "2026-10-06", 99999, "PE", master=FAKE_MASTER)
    message = str(excinfo.value)
    assert "NIFTY06OCT2699999PE" in message      # tradingsymbol hint
    assert "06-Oct-2026" in message              # available expiries
    assert "NFO" in message and "99999" in message


def test_angel_lookup_rejects_futures_rows():
    with pytest.raises(LookupError):
        angel_lookup("NIFTY", "2026-10-28", 25000, "PE", master=FAKE_MASTER)
    with pytest.raises(LookupError):
        angel_lookup("GOLD", "2026-10-05", 150000, "CE", exchange="MCX", master=FAKE_MASTER)


def test_angel_index_contains_only_option_rows():
    index = osm.build_angel_index(FAKE_MASTER)
    assert index[("BFO", "360ONE", "26NOV2026", 126000, "CE")]["token"] == "1100200"
    assert all(row["instrumenttype"] in osm.ANGEL_OPTION_TYPES for row in index.values())


def test_angel_expiries_and_strikes():
    assert angel_expiries("NIFTY", master=FAKE_MASTER) == [date(2026, 10, 6), date(2026, 10, 29)]
    assert angel_expiries("BSE:SENSEX", master=FAKE_MASTER) == [date(2026, 9, 24), date(2026, 10, 8)]
    assert angel_strikes("NIFTY", "2026-10-06", master=FAKE_MASTER) == [21400.0]
    assert angel_strikes("IEX", "2026-10-27", master=FAKE_MASTER) == [117.5]
    assert angel_strikes("NIFTY", "2026-10-28", master=FAKE_MASTER) == []


def test_global_index_built_once_and_clearable(monkeypatch):
    calls = {"n": 0}

    def fake_loader(force=False, cache_dir=None):
        calls["n"] += 1
        return FAKE_MASTER

    clear_angel_cache()
    monkeypatch.setattr(osm, "load_angel_master", fake_loader)
    try:
        first = angel_lookup("NIFTY", "2026-10-06", 21400, "PE")
        second = angel_lookup("NIFTY", "2026-10-06", 21400, "CE")
        assert calls["n"] == 1                       # master parsed once per process
        assert (first.token, second.token) == ("40637", "40638")
        clear_angel_cache()
        angel_lookup("NIFTY", "2026-10-06", 21400, "PE")
        assert calls["n"] == 2
    finally:
        clear_angel_cache()


def test_master_argument_never_downloads(monkeypatch):
    clear_angel_cache()

    def boom(force=False, cache_dir=None):
        raise AssertionError("master= must not trigger a download")

    monkeypatch.setattr(osm, "load_angel_master", boom)
    try:
        assert angel_lookup("NIFTY", "2026-10-06", 21400, "PE", master=FAKE_MASTER).token == "40637"
        assert angel_expiries("NIFTY", master=FAKE_MASTER)
    finally:
        clear_angel_cache()



# ------------------------------------------------------------------ breeze
def test_breeze_params_nfo_index():
    assert breeze_params("NIFTY", "2026-09-29", 25000, "CE") == {
        "stock_code": "NIFTY",
        "exchange_code": "NFO",
        "product_type": "options",
        "expiry_date": "2026-09-29T06:00:00.000Z",
        "right": "call",
        "strike_price": "25000",
    }


def test_breeze_golden_expired_nifty_request_format():
    assert breeze_params("NIFTY", "2024-02-29", 22100, "CE") == {
        "stock_code": "NIFTY", "exchange_code": "NFO", "product_type": "options",
        "expiry_date": "2024-02-29T06:00:00.000Z", "right": "call",
        "strike_price": "22100",
    }


def test_breeze_stock_code_resolution():
    # universe.yaml sym_breeze (NSE:RELIANCE -> RELIND), with and without prefix
    assert breeze_stock_code("NSE:RELIANCE") == "RELIND"
    assert breeze_stock_code("RELIANCE") == "RELIND"
    # index short codes (static map / universe.yaml)
    assert breeze_stock_code("BANKNIFTY") == "CNXBAN"
    assert breeze_stock_code("SENSEX") == "BSESEN"
    # MCX has no ICICI short codes - the underlying is the code
    assert breeze_stock_code("MCX:CRUDEOIL") == "CRUDEOIL"


def test_breeze_stock_code_passthrough_when_unknown(monkeypatch):
    from market_data import breeze_client
    monkeypatch.setattr(breeze_client, "breeze_scrip_short_names", lambda exchange_code="NSE": {})
    assert breeze_stock_code("SUZLON") == "SUZLON"


def test_breeze_params_bse_index_and_mcx_exchanges():
    bse = breeze_params("BSE:SENSEX", "2026-10-08", 84600, "PE")
    assert (bse["exchange_code"], bse["stock_code"], bse["right"]) == ("BFO", "BSESEN", "put")
    # a bare BSE index defaults to BFO without the prefix
    assert breeze_params("SENSEX", "2026-10-08", 84600, "PE")["exchange_code"] == "BFO"
    mcx = breeze_params("MCX:CRUDEOIL", "2026-10-19", 5800, "CE")
    assert (mcx["exchange_code"], mcx["stock_code"]) == ("MCX", "CRUDEOIL")
    assert mcx["expiry_date"] == "2026-10-19T06:00:00.000Z"


def test_breeze_params_overrides_and_string_strike():
    params = breeze_params("RELIANCE", "2026-11-23", "1,140", "CE")
    assert (params["stock_code"], params["strike_price"]) == ("RELIND", "1140")
    overridden = breeze_params("RELIANCE", "2026-11-23", 1140, "CE", stock_code="CUSTOM")
    assert overridden["stock_code"] == "CUSTOM"


def test_breeze_feed_params_subscribe_formats():
    assert breeze_feed_params("NIFTY", "2026-09-29", 25000, "CE") == {
        "exchange_code": "NFO",
        "stock_code": "NIFTY",
        "product_type": "options",
        "expiry_date": "29-Sep-2026",
        "strike_price": "25000",
        "right": "Call",
    }
    assert breeze_feed_params("SENSEX", "2026-10-08", 84600, "PE")["right"] == "Put"


# ---------------------------------------------------------------- yfinance
def test_yfinance_symbol_matches_live_yahoo_occ_format():
    # read verbatim from the live AAPL option chain
    assert yfinance_symbol("AAPL", "2026-09-23", 245, "CE") == "AAPL260923C00245000"
    assert yfinance_symbol("AAPL", "2026-09-23", 250, "call") == "AAPL260923C00250000"
    assert yfinance_symbol("AAPL", "2026-09-23", 250, "PE") == "AAPL260923P00250000"
    # strike x1000 zero-padded to 8 digits (fractional strikes keep 3 decimals)
    assert yfinance_symbol("AAPL", "2026-09-23", 17.5, "CE") == "AAPL260923C00017500"
    # exchange suffixes / cash tails are stripped from the OCC root
    assert yfinance_symbol("RELIANCE.NS", "2026-10-29", 1500, "CE") == "RELIANCE261029C01500000"
    assert yfinance_symbol("AAPL", "2026-09-23", 250, "CE", suffix=".NS") == "AAPL260923C00250000.NS"


def test_yfinance_symbol_rejects_unrepresentable_strike():
    with pytest.raises(ValueError):
        yfinance_symbol("AAPL", "2026-09-23", 100000, "CE")   # > 99999.999


# --------------------------------------------------------------- dispatcher
def test_get_option_symbol_dispatch_and_aliases():
    assert get_option_symbol("yahoo", "AAPL", "2026-10-16", 250, "CE") == "AAPL261016C00250000"
    angel = get_option_symbol("Angel One", "NIFTY", "2026-10-06", 21400, "PE", master=FAKE_MASTER)
    assert angel.token == "40637"
    breeze = get_option_symbol("ICICI Direct", "NIFTY", "2026-09-29", 25000, "CE")
    assert breeze["exchange_code"] == "NFO"


def test_get_option_symbol_unknown_broker():
    with pytest.raises(ValueError, match="Unknown broker"):
        get_option_symbol("zerodha", "NIFTY", "2026-09-29", 25000, "CE")


def test_normalize_broker_aliases():
    assert normalize_broker("angel_one") == "angel"
    assert normalize_broker(" Breeze ") == "breeze"
    assert normalize_broker("Yahoo Finance") == "yfinance"
    assert normalize_broker("smartapi") == "angel"

