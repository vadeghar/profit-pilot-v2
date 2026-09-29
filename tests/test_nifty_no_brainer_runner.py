"""Runner tests with a fake Breeze provider (no network, no cache)."""
from datetime import date, datetime, time, timedelta

import pytest

from backtest.charges import ChargeConfig, Fill, option_charges
from backtest.nifty_no_brainer_runner import run_backtest
from market_data.normalize import NormalizedCandle
from market_data.trading_days import TradingCalendar, TradingDayFetcher
from utils.timezone import IST

HOLIDAYS = ["2026-01-26"]  # Monday


class FakeBreeze:
    """Serves bars for NIFTY spot and NIFTY calls on the given expiries."""

    name = "breeze"

    def __init__(self, expiries, prices, spot=24_830.0):
        self.expiries, self.prices, self.spot = set(expiries), prices, spot
        self.requested_days: list[date] = []

    def get_historical_candles(self, instrument, timeframe, start, end):
        self.requested_days.append(start.date())
        step = 1 if timeframe == "1m" else 5
        strike = None
        if isinstance(instrument, dict):
            if instrument["expiry_date"][:10] not in self.expiries:
                raise RuntimeError("No Breeze data returned for option")
            strike = int(instrument["strike_price"])
        rows, ts = [], start
        while ts <= end:
            if time(9, 15) <= ts.timetz().replace(tzinfo=None) <= time(15, 29):
                px = self.spot if strike is None else self.prices(strike, ts)
                if px:
                    rows.append(NormalizedCandle(ts, px, px, px, px, 10, str(instrument), timeframe))
            ts += timedelta(minutes=step)
        if not rows:
            raise RuntimeError("No Breeze data returned")
        return rows


def cal():
    return TradingCalendar(HOLIDAYS, holiday_file=None)


def run(fake, start, end, **kw):
    kw.setdefault("charges", ChargeConfig.zero())
    return run_backtest(fake, start, end, calendar=cal(), expiry_path=None,
                        fetcher=TradingDayFetcher(fake, cal(), 0, 0, lambda s: None), **kw)


def quiet(strike, ts):
    return {25_200: 100.0, 25_500: 50.0, 26_500: 0.5}.get(strike)


def test_fetcher_never_requests_weekends_or_holidays():
    fake = FakeBreeze({"2026-02-24"}, quiet)
    f = TradingDayFetcher(fake, cal(), throttle_seconds=0, retries=0, sleep=lambda s: None)
    f.fetch("NSE:NIFTY", "1m", datetime(2026, 1, 23, 15, 10, tzinfo=IST), datetime(2026, 1, 28, 15, 15, tzinfo=IST))
    assert set(fake.requested_days) == {date(2026, 1, 23), date(2026, 1, 27), date(2026, 1, 28)}
    assert {date(2026, 1, 24), date(2026, 1, 25), date(2026, 1, 26)} <= f.skipped_days


def test_jan_2026_cycle_probes_expiry_and_hits_target():
    def price(strike, ts):
        rally = 40.0 if ts.date() >= date(2026, 2, 3) else 0.0
        return {25_200: 100.0 + rally, 25_500: 50.0, 26_500: 0.5}.get(strike)
    fake = FakeBreeze({"2026-02-24"}, price)
    jan = run(fake, date(2026, 1, 1), date(2026, 3, 1))["months"][0]
    assert jan["entry_date"] == "2026-01-30"
    assert jan["expiry"] == "2026-02-24" and jan["expiry_source"] == "calendar_rule"
    assert jan["strikes"] == {"near_buy": 25_200, "sell": 25_500, "hedge": 26_500}
    assert jan["status"] == "CLOSED" and jan["exit_reason"] == "TARGET" and jan["pnl_rupees"] > 0
    assert all(d.weekday() < 5 and d != date(2026, 1, 26) for d in fake.requested_days)


def test_expiry_falls_back_to_earlier_trading_day_when_rule_date_has_no_bars():
    fake = FakeBreeze({"2026-02-23"}, quiet)
    jan = run(fake, date(2026, 1, 1), date(2026, 3, 1))["months"][0]
    assert jan["expiry"] == "2026-02-23" and jan["expiry_source"] == "calendar_rule_fallback"


def test_no_matching_expiry_is_reported_not_dropped():
    rep = run(FakeBreeze(set(), quiet), date(2026, 1, 1), date(2026, 2, 1))
    assert rep["months"][0]["decision"] == "NO_EXPIRY_CONFIRMED"
    assert rep["summary"]["skipped_months"] == 1


