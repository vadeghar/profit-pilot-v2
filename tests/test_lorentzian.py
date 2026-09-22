"""Validation / sanity checks per spec §13.

Run: python -m pytest tests/test_lorentzian.py -q
"""
import os
import sys
import tempfile
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lorentzian_strategy.config import Settings
from lorentzian_strategy.data_loader import resample_ohlcv, load_data
from lorentzian_strategy.features import compute_features
from lorentzian_strategy.signals import generate_signals, entry_exit_signals
from lorentzian_strategy.main import run_pipeline


def make_ohlc(n=700, seed=42, timeframe="1h"):
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0005, 0.01, n)
    close = 100 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, n)))
    open_ = np.r_[close[0], close[:-1]]
    idx = pd.date_range("2024-01-01", periods=n, freq=timeframe)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


@pytest.fixture(scope="module")
def ohlc():
    return make_ohlc()


@pytest.fixture(scope="module")
def settings():
    return Settings(max_bars_back=400, neighbors_count=8, feature_count=5,
                    timeframe="1h", data_provider="yfinance")


def test_features_in_unit_interval(ohlc, settings):
    for name, series in compute_features(ohlc, settings).items():
        assert series.between(0, 1).all(), f"{name} outside [0,1]"


def test_prediction_range(ohlc, settings):
    pred = run_pipeline(settings, df=ohlc, verbose=False)["prediction"]
    valid = pred[pred.index >= pred.index[settings.max_bars_back]]
    assert valid.abs().max() <= settings.neighbors_count
    assert valid.abs().max() > 0


def test_signals_mutually_exclusive(ohlc, settings):
    e = run_pipeline(settings, df=ohlc, verbose=False)["entries"]
    assert not (e["is_new_buy_signal"] & e["is_new_sell_signal"]).any()
    assert not (e["start_long_trade"] & e["start_short_trade"]).any()


def test_trades_match_entry_signals(ohlc, settings):
    results = run_pipeline(settings, df=ohlc, verbose=False)
    e = results["entries"]
    entries_total = int(e["start_long_trade"].sum() + e["start_short_trade"].sum())
    trades = results["metrics"]["total_trades"]
    assert trades <= entries_total
    assert trades >= entries_total - 1


def test_determinism_no_lookahead(settings):
    """§13.6 — prediction at bar t is unaffected by future bars (causality).

    NOTE: the Pine-exact ANN search window is anchored to `last_bar_index`
    (``maxBarsBackIndex = last_bar_index - maxBarsBack``), so truncating the
    dataset changes the window and there is no truncation invariance by design.
    The causal property is instead verified directly: altering the prices of
    bars AFTER t must leave every prediction up to and including t identical.
    """
    df = make_ohlc(n=600)
    t_cut = 520
    full = run_pipeline(settings, df=df, verbose=False)["prediction"]

    df2 = df.copy()
    df2.loc[df2.index[t_cut + 1:], ["open", "high", "low", "close"]] = \
        df2.loc[df2.index[t_cut + 1:], ["open", "high", "low", "close"]] * 1.5
    fut = run_pipeline(settings, df=df2, verbose=False)["prediction"]

    assert np.array_equal(full.iloc[:t_cut + 1].to_numpy(),
                          fut.iloc[:t_cut + 1].to_numpy())


def test_train_labels_pine_semantics():
    """§Next Bar Classification — the labels encode the *past* 4-bar move with
    Pine's inverted ternary mapping:
        src[4] < src[0] -> short (-1); src[4] > src[0] -> long (+1).
    """
    from lorentzian_strategy.lorentzian_knn import train_labels
    src = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 99.0, 98.0, 100.0, 100.0])
    y = train_labels(src, label_lag=4)
    assert list(y[:4]) == [0, 0, 0, 0]      # no pre-history
    assert y[4] == -1                       # 104 > 100 -> rose -> short
    assert y[5] == -1                       # 105 > 101 -> rose -> short
    assert y[6] == 1                        #  99 < 102 -> fell -> long
    assert y[7] == 1                        #  98 < 103 -> fell -> long
    assert y[8] == 1                        # 100 < 104 -> fell -> long
    assert y[9] == 1                        # 100 < 105 -> fell -> long


