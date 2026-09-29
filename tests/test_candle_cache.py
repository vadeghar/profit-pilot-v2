"""Tests for the shared day-granularity candle cache (no network)."""
from datetime import date, datetime, timedelta

from market_data.cache import CandleCache, instrument_cache_key
from market_data.normalize import NormalizedCandle
from market_data.trading_days import TradingCalendar, TradingDayFetcher
from utils.timezone import IST


def make_candles(day: date, price: float = 100.0, n: int = 3):
    return [NormalizedCandle(datetime(day.year, day.month, day.day, 9, 15 + i, tzinfo=IST),
                             price, price + 1, price - 1, price, 10, "NSE:NIFTY", "5m")
           for i in range(n)]


def test_instrument_cache_key_distinguishes_symbols_and_option_dicts():
    assert instrument_cache_key("NSE:NIFTY") == "NSE_NIFTY"
    key_ce = instrument_cache_key({"stock_code": "NIFTY", "exchange_code": "NFO",
                                   "expiry_date": "2026-01-13T06:00:00.000Z",
                                   "strike_price": "25000", "right": "call"})
    key_pe = instrument_cache_key({"stock_code": "NIFTY", "exchange_code": "NFO",
                                   "expiry_date": "2026-01-13T06:00:00.000Z",
                                   "strike_price": "25000", "right": "put"})
    assert key_ce != key_pe
    assert key_ce.startswith("OPT_")


def test_miss_then_hit_round_trips_candles(tmp_path):
    cache = CandleCache(cache_dir=tmp_path)
    day = date(2026, 1, 5)
    today = date(2026, 1, 10)
    assert cache.get_day("breeze", "NSE:NIFTY", "5m", day, today=today) is None
    assert cache.misses == 1
    candles = make_candles(day)
    cache.put_day("breeze", "NSE:NIFTY", "5m", day, candles, today=today)
    got = cache.get_day("breeze", "NSE:NIFTY", "5m", day, today=today)
    assert cache.hits == 1
    assert got is not None and len(got) == len(candles)
    assert got[0].close == candles[0].close
    assert got[0].timestamp == candles[0].timestamp


def test_today_and_future_are_never_cached(tmp_path):
    cache = CandleCache(cache_dir=tmp_path)
    today = date(2026, 1, 10)
    candles = make_candles(today)
    cache.put_day("breeze", "NSE:NIFTY", "5m", today, candles, today=today)  # no-op
    assert cache.get_day("breeze", "NSE:NIFTY", "5m", today, today=today) is None
    future = today + timedelta(days=5)
    cache.put_day("breeze", "NSE:NIFTY", "5m", future, candles, today=today)
    assert cache.get_day("breeze", "NSE:NIFTY", "5m", future, today=today) is None


def test_distinct_keys_do_not_collide(tmp_path):
    cache = CandleCache(cache_dir=tmp_path)
    day, today = date(2026, 1, 5), date(2026, 1, 10)
    ce = {"stock_code": "NIFTY", "exchange_code": "NFO", "expiry_date": "2026-01-13T06:00:00.000Z",
         "strike_price": "25000", "right": "call"}
    pe = {**ce, "right": "put"}
    cache.put_day("breeze", ce, "5m", day, make_candles(day, price=200.0), today=today)
    cache.put_day("breeze", pe, "5m", day, make_candles(day, price=50.0), today=today)
    got_ce = cache.get_day("breeze", ce, "5m", day, today=today)
    got_pe = cache.get_day("breeze", pe, "5m", day, today=today)
    assert got_ce[0].close == 200.0
    assert got_pe[0].close == 50.0


def test_prune_older_than_removes_stale_entries(tmp_path):
    cache = CandleCache(cache_dir=tmp_path)
    today = date(2026, 1, 30)
    old_day, recent_day = date(2026, 1, 1), date(2026, 1, 28)
    cache.put_day("breeze", "NSE:NIFTY", "5m", old_day, make_candles(old_day), today=today)
    cache.put_day("breeze", "NSE:NIFTY", "5m", recent_day, make_candles(recent_day), today=today)
    removed = cache.prune_older_than(10, today=today)
    assert removed == 1
    assert cache.get_day("breeze", "NSE:NIFTY", "5m", old_day, today=today) is None
    assert cache.get_day("breeze", "NSE:NIFTY", "5m", recent_day, today=today) is not None


def cal():
    return TradingCalendar(["2026-01-26"], holiday_file=None)


class CountingFakeProvider:
    """Serves one flat day of candles per trading day; counts real network calls."""
    name = "breeze"

    def __init__(self):
        self.calls = 0

    def get_historical_candles(self, instrument, timeframe, start, end):
        self.calls += 1
        day = start.date()
        return make_candles(day)


def test_fetcher_skips_network_on_cache_hit(tmp_path):
    cache = CandleCache(cache_dir=tmp_path)
    provider = CountingFakeProvider()
    calendar = cal()
    start = datetime(2026, 1, 5, 9, 15, tzinfo=IST)
    end = datetime(2026, 1, 5, 15, 30, tzinfo=IST)

    f1 = TradingDayFetcher(provider, calendar, throttle_seconds=0, retries=0,
                           sleep=lambda s: None, cache=cache)
    rows1 = f1.fetch("NSE:NIFTY", "5m", start, end)
    assert provider.calls == 1
    assert cache.misses == 1 and cache.hits == 0

    f2 = TradingDayFetcher(provider, calendar, throttle_seconds=0, retries=0,
                           sleep=lambda s: None, cache=cache)
    rows2 = f2.fetch("NSE:NIFTY", "5m", start, end)
    assert provider.calls == 1  # no new network call: served from cache
    assert cache.hits == 1
    assert [c.timestamp for c in rows1] == [c.timestamp for c in rows2]


def test_fetcher_never_caches_a_partial_day_window(tmp_path):
    cache = CandleCache(cache_dir=tmp_path)
    provider = CountingFakeProvider()
    calendar = cal()
    # Only a slice of the session (not the full 9:15-15:30 window) - must not be cached.
    start = datetime(2026, 1, 5, 10, 0, tzinfo=IST)
    end = datetime(2026, 1, 5, 11, 0, tzinfo=IST)
    f = TradingDayFetcher(provider, calendar, throttle_seconds=0, retries=0,
                          sleep=lambda s: None, cache=cache)
    f.fetch("NSE:NIFTY", "5m", start, end)
    assert cache.get_day("breeze", "NSE:NIFTY", "5m", date(2026, 1, 5),
                         today=date(2026, 1, 10)) is None


def test_fetcher_without_cache_is_unaffected(tmp_path):
    provider = CountingFakeProvider()
    f = TradingDayFetcher(provider, cal(), throttle_seconds=0, retries=0, sleep=lambda s: None)
    rows = f.fetch("NSE:NIFTY", "5m", datetime(2026, 1, 5, 9, 15, tzinfo=IST),
                   datetime(2026, 1, 5, 15, 30, tzinfo=IST))
    assert provider.calls == 1
    assert len(rows) == 3
