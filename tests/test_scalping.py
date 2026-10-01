"""Tests for the tick store, SnapQuote parsing and the scalping engines (no network)."""
from datetime import date, datetime, timedelta

import pytest

from market_data.tick_recorder import parse_snapquote
from market_data.tick_store import Instrument, Tick, TickWriter, compress_day, list_days, load_instruments, read_ticks
from strategies.scalping import SCALP_STRATEGIES, OiVolumeBurst, ScalpConfig, ScalpEngine
from strategies.scalping.orderflow import PcrVelocity
from utils.timezone import IST

DAY = date(2026, 9, 29)
T0 = datetime(2026, 9, 29, 9, 15, tzinfo=IST)


def insts(atm=22550, strikes=(22500, 22550, 22600)):
    out = {"IDX": Instrument("IDX", "NIFTY", "IDX", exchange="NSE"),
           "FUT": Instrument("FUT", "NIFTYFUT", "FUT", lot=65)}
    for k in strikes:
        for kind in ("CE", "PE"):
            out[f"{k}{kind}"] = Instrument(f"{k}{kind}", f"NIFTY{k}{kind}", kind, float(k), 65)
    return out


# ------------------------------------------------------------------ tick store
def test_tick_store_round_trip_and_compression(tmp_path):
    w = TickWriter("angel", DAY, root=tmp_path)
    w.write_instruments(insts(), {"source": "angel", "expiry": "2026-10-06"})
    w.write(Tick(T0 + timedelta(seconds=2), "22550CE", 101.5, 75, 1000, 5e6, 101.4, 101.6))
    w.write(Tick(T0 + timedelta(seconds=1), "IDX", 22551.0))
    w.close(compress=True)
    ticks = list(read_ticks("angel", DAY, root=tmp_path))
    assert [t.token for t in ticks] == ["IDX", "22550CE"]  # re-sorted by timestamp
    assert ticks[1].bid == 101.4 and ticks[1].oi == 5e6 and ticks[1].ts.tzinfo is not None
    got, meta = load_instruments("angel", DAY, root=tmp_path)
    assert got["22550CE"].strike == 22550 and meta["expiry"] == "2026-10-06"
    assert list_days(tmp_path)[0]["compressed"] is True


def test_writer_reopens_a_compressed_day_and_appends(tmp_path):
    w = TickWriter("angel", DAY, root=tmp_path)
    w.write_instruments(insts(), {})
    w.write(Tick(T0, "IDX", 22550.0))
    w.close(compress=True)
    w2 = TickWriter("angel", DAY, root=tmp_path)
    w2.write(Tick(T0 + timedelta(seconds=1), "IDX", 22551.0))
    w2.close()
    assert [t.ltp for t in read_ticks("angel", DAY, root=tmp_path)] == [22550.0, 22551.0]


def test_parse_snapquote_converts_paise_and_orders_quotes():
    msg = {"token": "12345", "exchange_timestamp": int(T0.timestamp() * 1000), "last_traded_price": 10150,
           "last_traded_quantity": 75, "volume_trade_for_the_day": 900, "open_interest": 1234567,
           "average_traded_price": 10020, "total_buy_quantity": 5000.0, "total_sell_quantity": 4000.0,
           "best_5_buy_data": [{"price": 10145, "quantity": 150}], "best_5_sell_data": [{"price": 10155, "quantity": 300}]}
    t = parse_snapquote(msg)
    assert (t.ltp, t.bid, t.ask, t.atp) == (101.5, 101.45, 101.55, 100.2)
    assert (t.ltq, t.volume, t.oi, t.bid_qty, t.ask_qty) == (75, 900, 1234567.0, 150, 300)
    assert t.ts == T0
    swapped = parse_snapquote({**msg, "best_5_buy_data": [{"price": 10155, "quantity": 300}],
                               "best_5_sell_data": [{"price": 10145, "quantity": 150}]})
    assert swapped.bid < swapped.ask


# --------------------------------------------------------------------- engine
class OneShot(ScalpEngine):
    """Enters the ATM CE on the first bucket after 09:20."""
    strategy_id = "test_oneshot"

    def signal(self, ts, spot, atm):
        return ("CE", atm, "test")


def feed(engine, seconds, ce_price, ce_bid=None, ce_ask=None, start=T0, spot=22550.0):
    vol = getattr(engine, "_vol", 0)
    for s in range(seconds):
        ts = start + timedelta(seconds=s)
        px = ce_price(s) if callable(ce_price) else ce_price
        vol += 100
        engine.on_tick(Tick(ts, "IDX", spot))
        engine.on_tick(Tick(ts, "FUT", spot + 20, 65, vol, 1e7))
        bid = ce_bid(px) if ce_bid else px - 0.05
        ask = ce_ask(px) if ce_ask else px + 0.05
        engine.on_tick(Tick(ts, "22550CE", px, 65, vol, 5e6, bid, ask))
    engine._vol = vol