def test_ann_matches_literal_pine_transcription():
    """§"Core ML Logic" — _ann_loop must equal a bar-by-bar literal
    transcription of the Pine source, including its quirks:

    - predictions/distances are persistent ``var`` arrays across bars,
    - ``lastDistance = -1.0`` is reset at the top of every bar,
    - the search is ``for i = 0 to sizeLoop`` (always starts at bar 0),
    - candidates require ``d >= lastDistance and i % 4 != 0``,
    - when the arrays overflow, ``lastDistance :=`` the element at
      ``round(neighborsCount*3/4)`` (half-away-from-zero) taken BEFORE the
      ``array.shift`` of both arrays,
    - ``prediction := array.sum(predictions)`` only when
      ``bar_index >= maxBarsBackIndex`` (kept otherwise).
    """
    import math
    from lorentzian_strategy.lorentzian_knn import _ann_loop, train_labels

    rng = np.random.default_rng(7)
    n, mbb, kc, fc = 80, 30, 3, 4
    feats = np.abs(rng.normal(0.5, 0.2, (n, fc)))
    src = 100 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n)))
    y = train_labels(src, label_lag=4)
    mbbi = max(0, n - 1 - mbb)

    out = _ann_loop(feats, y, fc, kc, mbb, mbbi)

    anchor = int(math.floor(kc * 3 / 4 + 0.5))   # Pine math.round (half up)
    predictions, distances = [], []
    prediction = 0.0
    expected = np.zeros(n)
    for t in range(n):
        if t < mbbi:
            expected[t] = prediction             # block skipped, var persists
            continue
        last_distance = -1.0
        size_loop = min(mbb - 1, t)
        for i in range(0, size_loop + 1):
            if i % 4 == 0:
                continue
            d = sum(math.log(1.0 + abs(feats[t, k] - feats[i, k]))
                    for k in range(fc))
            if d >= last_distance:
                last_distance = d
                predictions.append(round(y[i]))
                distances.append(d)
                if len(predictions) > kc:
                    last_distance = distances[anchor]
                    distances.pop(0)
                    predictions.pop(0)
        prediction = float(sum(predictions))
        expected[t] = prediction
    assert np.allclose(out, expected)


def test_ann_window_covers_oldest_bars():
    """Pine's ``for i = 0 to sizeLoop`` means the search window is anchored to
    the OLDEST bars (array index i == absolute bar index), not a sliding
    window.  A neighbor that only exists in the first bars of the dataset must
    therefore still influence predictions after maxBarsBackIndex."""
    from lorentzian_strategy.lorentzian_knn import _ann_loop, train_labels

    n, mbb, kc, fc = 60, 20, 3, 1
    feats = np.full((n, fc), 0.5)
    src = np.full(n, 100.0)
    # plant a distinctive neighbor at the very beginning of the dataset
    feats[2, 0] = 0.9
    y = train_labels(src, label_lag=4)
    y[2] = 1.0                                  # label = +1 (long)
    mbbi = n - 1 - mbb
    out = _ann_loop(feats, y, fc, kc, mbb, mbbi)
    # at the last bar, i=2 is inside the 0..min(mbb-1, t) search and i%4 != 0
    assert out[-1] > 0.0


def test_bars_held_pine_quirk_first_bar():
    """§7 — Pine: ``barsHeld := ta.change(signal) ? 0 : barsHeld + 1`` with
    ``var int barsHeld = 0``.  On bar 0 ``ta.change(signal)`` is na -> falsy,
    so Pine evaluates ``barsHeld + 1`` = 1."""
    idx = pd.date_range("2024-01-01", periods=6, freq="1h")
    pred = pd.Series([1, 1, 1, 1, -1, -1], index=idx)
    filt = pd.Series(True, index=idx)
    s = generate_signals(pred, filt)
    assert list(s["bars_held"]) == [1, 2, 3, 4, 0, 1]


