"""Tests for the Self-Aware Trend port: indicator maths, the trade simulator and the option re-pricing (no network)."""
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from trading_strategies.self_aware_trend import data as dataset
from trading_strategies.self_aware_trend.backtest import LOTS, Market, metrics_points, price_options
from trading_strategies.self_aware_trend.indicator import Settings, compute, pivots, rma
from trading_strategies.self_aware_trend.strategy import INTRADAY, SCRIPT, Plan, simulate


def random_bars(n=600, seed=7, start=24000.0):
    rng = np.random.default_rng(seed)
    c = start + np.cumsum(rng.normal(0, 12, n) + 6 * np.sin(np.arange(n) / 40))
    o = np.r_[start, c[:-1]]
    h, l = np.maximum(o, c) + rng.uniform(0, 8, n), np.minimum(o, c) - rng.uniform(0, 8, n)
    return pd.DataFrame({"t": pd.date_range("2026-01-01 09:15", periods=n, freq="5min"), "o": o, "h": h, "l": l, "c": c})


# ------------------------------------------------------------------ indicator
def test_rma_is_seeded_with_the_sma_then_wilder_smoothed():
    out = rma(np.array([1.0, 2.0, 3.0, 4.0, 10.0]), 3)
    assert np.isnan(out[:2]).all() and out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx((2.0 * 2 + 4.0) / 3) and out[4] == pytest.approx((out[3] * 2 + 10.0) / 3)


def test_auto_preset_follows_the_chart_timeframe():
    s = Settings()
    five, fifteen, daily = s.resolve(5), s.resolve(15), s.resolve(1440)
    assert (five.preset, five.atr_len, five.base_mult, five.er_len, five.sl_atr_mult) == ("Scalping", 10, 1.5, 14, 1.0)
    assert (fifteen.preset, fifteen.atr_len, fifteen.base_mult, fifteen.sl_atr_mult) == ("Default", 14, 2.0, 1.5)
    assert daily.preset == "Swing" and Settings(preset="Custom").resolve(5).atr_len == 13
    assert five.warmup_bars() == 110


def test_with_the_adaptive_parts_off_it_is_a_plain_supertrend_on_the_close():
    bars = random_bars()
    plain = Settings(preset="Custom", atr_len=10, base_mult=2.0, use_adaptive=False, use_tqi=False, use_eff_atr=False,
                     use_char_flip=False, mult_smooth=False)
    out = compute(bars, plain)
    h, l, c = (bars[k].to_numpy() for k in "hlc")
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.r_[c[0], c[:-1]]), np.abs(l - np.r_[c[0], c[:-1]])))
    tr[0] = h[0] - l[0]
    atr = np.zeros(len(c))
    atr[9] = tr[:10].mean()
    for i in range(10, len(c)):
        atr[i] = (atr[i - 1] * 9 + tr[i]) / 10
    lower, upper, trend = c[0], c[0], 1
    expected = [1]
    for i in range(1, len(c)):
        new_lower, new_upper = c[i] - 2 * atr[i], c[i] + 2 * atr[i]
        new_lower = max(new_lower, lower) if c[i - 1] > lower else new_lower
        new_upper = min(new_upper, upper) if c[i - 1] < upper else new_upper
        trend = 1 if (trend == -1 and c[i] > upper) else (-1 if (trend == 1 and c[i] < lower) else trend)
        lower, upper = new_lower, new_upper
        expected.append(trend)
    assert out["trend"].tolist() == expected
    assert out["atr"].to_numpy()[20:] == pytest.approx(atr[20:])
    assert set(out["flip"].unique()) == {-1, 0, 1} and not out["char_flip"].any()


def test_quality_index_is_bounded_and_high_in_a_clean_trend():
    out = compute(random_bars(), Settings())
    assert out["tqi"].between(0, 1).all() and out["er"].between(0, 1).all() and out["score"].between(0, 100).all()
    n = 200
    c = 24000 + 10.0 * np.arange(n)
    ramp = pd.DataFrame({"t": pd.date_range("2026-01-01 09:15", periods=n, freq="5min"), "o": c - 10, "h": c + 1, "l": c - 11, "c": c})
    out = compute(ramp, Settings())
    assert out["er"].iloc[-1] == pytest.approx(1.0) and out["tqi"].iloc[-1] > 0.75 and (out["trend"] == 1).all()
    assert (out["signal"] == 0).all()


