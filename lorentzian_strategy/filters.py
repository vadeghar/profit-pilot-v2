"""Filters (§6). All filters are boolean pass-through when disabled."""
import numpy as np
import pandas as pd

from .features import adx, ema, rma, sma, true_range


def filter_volatility(df: pd.DataFrame, enabled: bool, min_len: int = 1, max_len: int = 10) -> pd.Series:
    """§6.1 — Pine `ml.filter_volatility(1, 10, use)` -> `ta.atr(1) > ta.atr(10)`.

    TradingView's ``ta.atr`` uses Wilder's RMA smoothing (EWM alpha = 1/length),
    NOT a simple rolling mean, so the comparison is
    ``rma(TR, 1) > rma(TR, 10)``.
    """
    if not enabled:
        return pd.Series(True, index=df.index)
    tr = true_range(df["high"], df["low"], df["close"])
    recent_atr = rma(tr, min_len)
    historical_atr = rma(tr, max_len)
    return (recent_atr > historical_atr).fillna(False)


def regime_filter(df: pd.DataFrame, enabled: bool, threshold: float = -0.1) -> pd.Series:
    """§6.2 Kalman-like slope regime detector — stateful recursive loop."""
    idx = df.index
    if not enabled:
        return pd.Series(True, index=idx)
    ohlc4 = ((df["open"] + df["high"] + df["low"] + df["close"]) / 4.0).to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    n = len(df)
    ok = np.zeros(n, dtype=bool)
    value1 = value2 = 0.0
    ema_alpha = 2.0 / (200.0 + 1.0)
    klmf = None
    slope_ema = None
    for t in range(n):
        if klmf is None:
            # §14: initialize recursive state at first valid OHLC4 bar
            klmf = ohlc4[t]
            prev_klmf = klmf
            slope_ema = 0.0
            ok[t] = True
            continue
        value1 = 0.2 * (ohlc4[t] - ohlc4[t - 1]) + 0.8 * value1
        value2 = 0.1 * (high[t] - low[t]) + 0.8 * value2
        omega = abs(value1 / value2) if value2 != 0 else 0.0
        alpha = (-omega * omega + np.sqrt(omega ** 4 + 16 * omega * omega)) / 8.0
        prev_klmf = klmf
        klmf = alpha * ohlc4[t] + (1 - alpha) * klmf
        abs_slope = abs(klmf - prev_klmf)
        slope_ema = ema_alpha * abs_slope + (1 - ema_alpha) * slope_ema
        ok[t] = ((abs_slope - slope_ema) / slope_ema >= threshold) if slope_ema != 0 else True
    return pd.Series(ok, index=idx)


def filter_adx(df: pd.DataFrame, src: pd.Series, enabled: bool,
               threshold: float = 20.0, length: int = 14) -> pd.Series:
    """§6.3 — Pine `ml.filter_adx(settings.source, 14, threshold, use)`.

    The ADX filter is computed on the **settings.source** series (passed by Pine
    as its first argument), not on the raw close.
    """
    if not enabled:
        return pd.Series(True, index=df.index)
    return (adx(df["high"], df["low"], src, length) > threshold).fillna(False)


def combined_filter(df: pd.DataFrame, s) -> pd.Series:
    """§6.4 — `filter_all = filter.volatility and filter.regime and filter.adx`."""
    from .data_loader import source_series
    vol = filter_volatility(df, s.use_volatility_filter)
    reg = regime_filter(df, s.use_regime_filter, s.regime_threshold)
    adx_ok = filter_adx(df, source_series(df, s.source), s.use_adx_filter, s.adx_threshold)
    return vol & reg & adx_ok


def trend_filters(df: pd.DataFrame, s):
    """§6.5 EMA/SMA trend booleans (True when filter disabled)."""
    close = df["close"]
    if s.use_ema_filter:
        e = ema(close, s.ema_period)
        ema_up, ema_down = close > e, close < e
    else:
        ema_up = ema_down = pd.Series(True, index=df.index)
    if s.use_sma_filter:
        m = sma(close, s.sma_period)
        sma_up, sma_down = close > m, close < m
    else:
        sma_up = sma_down = pd.Series(True, index=df.index)
    return ema_up, ema_down, sma_up, sma_down