def test_entry_fills_at_ask_plus_slippage_and_exit_at_target_bid():
    e = OneShot(insts(), capital=100_000, config=ScalpConfig(slippage_ticks=1, sl_pct=0.10, target_pct=0.20))
    feed(e, 6 * 60, 100.0)                         # quiet until 09:21: entry after 09:20
    assert e.pos is not None and e.pos.entry == pytest.approx(100.10)  # ask 100.05 + 1 tick
    feed(e, 60, lambda s: 100 + s, start=e.last_tick_at + timedelta(seconds=1))
    t = e.trades[0]
    # target 120.12 is first reached at LTP 121: exit at its bid (120.95) minus one 0.05 tick
    assert t.reason == "TARGET" and t.exit == pytest.approx(120.90)
    assert t.net == pytest.approx(t.gross - t.charges)
    assert t.spread_cost > 0  # paid the half spread + slippage on both legs


def test_stop_loss_and_daily_loss_limit():
    e = OneShot(insts(), capital=100_000, config=ScalpConfig(max_losses=2, cooldown_min=0, time_stop_min=60))
    start = T0 + timedelta(minutes=6)
    feed(e, 6 * 60, 100.0)
    for _ in range(4):  # each position gets stopped out by a 12% drop
        if e.pos is None:
            feed(e, 30, 100.0, start=e.last_tick_at + timedelta(seconds=1))
        if e.pos:
            feed(e, 5, 88.0, start=e.last_tick_at + timedelta(seconds=1))
            feed(e, 20, 100.0, start=e.last_tick_at + timedelta(seconds=1))
    assert [t.reason for t in e.trades] == ["STOP", "STOP"]
    assert e.day_losses == 2 and not e.can_enter(e.last_tick_at)


def test_time_stop_and_square_off():
    e = OneShot(insts(), capital=100_000, config=ScalpConfig(time_stop_min=5))
    feed(e, 6 * 60, 100.0)
    feed(e, 6 * 60, 100.5, start=e.last_tick_at + timedelta(seconds=1))
    assert e.trades[0].reason == "TIME_STOP"
    late = OneShot(insts(), capital=100_000, config=ScalpConfig(entry_end="15:30", time_stop_min=600))
    feed(late, 6 * 60, 100.0)
    feed(late, 30, 100.0, start=datetime(2026, 9, 29, 14, 59, 50, tzinfo=IST))
    assert late.trades and late.trades[-1].reason == "SQUARE_OFF"


def test_capital_sizing_compounds_from_10000():
    cfg = ScalpConfig(cooldown_min=0, time_stop_min=60, target_pct=0.20)
    e = OneShot(insts(), capital=10_000, config=cfg)
    feed(e, 6 * 60, 100.0)
    assert e.pos.lots == 1  # 10,000 // (100.10 * 65 = 6,506.5)
    feed(e, 60, lambda s: 100 + s, start=e.last_tick_at + timedelta(seconds=1))  # target hit: ~+20%
    t = e.trades[0]
    assert t.reason == "TARGET" and e.balance == pytest.approx(10_000 + sum(x.net for x in e.trades)) and e.balance > 11_000
    # the next size comes from the grown balance: 20,000 would buy 3 lots of the same contract
    bigger = OneShot(insts(), capital=20_000, config=cfg)
    feed(bigger, 6 * 60, 100.0)
    assert bigger.pos.lots == 3  # 20,000 // 6,506.5


def test_signal_skipped_when_one_lot_costs_more_than_the_balance():
    e = OneShot(insts(), capital=10_000, config=ScalpConfig())
    feed(e, 6 * 60, 200.0)  # one lot = 200.10 * 65 = 13,006 > 10,000
    assert e.pos is None and e.skips and e.skips[0]["cost_per_lot"] > 10_000


def test_max_trades_per_day_and_risk_sizing():
    cfg = ScalpConfig(sizing="risk", max_trades=1, risk_pct=0.015, sl_pct=0.10, time_stop_min=1,
                      time_stop_min_gain=0.5, cooldown_min=0)
    e = OneShot(insts(), capital=100_000, config=cfg)
    feed(e, 6 * 60, 100.0)
    assert e.pos.lots == 2  # floor(1,500 / (100.10 * 10% * 65)) = 2
    feed(e, 5 * 60, 100.0, start=e.last_tick_at + timedelta(seconds=1))
    assert len(e.trades) == 1 and e.pos is None and e.day_trades == 1


