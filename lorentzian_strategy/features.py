"""Normalized feature calculations (§4). All outputs are in [0, 1]."""
import numpy as np
import pandas as pd


def rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder's smoothing (RMA) — ewm with alpha=1/length."""
    return series.ewm(alpha=1.0 / length, adjust=False).mean()


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length).mean()


def wma(series: pd.Series, length: int) -> pd.Series:
    weights = np.arange(1, length + 1, dtype=float)
    weights /= weights.sum()
    return series.rolling(length).apply(lambda w: float(np.dot(w, weights)), raw=True)


def rsi(series: pd.Series, length: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.fillna(100.0).where(avg_loss != 0, 100.0 * (avg_gain > 0)).where(
        ~((avg_loss == 0) & (avg_gain == 0)), 50.0
    )


def _minmax_rescale(raw: pd.Series) -> pd.Series:
    """Min-max rescale to [0, 1] using a causal expanding window (§14).

    Matches the per-bar evaluation of TradingView's MLExtensions `normalize()`
    helper (min-and-max over the values available up to the current bar) while
    staying non-repainting for live use.
    """
    rmin = raw.expanding(min_periods=1).min()
    rmax = raw.expanding(min_periods=1).max()
    rng = (rmax - rmin).replace(0.0, np.nan)
    out = ((raw - rmin) / rng).clip(0.0, 1.0)
    return out.fillna(0.5)


def n_rsi(src: pd.Series, param_a: int, param_b: int) -> pd.Series:
    """§4.1 normalized RSI (MLExtensions n_rsi: EMA-smoothed RSI / 100)."""
    r = rsi(src, param_a)
    if param_b > 1:
        r = ema(r, param_b)
    return (r / 100.0).clip(0.0, 1.0)


def n_wt(hlc3: pd.Series, param_a: int, param_b: int) -> pd.Series:
    """§4.2 normalized WaveTrend (MLExtensions n_wt).

    Computed on the HLC3 series exactly like Pine's
    ``series_from(..., "WT") => ml.n_wt(_hlc3, f_paramA, f_paramB)``:

        esa = EMA(hlc3, paramA)
        d   = EMA(|hlc3 - esa|, paramA)
        ci  = (hlc3 - esa) / (0.015 * d)
        wt1 = EMA(ci, paramB); wt2 = SMA(wt1, 4)
        out = min/max-normalize(wt1 - wt2)
    """
    esa = ema(hlc3, param_a)
    d = ema((hlc3 - esa).abs(), param_a) / 0.015
    ci = ((hlc3 - esa) / d.replace(0.0, np.nan)).fillna(0.0)
    wt1 = ema(ci, param_b)
    wt2 = sma(wt1, 4)
    return _minmax_rescale((wt1 - wt2).fillna(0.0))


def n_cci(src: pd.Series, param_a: int, param_b: int) -> pd.Series:
    """§4.3 normalized CCI (MLExtensions n_cci: EMA-smoothed CCI, min-max in [0,1])."""
    tp = src
    sma_tp = sma(tp, param_a)
    mean_dev = tp.rolling(param_a).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    cci = (tp - sma_tp) / (0.015 * mean_dev.replace(0.0, np.nan))
    if param_b > 1:
        cci = ema(cci.fillna(0.0), param_b)
    return _minmax_rescale(cci.fillna(0.0))


def adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    """Standard Wilder ADX (§4.4, §6.3)."""
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    tr = true_range(high, low, close)
    atr_ = rma(tr, length)
    plus_di = 100.0 * rma(plus_dm, length) / atr_.replace(0.0, np.nan)
    minus_di = 100.0 * rma(minus_dm, length) / atr_.replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return rma(dx.fillna(0.0), length)


def n_adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    """§4.4 normalized ADX in [0, 1]."""
    return (adx(high, low, close, length) / 100.0).clip(0.0, 1.0)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1)
    return tr.max(axis=1)


def compute_features(df: pd.DataFrame, settings) -> "dict[str, pd.Series]":
    """Compute the configured feature slots, normalized to [0, 1] (§4).

    The source series for each feature exactly mirrors Pine's
    ``series_from(feature_string, close, high, low, hlc3, fA, fB)``:
    RSI/CCI are built from **close**, WaveTrend from **hlc3** and ADX from
    **high/low/close** — the ``settings.source`` input is *not* used for the
    features, it is only used by the ML labels and the kernel regression.
    """
    close = df["close"].astype(float)
    hlc3 = (df["high"] + df["low"] + df["close"]) / 3.0
    feats = {
        "RSI": lambda a, b: n_rsi(close, a, b),
        "WT": lambda a, b: n_wt(hlc3, a, b),
        "CCI": lambda a, b: n_cci(close, a, b),
        "ADX": lambda a, b: n_adx(df["high"], df["low"], df["close"], a),
    }
    out = {}
    for i in range(settings.feature_count):
        name = f"f{i + 1}"
        ind = settings.feature_indicators[i]
        out[name] = feats[ind](settings.feature_param_a[i], settings.feature_param_b[i])
    return out
