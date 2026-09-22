"""Nadaraya-Watson kernel regression — Rational Quadratic & Gaussian (§8)."""
import numpy as np
import pandas as pd


def rational_quadratic(src: pd.Series, h: float, r: float, x: int) -> pd.Series:
    """§8.1 — weighted average of the last `x` bars with RQ kernel weights.

    Exactly replicates KernelFunctions.rationalQuadratic(src, h, r, x):
    weight for lag ``i`` is ``(1 + i^2 / (2 * r * h^2)) ** -r`` *with ``i = 0``
    being the most recent bar*.  pandas rolling windows are chronological
    (oldest .. newest), so the kernel weights are reversed before the dot
    product to give the most recent bar the highest weight.
    """
    i = np.arange(x, dtype=float)
    weights = (1.0 + (i * i) / (2.0 * r * h * h)) ** (-r)
    weights /= weights.sum()
    return src.rolling(x).apply(lambda w: float(np.dot(w, weights[::-1])), raw=True)


def gaussian(src: pd.Series, h: float, x: int) -> pd.Series:
    """§8.2 — weighted average of the last `x` bars with Gaussian kernel weights.

    KernelFunctions.gaussian(src, h, x): weight for lag ``i`` is
    ``exp(-i^2 / (2 * h^2))`` with ``i = 0`` the most recent bar, hence the
    reversed dot product (see rational_quadratic).
    """
    i = np.arange(x, dtype=float)
    weights = np.exp(-(i * i) / (2.0 * h * h))
    weights /= weights.sum()
    return src.rolling(x).apply(lambda w: float(np.dot(w, weights[::-1])), raw=True)


def kernel_signals(src: pd.Series, s):
    """§8.3–§8.5 — compute kernel estimates and the isBullish/isBearish/alert booleans."""
    yhat1 = rational_quadratic(src, float(s.h), float(s.r), int(s.x))
    yhat2 = gaussian(src, float(s.h - s.lag), int(s.x))

    y1 = yhat1.to_numpy()
    # Pine: wasBearishRate = yhat1[2] > yhat1[1]  (values 2 and 1 bars ago)
    #   -> output[t] = y1[t-2] > y1[t-1]  ==  y1[:-2] > y1[1:-1] shifted by 2.
    was_bearish_rate = np.r_[False, False, y1[:-2] > y1[1:-1]]
    was_bullish_rate = np.r_[False, False, y1[:-2] < y1[1:-1]]
    # Pine: isBearishRate = yhat1[1] > yhat1  -> output[t] = y1[t-1] > y1[t]
    is_bearish_rate = np.r_[False, y1[:-1] > y1[1:]]
    is_bullish_rate = np.r_[False, y1[:-1] < y1[1:]]
    is_bearish_change = is_bearish_rate & was_bullish_rate
    is_bullish_change = is_bullish_rate & was_bearish_rate

    y2 = yhat2.to_numpy()
    prev_cross = np.r_[False, y2[:-1] <= y1[:-1]]
    is_bullish_cross_alert = (y2 > y1) & prev_cross
    prev_cross_b = np.r_[False, y2[:-1] >= y1[:-1]]
    is_bearish_cross_alert = (y2 < y1) & prev_cross_b
    is_bullish_smooth = pd.Series(y2 >= y1, index=src.index)
    is_bearish_smooth = pd.Series(y2 <= y1, index=src.index)

    idx = src.index
    is_bearish_rate = pd.Series(is_bearish_rate, index=idx)
    is_bullish_rate = pd.Series(is_bullish_rate, index=idx)
    is_bearish_change = pd.Series(is_bearish_change, index=idx)
    is_bullish_change = pd.Series(is_bullish_change, index=idx)

    if s.use_kernel_filter:
        is_bullish = is_bullish_smooth if s.use_kernel_smoothing else is_bullish_rate
        is_bearish = is_bearish_smooth if s.use_kernel_smoothing else is_bearish_rate
    else:
        is_bullish = pd.Series(True, index=idx)
        is_bearish = pd.Series(True, index=idx)

    alert_bullish = is_bullish_cross_alert if s.use_kernel_smoothing else is_bullish_change
    alert_bearish = is_bearish_cross_alert if s.use_kernel_smoothing else is_bearish_change
    alert_bullish = pd.Series(alert_bullish, index=idx)
    alert_bearish = pd.Series(alert_bearish, index=idx)

    return {
        "yhat1": yhat1, "yhat2": yhat2,
        "is_bullish_rate": is_bullish_rate, "is_bearish_rate": is_bearish_rate,
        "is_bullish_change": is_bullish_change, "is_bearish_change": is_bearish_change,
        "is_bullish": is_bullish, "is_bearish": is_bearish,
        "alert_bullish": alert_bullish, "alert_bearish": alert_bearish,
    }