def test_credit_over_band_shifts_and_uses_shifted_strikes():
    table = {25_200: 40.0, 25_500: 40.0, 25_300: 30.0, 25_600: 15.0, 25_400: 20.0, 25_700: 9.0}
    fake = FakeBreeze({"2026-02-24"}, lambda k, ts: table.get(k, 0.5 if k >= 26_000 else 12.0))
    jan = run(fake, date(2026, 1, 1), date(2026, 2, 1))["months"][0]
    assert jan["shift_steps"] >= 1
    assert jan["strikes"]["near_buy"] > 25_200
    assert jan["strikes"]["sell"] - jan["strikes"]["near_buy"] == 300


def test_window_past_end_date_is_reported_open():
    rep = run(FakeBreeze({"2026-02-24"}, quiet), date(2026, 1, 1), date(2026, 2, 6))
    assert rep["months"][0]["status"] == "OPEN"
    assert rep["summary"]["closed_trades"] == 0 and rep["summary"]["open_trades"] == 1


def test_dead_session_aborts_run():
    class Dead:
        name = "breeze"
        def get_historical_candles(self, *a, **k):
            raise RuntimeError("Breeze authentication failed: Session key is expired")
    rep = run(Dead(), date(2026, 1, 1), date(2026, 6, 1))
    assert len(rep["months"]) == 1 and rep["months"][0]["status"] == "ERROR"


def test_stop_loss_is_capped_at_3_3pct_of_margin_and_records_raw_mark():
    def price(strike, ts):  # gap: long call collapses on Feb 3 -> raw loss far beyond 3%
        crash = ts.date() >= date(2026, 2, 3)
        return {25_200: 10.0 if crash else 100.0, 25_500: 50.0, 26_500: 0.5}.get(strike)
    jan = run(FakeBreeze({"2026-02-24"}, price), date(2026, 1, 1), date(2026, 3, 1))["months"][0]
    assert jan["exit_reason"] == "STOP_LOSS"
    assert jan["stop_uncapped_pnl"] < -0.033 * jan["margin"]
    assert jan["pnl_pct_margin"] == pytest.approx(-0.033)
    assert jan["stop_hard_cap_rupees"] == pytest.approx(-0.033 * jan["margin"])


def test_trade_attributes_present_even_for_skipped_month():
    fake = FakeBreeze({"2026-02-24"}, lambda k, ts: {25_200: 100.0, 25_500: 50.0, 26_500: 30.0}.get(k))
    jan = run(fake, date(2026, 1, 1), date(2026, 2, 1))["months"][0]
    assert jan["decision"] == "SKIP_DEBIT" and jan["entry_time"].endswith("15:16:00+05:30")
    assert jan["debit_on_downside_pct"] > 0.01 and jan["credit_pct"] == 0
    assert jan["stop_rupees"] == pytest.approx(-0.03 * jan["margin"])


def test_structure_margin_builds_1_2_1_payload_and_reads_span():
    from brokers.breeze_margin import MarginError, structure_margin

    class Client:
        def margin_calculator(self, legs, exchange):
            self.legs, self.exchange = legs, exchange
            return {"Status": 200, "Success": {"span_margin_required": "86350.77", "non_span_margin_required": "0"}}
    c = Client()
    q = structure_margin(c, date(2026, 10, 27), 23_400, 23_700, 24_500, 65)
    assert q.margin == pytest.approx(86_350.77) and c.exchange == "NFO"
    assert [(l["strike_price"], l["action"], l["quantity"]) for l in c.legs] == [
        ("23400", "buy", "65"), ("23700", "sell", "130"), ("24500", "buy", "65")]
    assert c.legs[0]["expiry_date"] == "2026-10-27T06:00:00.000Z"

    class Bad:
        def margin_calculator(self, legs, exchange):
            return {"Status": 500, "Error": "boom", "Success": None}
    with pytest.raises(MarginError):
        structure_margin(Bad(), date(2026, 10, 27), 23_400, 23_700, 24_500, 65)


def test_fixed_margin_drives_target_and_stop_levels():
    rep = run_backtest(FakeBreeze({"2026-02-24"}, quiet), date(2026, 1, 1), date(2026, 3, 1),
                       calendar=cal(), expiry_path=None, fixed_margin=86_350.77, margin_method="test",
                       charges=ChargeConfig.zero(),
                       fetcher=TradingDayFetcher(FakeBreeze({"2026-02-24"}, quiet), cal(), 0, 0, lambda s: None))
    jan = rep["months"][0]
    assert jan["margin"] == pytest.approx(86_350.77) and jan["margin_method"] == "test"
    assert jan["target_rupees"] == pytest.approx(0.025 * 86_350.77)