def test_every_flip_is_a_band_break_or_a_flagged_character_flip_and_signals_wait_for_the_warm_up():
    out = compute(random_bars(1500, seed=3), Settings(), tf_minutes=15)
    c, lower, upper, trend = (out[k].to_numpy() for k in ("c", "lower", "upper", "trend"))
    flips = np.flatnonzero(out["flip"].to_numpy())
    assert len(flips) > 10
    for i in flips:
        broke = c[i] > upper[i - 1] if trend[i] == 1 else c[i] < lower[i - 1]
        assert broke or out["char_flip"].iloc[i]
    warm = Settings().resolve(15).warmup_bars()
    assert (out["signal"].iloc[:warm] == 0).all() and (out["signal"].iloc[warm:] == out["flip"].iloc[warm:]).all()


def test_pivots_are_known_only_after_confirmation():
    high = np.array([1, 2, 3, 9, 3, 2, 1, 2, 3], float)
    low = np.array([5, 4, 3, 1, 3, 4, 5, 4, 3], float)
    ph, pl = pivots(high, low, 3)
    assert np.isnan(ph[:6]).all() and ph[6] == 9 and np.isnan(pl[:6]).all() and pl[6] == 1 and ph[8] == 9


def test_signal_stop_sits_between_the_buffer_and_the_cap():
    out = compute(random_bars(1500, seed=11), Settings(), tf_minutes=15)       # Default preset: buffer 1.5, cap 4.0 ATR
    ok = out["atr"] > 0
    long_risk, short_risk = (out["c"] - out["sl_long"])[ok] / out["atr"][ok], (out["sl_short"] - out["c"])[ok] / out["atr"][ok]
    for risk in (long_risk, short_risk):
        assert risk.min() >= 1.5 - 1e-9 and risk.max() <= 4.0 + 1e-9
    assert (out[["tp1_r", "tp2_r", "tp3_r"]].iloc[-1] == [1.0, 2.0, 3.0]).all()
    dynamic = compute(random_bars(400), Settings(tp_mode="Dynamic"), tf_minutes=15)
    assert (dynamic["tp1_r"] <= dynamic["tp2_r"]).all() and (dynamic["tp2_r"] <= dynamic["tp3_r"]).all()
    assert dynamic["tp1_r"].min() >= 0.5 and dynamic["tp3_r"].max() <= 6.0


# ------------------------------------------------------------------ simulator
def session(rows, day=date(2026, 3, 9), first=time(9, 15)):
    """Execution bars from (open, high, low, close) rows, five minutes apart."""
    t0 = datetime.combine(day, first)
    return pd.DataFrame([{"t": t0 + timedelta(minutes=5 * i), "o": o, "h": h, "l": l, "c": c} for i, (o, h, l, c) in enumerate(rows)])


def signals(ex, at, stop_distance=10.0):
    """Indicator frame on the same bars: ``at`` maps bar index -> +1/-1; stops ``stop_distance`` from the close."""
    sig = ex.copy()
    sig["signal"] = [at.get(i, 0) for i in range(len(ex))]
    sig["trend"] = pd.Series(sig["signal"]).replace(0, np.nan).ffill().fillna(1).astype(int)
    sig["tqi"], sig["score"], sig["atr"] = 0.6, 50.0, 10.0
    sig["sl_long"], sig["sl_short"] = sig["c"] - stop_distance, sig["c"] + stop_distance
    sig["tp1_r"], sig["tp2_r"], sig["tp3_r"] = 1.0, 2.0, 3.0
    return sig


def run(rows, at, plan=INTRADAY, **kw):
    ex = session(rows, **kw)
    return simulate(signals(ex, at), ex, np.arange(len(ex)), plan)


FLAT = (100, 101, 99, 100)


def test_first_target_then_stop_books_a_third_of_r_against_two_thirds_lost():
    trades = run([FLAT, FLAT, (100, 111, 100, 105), (105, 106, 89, 92)], {1: 1})
    assert len(trades) == 1 and trades[0]["reason"] == "stop" and trades[0]["targets_hit"] == 1
    assert trades[0]["r"] == pytest.approx(1 / 3 - 2 / 3) and trades[0]["risk"] == pytest.approx(10)


