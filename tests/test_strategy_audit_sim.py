"""Tests for the strategy-audit bar simulator (no network)."""
from datetime import datetime, timedelta

import pytest

from core.models import Candle, OrderSide, OrderType, Signal
from tools.strategy_audit import sim
from tools.strategy_audit.costs import commission, friction
from utils.timezone import IST

T0 = datetime(2024, 1, 1, 15, 30, tzinfo=IST)


def bars(closes, inst="NSE:TEST", opens=None):
    out = []
    for i, c in enumerate(closes):
        o = opens[i] if opens else c
        out.append(Candle(timestamp=T0 + timedelta(days=i), open=o, high=max(o, c) + 1, low=min(o, c) - 1,
                          close=c, volume=1000, instrument=inst, timeframe="1d"))
    return out


class Scripted:
    """Emits a scripted action per bar index: 'B', 'S', ('S', price, reason) or None."""

    def __init__(self, script):
        self.script, self.i = script, -1

    def on_candle(self, candle):
        self.i += 1
        step = self.script[self.i] if self.i < len(self.script) else None
        if step is None:
            return None
        action, price, reason = (step, None, None) if isinstance(step, str) else step
        return Signal(strategy_id="t", instrument=candle.instrument,
                      action=OrderSide.BUY if action == "B" else OrderSide.SELL, quantity=1,
                      order_type=OrderType.MARKET, price=price, metadata={"reason": reason} if reason else {})


def run(script, closes, **kw):
    kw.setdefault("window_start", T0)
    return sim.run(Scripted(script), {"NSE:TEST": bars(closes, opens=kw.pop("opens", None))},
                   capital=100_000, segment_of=lambda _i: "eq", **kw)


def test_opposite_signal_closes_then_next_opposite_opens_short():
    acct = run(["B", None, "S", "S", None, "B"], [100, 105, 110, 108, 104, 100])
    assert [t.side for t in acct.trades] == ["LONG", "SHORT"]
    long_, short = acct.trades
    assert (long_.entry_price, long_.exit_price) == (100, 110)
    assert (short.entry_price, short.exit_price) == (108, 100)


def test_same_side_signal_is_ignored():
    acct = run(["B", "B", "B", "S"], [100, 101, 102, 103])
    assert len(acct.trades) == 1 and acct.trades[0].entry_price == 100


def test_all_in_sizing_rounds_equities_down_to_five_shares():
    acct = run(["B", "S"], [333.0, 340.0])
    assert acct.trades[0].qty == 295  # floor(99,700 / 333 / 5) * 5


def test_long_stop_gapping_below_fills_at_open():
    acct = run(["B", ("S", 95.0, "stop")], [100, 90], opens=[100, 88])
    assert acct.trades[0].exit_price == 88


def test_warmup_positions_are_tracked_but_not_counted():
    acct = run(["B", None, "S", None, "B", "S"], [100, 101, 102, 103, 104, 106], window_start=T0 + timedelta(days=3))
    assert len(acct.trades) == 1
    assert acct.trades[0].entry_price == 104


def test_costs_are_itemised_and_net_is_gross_minus_costs():
    acct = run(["B", "S"], [1000.0, 1100.0])
    t = acct.trades[0]
    entry_to, exit_to = t.entry_price * t.qty, t.exit_price * t.qty
    exp_comm = (commission("eq", "BUY", entry_to, T0.date()) +
                commission("eq", "SELL", exit_to, (T0 + timedelta(days=1)).date()))
    s1, l1 = friction("eq", entry_to)
    s2, l2 = friction("eq", exit_to)
    assert t.commission == pytest.approx(exp_comm)
    assert t.spread == pytest.approx(s1 + s2) and t.slippage == pytest.approx(l1 + l2)
    assert t.net_pnl == pytest.approx(t.gross_pnl - t.commission - t.spread - t.slippage)


def test_same_day_round_trip_uses_intraday_equity_charges():
    delivery = commission("eq", "SELL", 100_000, T0.date(), intraday=False)
    intraday = commission("eq", "SELL", 100_000, T0.date(), intraday=True)
    assert intraday < delivery  # 0.025% vs 0.1% STT, no DP charge


def test_target_semantics_reverse_on_opposite_entry():
    script = [("B", None, "lorentzian_new_long"), ("S", None, "lorentzian_new_short"),
              ("B", None, "lorentzian_exit_short")]
    acct = sim.run(Scripted(script), {"NSE:TEST": bars([100, 110, 105])}, capital=100_000, window_start=T0,
                   segment_of=lambda _i: "eq", semantics="target")
    assert [t.side for t in acct.trades] == ["LONG", "SHORT"]
    assert acct.trades[1].entry_price == 110 and acct.trades[1].exit_price == 105


def test_metrics_profit_factor_and_drawdown():
    acct = run(["B", "S", "B", "S"], [100, 120, 120, 110])
    m = sim.metrics(acct, T0, T0 + timedelta(days=4))
    wins = sum(t.net_pnl for t in acct.trades if t.net_pnl > 0)
    losses = -sum(t.net_pnl for t in acct.trades if t.net_pnl <= 0)
    assert m["trades"] == 2 and m["profit_factor"] == pytest.approx(wins / losses)
    assert 0 < m["max_dd_pct"] < 1
