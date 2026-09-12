from datetime import datetime, timezone

from profit_pilot.backtest.simulator import evaluate_long_exit
from profit_pilot.data.models import MarketState


def bar(o, h, l, c, minute=0):
    return MarketState(datetime(2026, 1, 1, 9, minute, tzinfo=timezone.utc), "TEST", c, {"open": o, "high": h, "low": l, "volume": 1})


def test_stop_hit():
    x = evaluate_long_exit(100, 95, 110, bar(100, 103, 94, 99), 1, 5)
    assert (x.reason, x.price) == ("stop", 95)


def test_target_hit():
    x = evaluate_long_exit(100, 95, 110, bar(100, 111, 99, 108), 1, 5)
    assert (x.reason, x.price) == ("target", 110)


def test_both_hit_stop_wins():
    x = evaluate_long_exit(100, 95, 110, bar(100, 111, 94, 105), 1, 5)
    assert (x.reason, x.price) == ("stop", 95)


def test_gap_through_stop_uses_open():
    x = evaluate_long_exit(100, 95, 110, bar(92, 98, 90, 94), 1, 5)
    assert (x.reason, x.price) == ("stop", 92)


def test_time_stop_and_timestamp():
    x = evaluate_long_exit(100, 95, 110, bar(100, 104, 98, 101, 7), 5, 5)
    assert x.reason == "time_stop"
    assert x.price == 101
    assert x.timestamp.minute == 7


def test_no_exit_before_time_stop():
    assert evaluate_long_exit(100, 95, 110, bar(100, 104, 98, 101), 4, 5) is None