# ---------------------------------------------------------------- OI burst
def burst_session(opp_unwinds=True):
    """09:15-09:45 synthetic session; at 09:41 the ATM CE gets big prints, volume, price and OI up."""
    e = OiVolumeBurst(insts(), capital=100_000)
    vols = {k: 0 for k in insts()}
    ois = {k: 5e6 for k in insts()}
    px = {k: 100.0 for k in insts() if k not in ("IDX", "FUT")}
    t = T0
    end = T0 + timedelta(minutes=27)
    while t < end:
        burst = t >= T0 + timedelta(minutes=26)
        if t >= T0 + timedelta(minutes=23) and opp_unwinds:
            ois["22550PE"] *= 0.9998                         # PE writers unwinding: ~ -3% in 3 minutes
        e.on_tick(Tick(t, "IDX", 22550.0))
        vols["FUT"] += 50
        e.on_tick(Tick(t, "FUT", 22570.0, 50, vols["FUT"], 1e7))
        for k in px:
            for sub in range(4):                            # 4 normal prints per second
                ts = t + timedelta(milliseconds=250 * sub)
                if k == "22550CE" and burst:
                    px[k] += 0.02
                    if sub == 0:
                        ois[k] += 2000                       # long buildup
                ltq = 1000 if (k == "22550CE" and burst and sub == 0 and t.second % 2 == 0) else 50
                vols[k] += ltq if ltq > 50 else 50 + (400 if k == "22550CE" and burst else 0)
                e.on_tick(Tick(ts, k, px[k], ltq, vols[k], ois[k], px[k] - 0.05, px[k] + 0.05))
        t += timedelta(seconds=1)
    return e


def test_oi_volume_burst_fires_when_all_rules_hold():
    e = burst_session(opp_unwinds=True)
    assert e.pos is not None or e.trades
    p = e.pos or e.trades[0]
    assert (p.kind, p.strike) == ("CE", 22550.0)
    assert "LTQ burst" in p.why and "opp ATM OI" in p.why


def test_oi_volume_burst_needs_opposite_side_unwinding():
    e = burst_session(opp_unwinds=False)
    assert e.pos is None and not e.trades


# ------------------------------------------------------------ S3 PCR velocity
def pcr_session(pe_unwind_per_sec):
    """25 quiet minutes, then calls are written (+500 OI/s per strike) while puts shed ``pe_unwind_per_sec``."""
    strikes = (22450, 22500, 22550, 22600, 22650)
    ii = insts(strikes=strikes)
    e = PcrVelocity(ii, capital=100_000)
    vols = {k: 0 for k in ii}
    ois = {k: 5e6 for k in ii}
    pe_px, fut = 100.0, 22570.0
    for s in range(34 * 60):
        t = T0 + timedelta(seconds=s)
        shift = s >= 25 * 60
        e.on_tick(Tick(t, "IDX", 22550.0))
        if shift:
            fut -= 0.02
            pe_px += 0.02
        vols["FUT"] += 50
        e.on_tick(Tick(t, "FUT", fut, 50, vols["FUT"], 1e7))
        for k in strikes:
            for kind in ("CE", "PE"):
                key = f"{k}{kind}"
                if shift:
                    ois[key] += 500 if kind == "CE" else -pe_unwind_per_sec
                atm_pe = key == "22550PE"
                vols[key] += 300 if (atm_pe and shift) else 50
                px = pe_px if atm_pe else 100.0
                e.on_tick(Tick(t, key, px, 50, vols[key], ois[key], px - 0.05, px + 0.05))
    return e


def test_pcr_velocity_buys_the_put_when_puts_unwind_against_call_writing():
    e = pcr_session(pe_unwind_per_sec=250)   # unwind = 50% of the build
    p = e.pos or (e.trades[0] if e.trades else None)
    assert p is not None and (p.kind, p.strike) == ("PE", 22550.0)


def test_pcr_velocity_ignores_a_token_unwind():
    e = pcr_session(pe_unwind_per_sec=25)    # unwind = 5% of the build: below MIN_UNWIND_RATIO
    assert e.pos is None and not e.trades


class FakeHub:
    def __init__(self):
        self.subs = {}

    def subscribe(self, on_tick, on_instruments=None):
        self.subs[len(self.subs)] = on_tick
        return len(self.subs) - 1

    def unsubscribe(self, sid):
        self.subs.pop(sid, None)

    def ensure_recording(self):
        return None

    def status(self):
        return {"running": False}


def test_running_sessions_resume_after_restart_but_stopped_ones_do_not(tmp_path):
    from execution.scalping_paper_trader import ScalpPaperSession, sessions_to_resume
    a = ScalpPaperSession("scalp_trap_fade", capital=50_000, overrides={"sl_pct": 0.08}, hub=FakeHub(), state_dir=tmp_path)
    b = ScalpPaperSession("scalp_pcr_velocity", capital=100_000, hub=FakeHub(), state_dir=tmp_path)
    a.start()
    b.start()
    b.stop("manual")
    resume = sessions_to_resume(tmp_path)  # process "dies" here with a still running
    assert resume == [{"strategy_id": "scalp_trap_fade", "capital": 50_000, "overrides": {"sl_pct": 0.08}}]
    a.stop("manual")


def test_every_strategy_is_registered_and_runs_on_quiet_ticks():
    for sid, cls in SCALP_STRATEGIES.items():
        e = cls(insts(), capital=100_000)
        feed(e, 30 * 60, 100.0)
        assert e.ticks > 0 and not e.trades, sid  # a flat, quiet tape must not trigger anything