def test_all_three_targets_average_two_r_and_the_stop_comes_first_inside_a_bar():
    assert run([FLAT, FLAT, (100, 131, 100, 130)], {1: 1})[0]["r"] == pytest.approx(2.0)
    both = run([FLAT, FLAT, (100, 131, 89, 130)], {1: 1})[0]                 # target and stop in one bar
    assert both["reason"] == "stop" and both["r"] == pytest.approx(-1.0)
    short = run([FLAT, FLAT, (100, 100, 69, 70)], {1: -1})[0]
    assert short["dir"] == -1 and short["r"] == pytest.approx(2.0)


def test_opposite_signal_closes_the_trade_at_the_close_and_opens_the_other_side():
    trades = run([FLAT, FLAT, (100, 104, 99, 104), (104, 105, 96, 97), (97, 97, 66, 67)], {1: 1, 3: -1})
    assert [t["reason"] for t in trades] == ["flip", "tp3"]
    assert trades[0]["r"] == pytest.approx(-0.3) and trades[1]["dir"] == -1 and trades[1]["entry"] == 97


def test_breakeven_and_runner_plans():
    rows = [FLAT, FLAT, (100, 111, 100, 108), (108, 109, 99, 101)]
    assert run(rows, {1: 1}, Plan(breakeven_after_tp1=True))[0]["r"] == pytest.approx(1 / 3)
    assert run(rows, {1: 1}, Plan(breakeven_after_tp1=True))[0]["reason"] == "breakeven"
    ride = run([FLAT, FLAT, (100, 150, 100, 150), (150, 160, 150, 158), (158, 158, 150, 151)], {1: 1, 4: -1}, Plan(targets=()))
    assert ride[0]["reason"] == "flip" and ride[0]["r"] == pytest.approx(5.1) and ride[0]["targets_hit"] == 0
    runner = run([FLAT, FLAT, (100, 150, 100, 150), (150, 150, 140, 140)], {1: 1, 3: -1}, Plan(targets=((1.0, 1 / 3),)))
    assert runner[0]["r"] == pytest.approx(1 / 3 * 1 + 2 / 3 * 4)


def test_intraday_window_and_the_1510_exit():
    late = [FLAT] * 3
    assert run(late, {1: 1}, first=time(14, 25)) == []                         # signal bar closes 14:35 > last entry
    held = run([FLAT] * 12, {1: 1}, first=time(14, 20))                        # entry 14:30, 15:05 bar closes 15:10
    assert len(held) == 1 and held[0]["reason"] == "time" and held[0]["exit_minute"] == 15 * 60 + 10 - 555
    assert run([FLAT, FLAT], {0: 1}, first=time(9, 10)) == []                  # closes 09:15 < first entry
    two_days = pd.concat([session([FLAT] * 3, first=time(14, 20)), session([FLAT] * 3, day=date(2026, 3, 10))], ignore_index=True)
    carried = simulate(signals(two_days, {1: 1}), two_days, np.arange(6), INTRADAY)
    assert carried[0]["reason"] == "time" and carried[0]["exit_date"] == "2026-03-09"


def test_script_plan_carries_overnight_fills_stops_at_the_stop_and_times_out():
    two_days = pd.concat([session([FLAT] * 3, first=time(15, 15)), session([(80, 81, 79, 80)] * 2, day=date(2026, 3, 10))], ignore_index=True)
    script = simulate(signals(two_days, {1: 1}), two_days, np.arange(5), SCRIPT)
    assert script[0]["r"] == pytest.approx(-1.0) and script[0]["exit_date"] == "2026-03-10"
    gap = simulate(signals(two_days, {1: 1}), two_days, np.arange(5), Plan(intraday=False))
    assert gap[0]["r"] == pytest.approx(-2.0)                                  # gap-aware: filled at the 80 open
    timed = run([FLAT] * 6, {1: 1}, Plan(intraday=False, timeout_bars=3))
    assert timed[0]["reason"] == "timeout" and len(timed) == 1


