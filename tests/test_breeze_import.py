"""Breeze 1-second importer: no silent gaps, retry everything except a real rate-limit/session error, parallel days."""
import threading
import time
from datetime import date

import pytest

from market_data.tick_store import load_instruments, read_ticks
from scalp_strategies.tools import import_breeze_1s as ib

DAY = date(2026, 10, 7)
REAL_SLEEP = time.sleep   # the fixture stubs time.sleep for the back-off; a hung call still needs the real one


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(ib._time, "sleep", lambda s: None)
    monkeypatch.setattr(ib, "REQUEST_TIMEOUT", 0.3)
    ib._STOP.clear()
    yield
    ib._STOP.clear()


def bar(t, px=100.0):
    return {"datetime": f"2026-10-07 {t}", "open": px, "close": px, "volume": 10, "open_interest": 5000}


class Client:
    def __init__(self, script=None):
        self.script = list(script or [])   # responses/exceptions for the first calls, then a normal answer
        self.calls = 0
        self.lock = threading.Lock()

    def get_historical_data_v2(self, **kw):
        with self.lock:
            self.calls += 1
            step = self.script.pop(0) if self.script else None
        if isinstance(step, Exception):
            raise step
        if step == "hang":
            REAL_SLEEP(1.0)
            return {"Status": 200, "Success": []}
        if step is not None:
            return step
        return {"Status": 200, "Success": [bar(kw["from_date"][11:19])]}


def test_hung_and_failing_calls_are_retried_and_no_window_is_skipped():
    c = Client(["hang", ConnectionError("reset"), {"Status": 500, "Error": "Internal error"}])
    rows = ib._bars(c, DAY, stock_code="NIFTY", exchange_code="NSE", product_type="cash")
    assert len(rows) == 25                       # every 15-minute window from 09:15 to 15:30 is there
    assert c.calls == 28                         # 3 failures retried, then 25 good windows


def test_no_data_is_an_empty_window_not_an_error():
    rows = ib._bars(Client([{"Status": 500, "Error": "No Data Found"}]), DAY, stock_code="NIFTY")
    assert len(rows) == 24


def test_a_rate_limit_error_stops_the_whole_run():
    with pytest.raises(ib.RateLimited):
        ib._bars(Client([{"Status": 429, "Error": "Rate limit exceeded"}]), DAY, stock_code="NIFTY")
    assert ib._STOP.is_set()
    with pytest.raises(RuntimeError, match="stopped"):   # every other parallel download stops too
        ib._bars(Client(), DAY, stock_code="NIFTY")


def test_a_persistent_error_fails_loudly_after_max_attempts(monkeypatch):
    monkeypatch.setattr(ib, "MAX_ATTEMPTS", 3)
    with pytest.raises(RuntimeError, match="gave up after 3"):
        ib._bars(Client([{"Status": 500, "Error": "boom"}] * 3), DAY, stock_code="NIFTY")


def test_a_day_is_saved_only_when_every_contract_came_back(tmp_path):
    out = ib.import_day(DAY, strikes_each_side=1, client=Client(), root=tmp_path, workers=4)
    assert out["instruments"] == 8 and not out["empty"] and ib.is_done(DAY, tmp_path)
    insts, _ = load_instruments("breeze_1s", DAY, tmp_path)
    assert len({t.token for t in read_ticks("breeze_1s", DAY, tmp_path)}) == 8 == len(insts)
    other = date(2026, 10, 8)
    with pytest.raises(ib.SessionExpired):
        ib.import_day(other, strikes_each_side=1, root=tmp_path, workers=4,
                      client=Client([{"Status": 200, "Success": [bar("09:15:00")]}] + [{"Status": 401, "Error": "Session key expired"}] * 50))
    assert not ib.is_done(other, tmp_path)