def test_gap_within_tolerance_keeps_raw_loss():
    # margin ~ (700+~0)*65 = 45.5k; long call drop of 25 pts => -1,625 = -3.57%? use 22 pts => ~ -3.14%
    def price(strike, ts):
        return {25_200: 78.0 if ts.date() >= date(2026, 2, 3) else 100.0, 25_500: 50.0, 26_500: 0.5}.get(strike)
    jan = run(FakeBreeze({"2026-02-24"}, price), date(2026, 1, 1), date(2026, 3, 1))["months"][0]
    assert jan["exit_reason"] == "STOP_LOSS" and jan["stop_uncapped_pnl"] is None
    assert -0.033 < jan["pnl_pct_margin"] < -0.03


def test_default_max_hold_is_19_days_and_early_exit_wins():
    import inspect
    assert inspect.signature(run_backtest).parameters["max_hold_days"].default == 19
    rep = run(FakeBreeze({"2026-02-24"}, lambda k, ts: {25_200: 140.0 if ts.date() >= date(2026, 2, 3) else 100.0,
                                                        25_500: 50.0, 26_500: 0.5}.get(k)),
              date(2026, 1, 1), date(2026, 3, 1))
    assert rep["params"]["max_hold_days"] == 19 and rep["months"][0]["exit_reason"] == "TARGET"


def test_charges_math_stt_rate_changes_on_2026_04_01_and_only_sell_side():
    fills = [Fill("BUY", 100.0, 65), Fill("SELL", 50.0, 130)]
    cfg = ChargeConfig()
    before = option_charges(fills, date(2026, 3, 31), cfg)
    after = option_charges(fills, date(2026, 4, 1), cfg)
    assert before["stt"] == pytest.approx(50 * 130 * 0.001) and after["stt"] == pytest.approx(50 * 130 * 0.0015)
    assert before["brokerage"] == 40.0
    turnover = 100 * 65 + 50 * 130
    assert before["exchange"] == pytest.approx(turnover * 0.0003503)
    assert before["stamp"] == pytest.approx(100 * 65 * 0.00003)
    assert before["gst"] == pytest.approx((40 + before["exchange"] + before["sebi"]) * 0.18)
    assert before["total"] == pytest.approx(sum(v for k, v in before.items() if k != "total"))


def test_charges_reduce_net_pnl_and_are_reported_per_trade():
    def price(strike, ts):
        return {25_200: 140.0 if ts.date() >= date(2026, 2, 3) else 100.0, 25_500: 50.0, 26_500: 0.5}.get(strike)
    gross = run(FakeBreeze({"2026-02-24"}, price), date(2026, 1, 1), date(2026, 3, 1))["months"][0]
    net = run(FakeBreeze({"2026-02-24"}, price), date(2026, 1, 1), date(2026, 3, 1),
              charges=ChargeConfig())["months"][0]
    assert net["charges_total"] > 150  # 6 orders x Rs20 = 120 plus GST, STT, exchange, stamp
    assert net["pnl_rupees"] == pytest.approx(gross["pnl_rupees"] - net["charges_total"])
    assert net["gross_pnl"] == pytest.approx(gross["pnl_rupees"])
    assert set(net["charges"]) == {"brokerage", "stt", "exchange", "sebi", "stamp", "gst", "total"}


def test_lots_compound_from_balance_and_deployed_capital_is_reported():
    # margin proxy = 700 * 65 = 45,500 per set. Win of ~+Rs 2,600/lot each month.
    def price(strike, ts):
        rally = 40.0 if ts.date().day in (1, 2, 3, 4, 5, 6, 9, 10) else 0.0  # early-month rally -> TARGET
        return {25_200: 100.0 + rally, 25_500: 50.0, 26_500: 0.5}.get(strike)
    fake = FakeBreeze({"2026-02-24"}, price)
    rep = run(fake, date(2026, 1, 1), date(2026, 2, 1), capital=100_000.0)
    jan = rep["months"][0]
    assert jan["lots"] == 2 and jan["deployed_capital"] == pytest.approx(2 * 45_532.5)
    assert jan["balance_before"] == 100_000.0
    assert jan["balance_after"] == pytest.approx(100_000.0 + jan["pnl_rupees"])
    assert rep["summary"]["final_balance"] == pytest.approx(jan["balance_after"])
    big = run(FakeBreeze({"2026-02-24"}, price), date(2026, 1, 1), date(2026, 2, 1), capital=400_000.0)["months"][0]
    assert big["lots"] == 8