def test_entry_filters():
    rows = [FLAT, FLAT, (100, 131, 100, 130)]
    assert run(rows, {1: 1}, Plan(min_tqi=0.7)) == [] and run(rows, {1: 1}, Plan(min_score=60)) == []
    assert run(rows, {1: 1}, Plan(sides=(-1,))) == [] and len(run(rows, {1: 1}, Plan(min_tqi=0.5))) == 1
    ex = session(rows)
    sig = signals(ex, {1: 1})
    assert simulate(sig, ex, np.arange(3), Plan(htf_align=True), htf_trend=np.array([-1, -1, -1])) == []
    assert len(simulate(sig, ex, np.arange(3), Plan(htf_align=True), htf_trend=np.array([1, 1, 1]))) == 1
    assert simulate(sig, ex, np.arange(3), Plan(vix_min=15), vix=np.array([14.0] * 3)) == []
    assert len(simulate(sig, ex, np.arange(3), Plan(vix_min=15), vix=np.array([16.0] * 3))) == 1


def test_signal_bars_longer_than_the_execution_bars():
    ex = session([FLAT] * 3 + [(100, 111, 100, 110), (110, 121, 110, 120), (120, 131, 120, 130)])
    sig15, closes_on = dataset.resample(ex, 15)
    assert len(sig15) == 2 and closes_on.tolist() == [2, 5] and sig15["h"].tolist() == [101, 131]
    trades = simulate(signals(sig15, {0: 1}), ex, closes_on, INTRADAY)
    assert trades[0]["entry_minute"] == 15 and trades[0]["r"] == pytest.approx(2.0)
    assert [f["minute"] for f in trades[0]["fills"]] == [17.5, 22.5, 27.5]


# ------------------------------------------------------------------ options and metrics
def test_option_repricing_buys_three_lots_and_sells_them_with_the_fills():
    ex = session([(24000, 24001, 23999, 24000)] * 2 + [(24000, 24031, 24000, 24030)])
    market = Market(ex[["t", "o", "h", "l", "c"]], pd.Series({date(2026, 3, 6): 16.0}))
    trades = simulate(signals(ex, {1: 1}), ex, np.arange(3), INTRADAY)
    priced = price_options(trades, market)
    assert len(priced) == 1 and priced[0]["right"] == "CE" and priced[0]["strike"] == 24000
    assert priced[0]["expiry"] == "2026-03-10" and priced[0]["outlay"] == pytest.approx(priced[0]["buy"] * 65 * LOTS, abs=1)
    assert priced[0]["gross"] > 0 and priced[0]["net"] == pytest.approx(priced[0]["gross"] - priced[0]["charges"], abs=0.02)
    assert price_options(trades, Market(ex[["t", "o", "h", "l", "c"]], pd.Series(dtype=float))) == []
    put = price_options(simulate(signals(ex, {1: -1}), ex, np.arange(3), INTRADAY), market)[0]
    assert put["right"] == "PE" and put["reason"] == "stop" and put["net"] < 0


def test_points_metrics():
    trades = [{"r": 2.0, "points": 20, "risk": 10, "dir": 1}, {"r": -1.0, "points": -10, "risk": 10, "dir": -1},
              {"r": -1.0, "points": -10, "risk": 10, "dir": 1}]
    m = metrics_points(trades)
    assert m["win_rate"] == 33.3 and m["total_r"] == 0.0 and m["max_dd_r"] == 2.0 and m["profit_factor"] == 1.0
    assert m["long_points"] == 10 and m["short_points"] == -10 and metrics_points([]) == {"trades": 0}


def test_resample_sums_volume_and_presets_change_the_band():
    ex = session([FLAT] * 6).assign(v=[10, 20, 30, 40, 50, 60])
    bars, closes_on = dataset.resample(ex, 15)
    assert bars["v"].tolist() == [60, 150] and closes_on.tolist() == [2, 5]
    hourly, closes_on = dataset.resample(ex, 60, base_minutes=60)
    assert len(hourly) == 6 and closes_on.tolist() == list(range(6))
    bars = random_bars(900, seed=5).assign(v=1000.0)
    flips = {p: int((compute(bars, Settings(preset=p), 15)["flip"] != 0).sum()) for p in ("Scalping", "Default", "Swing")}
    assert flips["Scalping"] > flips["Default"] > flips["Swing"] > 0