def test_kernel_weights_favor_recent():
    """§8 — rational quadratic / gaussian weights must be applied with the most
    recent bar receiving weight (1.) exactly like KernelFunctions: for an
    increasing series the estimate must sit above the simple window mean."""
    from lorentzian_strategy.kernels import rational_quadratic, gaussian
    src = pd.Series(np.arange(120.0))
    last_rq = rational_quadratic(src, 8.0, 8.0, 25).iloc[-1]
    last_gs = gaussian(src, 8.0, 25).iloc[-1]
    assert last_rq > src.iloc[-25:].mean()
    assert last_gs > src.iloc[-25:].mean()


def test_bollinger_additive_isolation(ohlc, settings):
    base = run_pipeline(settings, df=ohlc, verbose=False)
    s2 = Settings(**{**settings.__dict__, "use_bollinger_bands": True})
    with_bb = run_pipeline(s2, df=ohlc, verbose=False)
    assert with_bb["bollinger"] is not None
    # Enabling BB must not change backtest results (§15.4/§15.8.7)
    assert with_bb["metrics"]["total_trades"] == base["metrics"]["total_trades"]
    assert with_bb["metrics"]["win_rate"] == base["metrics"]["win_rate"]
    bb = with_bb["bollinger"]
    valid = bb["upper"].dropna()
    assert (valid > bb["basis"].reindex(valid.index)).all()
    lo = bb["lower"].dropna()
    assert (lo < bb["basis"].reindex(lo.index)).all()


def test_bollinger_validation(settings):
    with pytest.raises(ValueError):
        Settings(**{**settings.__dict__, "use_bollinger_bands": True, "bollinger_length": 0}).validate()
    with pytest.raises(ValueError):
        Settings(**{**settings.__dict__, "use_bollinger_bands": True, "bollinger_mult": -1}).validate()
    with pytest.raises(ValueError):
        Settings(**{**settings.__dict__, "use_bollinger_bands": True, "bollinger_ma_type": "SMA"}).validate()


def test_config_validation():
    with pytest.raises(ValueError):
        Settings(timeframe="3h").validate()
    with pytest.raises(ValueError):
        Settings(feature_count=6).validate()
    with pytest.raises(ValueError):
        Settings(data_provider="csv", csv_path=None).validate()


def test_timeframe_resample():
    df = make_ohlc(n=48 * 30, timeframe="1h")
    d4 = resample_ohlcv(df, "4h")
    assert len(d4) == 360
    w = resample_ohlcv(df, "1wk")
    assert len(w) <= 10


def test_data_loader_fail_fast():
    # NOTE: uses tempfile instead of pytest's ``tmp_path`` fixture because
    # pytest 9.1.1's tmpdir factory fails on Windows when the given
    # ``--basetemp`` parent does not exist (mkdir(parents=False) quirk).
    df = make_ohlc(n=100)
    df.index.name = "datetime"
    with tempfile.TemporaryDirectory() as tmp:
        csv = os.path.join(tmp, "short.csv")
        df.to_csv(csv)
        s = Settings(data_provider="csv", csv_path=csv, timeframe="1h", max_bars_back=400)
        with pytest.raises(ValueError, match="Insufficient history"):
            load_data(s)


def test_metrics_report(ohlc, settings):
    m = run_pipeline(settings, df=ohlc, verbose=False)["metrics"]
    for key in ("total_trades", "total_wins", "total_losses", "win_rate",
                "win_loss_ratio", "total_early_signal_flips"):
        assert key in m
    assert m["total_wins"] + m["total_losses"] == m["total_trades"]
    assert 0.0 <= m["win_rate"] <= 1.0