def test_insufficient_capital_skips_with_reason():
    jan = run(FakeBreeze({"2026-02-24"}, quiet), date(2026, 1, 1), date(2026, 2, 1), capital=30_000.0)["months"][0]
    assert jan["status"] == "SKIPPED" and jan["decision"] == "INSUFFICIENT_CAPITAL"


def test_entry_and_exit_events_stream_in_order():
    events = []
    fake = FakeBreeze({"2026-02-24"}, quiet)
    run(fake, date(2026, 1, 1), date(2026, 3, 1), on_event=lambda kind, p: events.append((kind, p["trade"]["status"], p["balance"])))
    kinds = [k for k, _, _ in events]
    assert kinds[:2] == ["entry", "exit"]
    assert events[0][1] == "OPEN" and events[1][1] == "CLOSED"


def _span_xml():
    def opt(k, ra):
        return (f"<opt><cId>1</cId><o>C</o><k>{k}.00</k><p>10.0</p><d>0.3</d><ra><r>1</r>"
                + "".join(f"<a>{v}</a>" for v in ra) + "<d>0.3</d></ra></opt>")
    long_near = [0.0] * 14 + [5.0, 1.0]
    up = [-10.0, -10.0] + [0.0] * 14                  # scenario 1-2 gain for a long, i.e. loss for a short
    return ("<?xml version='1.0'?><spanFile><definitions/>"
            "<phyPf><pfId>1</pfId><pfCode>NIFTY</pfCode><phy><cId>1</cId><p>25000.0</p>"
            "<scanRate><r>1</r><priceScan>2000.0</priceScan><volScan>0.04</volScan></scanRate></phy></phyPf>"
            "<oopPf><pfId>2</pfId><pfCode>NIFTY</pfCode><undPf><pfCode>NIFTY</pfCode></undPf>"
            "<series><pe>20260227</pe>" + opt(25300, [2.0] * 16) + opt(25600, up) + opt(26500, long_near) + "</series></oopPf>"
            "<oopPf><pfId>3</pfId><pfCode>OTHER</pfCode><series><pe>20260227</pe>" + opt(1, [9.0] * 16) + "</series></oopPf>"
            "</spanFile>").encode()


def test_span_parser_keeps_only_nifty_and_reads_risk_arrays():
    import io
    from brokers.span_margin import parse_span_xml
    snap = parse_span_xml(io.BytesIO(_span_xml()))
    assert snap.underlying_price == 25000.0 and snap.price_scan == 2000.0
    assert len(snap.options) == 3 and (date(2026, 2, 27), "C", 25600.0) in snap.options


def test_span_margin_takes_worst_of_16_scenarios_with_short_sign_and_exposure():
    import io
    from brokers.span_margin import parse_span_xml, span_margin, structure_positions
    snap = parse_span_xml(io.BytesIO(_span_xml()))
    pos = structure_positions(date(2026, 2, 27), 25300, 25600, 26500, 65)
    m = span_margin(snap, pos)
    # scenario 1: long near 2*65 + short 2x(-10)*(-130)... = 130 + 1300 + 0 -> worst loss is scenario 1/2
    assert m.scanning_risk == pytest.approx(65 * 2.0 + (-130) * -10.0)
    assert m.worst_scenario in (0, 1)
    gross = span_margin(snap, pos, 0.02, "gross_short")
    assert gross.exposure == pytest.approx(0.02 * 25000.0 * 130)
    assert span_margin(snap, pos, 0.02, "net_short").exposure == 0.0   # 130 short vs 130 long units
    import pytest as _p
    with _p.raises(Exception):
        span_margin(snap, [__import__("brokers.span_margin", fromlist=["Position"]).Position(date(2026, 2, 27), "C", 99999, 65)])


def test_margin_fn_overrides_proxy_and_receives_exact_legs():
    seen = {}
    def margin_fn(entry_at, expiry, near, sell, hedge, lot):
        seen.update(entry_at=entry_at, expiry=expiry, legs=(near, sell, hedge), lot=lot)
        return 60_000.0
    jan = run(FakeBreeze({"2026-02-24"}, quiet), date(2026, 1, 1), date(2026, 3, 1), margin_fn=margin_fn,
              capital=130_000.0)["months"][0]
    assert seen["legs"] == (25_200, 25_500, 26_500) and seen["expiry"] == date(2026, 2, 24) and seen["lot"] == 65
    assert seen["entry_at"].hour == 15 and seen["entry_at"].minute == 16
    assert jan["margin_per_set"] == 60_000.0 and jan["lots"] == 2 and jan["deployed_capital"] == 120_000.0
