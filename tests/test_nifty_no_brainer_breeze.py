from datetime import date, datetime, timedelta, time

from backtest.nifty_no_brainer_breeze import (
    build_breeze_option_request,
    load_expiry_metadata,
    run_monthly_backtest,
    trace_monthly_option_requests,
)
from market_data.normalize import NormalizedCandle
from utils.timezone import IST


class MockBreeze:
    def __init__(self):
        self.calls = []

    def get_historical_candles(self, instrument, timeframe, start, end):
        self.calls.append((instrument, timeframe, start, end))
        start = start.astimezone(IST)
        if instrument == "NSE:NIFTY":
            ts = datetime(2025, 1, 31, 15, 10, tzinfo=IST)
            return [NormalizedCandle(ts, 25000, 25000, 25000, 25000, 1, instrument, timeframe)]
        if timeframe == "1m":
            ts = datetime(2025, 1, 31, 15, 16, tzinfo=IST)
            return [NormalizedCandle(ts, 100, 100, 100, 100, 1, str(instrument), timeframe)]
        ts = datetime(2025, 1, 31, 15, 30, tzinfo=IST)
        return [NormalizedCandle(ts, 101, 101, 101, 101, 1, str(instrument), timeframe)]


def test_expiry_loader_has_sources_and_lot_size_transitions():
    data = load_expiry_metadata()
    assert data["monthly_expiries"]["2025-01"]["expiry"] == "2025-01-30"
    assert data["lot_size_effective"][0]["lot_size"] == 75
    assert all(row["source_urls"] for row in data["lot_size_effective"])


def test_runner_option_request_builder_matches_golden_breeze_contract():
    assert build_breeze_option_request(date(2024, 2, 29), 22100) == {
        "stock_code": "NIFTY",
        "exchange_code": "NFO",
        "product_type": "options",
        "expiry_date": "2024-02-29T06:00:00.000Z",
        "right": "call",
        "strike_price": "22100",
    }


def test_month_flow_uses_normalized_mock_provider_without_live_calls():
    provider = MockBreeze()
    result = run_monthly_backtest(provider, start=date(2025, 1, 1), end=date(2025, 1, 31))
    assert len(result["months"]) == 1
    assert result["months"][0]["decision"] in {"TRADE", "SKIP_DEBIT", "SKIPPED_NO_ENTRY_PRICE"}
    assert all(call[1] in {"1m", "15m"} for call in provider.calls)
    option_requests = [call[0] for call in provider.calls if isinstance(call[0], dict)]
    assert option_requests
    assert {request["expiry_date"] for request in option_requests} == {"2025-02-27T06:00:00.000Z"}
    assert {request["right"] for request in option_requests} == {"call"}
    assert all(request["strike_price"].isdigit() for request in option_requests)


def test_request_trace_uses_runner_builder_and_discards_candles():
    provider = MockBreeze()
    rows = trace_monthly_option_requests(provider, date(2025, 1, 1), date(2025, 1, 31))
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "PASS"
    assert row["expiry_month_used"] == "2025-02"
    assert row["confirmed_expiry_date"] == "2025-02-27"
    assert row["runner_option_requests"]["near_buy"]["strike_price"] == "25300"
    assert row["rows_returned_per_leg"] == {"near_buy": 1, "sell": 1, "hedge": 1}
    assert row["candle_1516_present"] == {"near_buy": True, "sell": True, "hedge": True}


def test_missing_entry_leg_is_explicit_and_does_not_stop_run():
    class Missing(MockBreeze):
        def get_historical_candles(self, instrument, timeframe, start, end):
            if timeframe == "1m" and instrument != "NSE:NIFTY":
                return []
            return super().get_historical_candles(instrument, timeframe, start, end)

    result = run_monthly_backtest(Missing(), start=date(2025, 1, 1), end=date(2025, 1, 31))
    row = result["months"][0]
    assert row["decision"] == "SKIPPED_NO_ENTRY_PRICE"
    assert any(flag.startswith("MISSING_") for flag in row["flags"])
