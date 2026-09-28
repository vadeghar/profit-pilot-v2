from datetime import date, datetime, timedelta

from strategies.nifty_no_brainer import CalendarEngine, NiftyNoBrainer, RiskEngine, get_entry_date, get_monthly_expiry, round_to_50, run_nifty_no_brainer_backtest, IST
from market_data.normalize import NormalizedCandle


class FakeOptionProvider:
    def __init__(self):
        self.calls = []

    def get_historical_candles(self, instrument, timeframe, start, end):
        self.calls.append(instrument)
        rows = []
        for day in (date(2026, 1, 30), date(2026, 2, 26)):
            for minute, close in ((15 * 60 + 16, 100.0), (15 * 60 + 17, 100.0)):
                ts = datetime(day.year, day.month, day.day, minute // 60, minute % 60, tzinfo=IST)
                if instrument == "NSE:NIFTY": value = 25000.0
                elif isinstance(instrument, str) and "25300" in instrument: value = 100.0
                elif isinstance(instrument, str) and "25600" in instrument: value = 95.0
                else: value = 90.0
                rows.append(NormalizedCandle(ts, value, value, value, value, 1, str(instrument), timeframe))
        return rows


def test_calendar_adjusts_holidays_backward():
    holidays = ["2026-09-25", "2026-09-24"]
    assert get_entry_date(2026, 9, holidays) == date(2026, 9, 23)
    assert get_monthly_expiry(2026, 9, ["2026-09-29"]) == date(2026, 9, 28)


def test_calendar_uses_previous_trading_day_for_2024_republic_day():
    calendar = CalendarEngine()
    assert calendar.get_entry_date(2024, 1) == date(2024, 1, 25)
    assert calendar.get_monthly_expiry(2024, 2) == date(2024, 2, 29)
    assert calendar.get_monthly_expiry(2025, 9) == date(2025, 9, 30)


def test_rounding_and_next_month_expiry():
    assert round_to_50(25125) == 25100
    assert CalendarEngine().get_monthly_expiry(2026, 10).weekday() == 1


def test_risk_and_lifecycle(monkeypatch):
    monkeypatch.setattr("strategies.nifty_no_brainer.get_option_symbol", lambda *args, **kwargs: "NIFTY-OPTION")
    strategy = NiftyNoBrainer(capital=1_000_000, lot_size=65, holidays=[])
    log = strategy.enter(datetime(2026, 9, 25, 15, 16), 25010, {"leg1": 100, "leg2": 95, "leg3": 90})
    assert log and log.sets == 21
    assert strategy.legs[0].strike == 25300
    assert strategy.mark_to_market({"leg1": 130, "leg2": 90, "leg3": 120}) == 95550
    assert strategy.should_exit(datetime(2026, 10, 9, 15, 15), {"leg1": 130, "leg2": 90, "leg3": 120}) == "Target"
    closed = strategy.close(datetime(2026, 10, 10, 15, 15), {"leg1": 130, "leg2": 90, "leg3": 120}, "Target")
    assert closed.realized_pnl == 95550


def test_debit_limit():
    risk = RiskEngine(1_000_000, 65)
    assert risk.entry_allowed(100, 1)
    assert not risk.entry_allowed(200, 1)
    assert risk.margin_per_set(10) == 46150


def test_runner_creates_one_trade_for_an_eligible_month(monkeypatch):
    monkeypatch.setattr("strategies.nifty_no_brainer.get_option_symbol", lambda broker, *args, **kwargs: "NIFTY25300CE" if args[2] == 25300 else ("NIFTY25600CE" if args[2] == 25600 else "NIFTY26600CE"))
    events = []
    result = run_nifty_no_brainer_backtest(
        FakeOptionProvider(), datetime(2026, 1, 1, tzinfo=IST),
        datetime(2026, 1, 31, tzinfo=IST), "1m", 100000, 65,
        event_callback=lambda event, payload: events.append((event, payload)), broker="breeze")
    assert result.total_trades == 1
    assert any(event == "trade" for event, _ in events)