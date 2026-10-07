"""Self-Aware Trend System (SATS): Python port of the TradingView indicator by WillyAlgoTrader, v1.12.0.

An adaptive SuperTrend. The band is ``close -/+ multiplier x ATR`` as usual, but the multiplier moves every bar:

  * Trend Quality Index (TQI, 0..1) = weighted mix of efficiency ratio (0.35), volatility regime (0.20),
    position inside the recent high-low range (0.25) and momentum persistence (0.20). High quality narrows
    the band, low quality widens it (non-linear, ``quality_curve``).
  * the ATR itself is scaled by (0.5 + 0.5 x efficiency ratio): noisy volatility counts half.
  * asymmetric bands: the side the trend is leaning on tightens, the far side widens.
  * the multipliers are EMA-smoothed before the usual SuperTrend ratchet.

The trend flips on a close through the band, or on a "character flip": TQI falling from above
``char_flip_high`` to below ``char_flip_low`` inside a few bars while price is already moving against the trend.
A flip after the warm-up is a signal; its stop sits beyond the last confirmed pivot, kept between
``sl_atr_mult`` and ``sl_max_dist`` ATRs from the entry, and its targets are R-multiples of that risk.

Pine semantics are kept (Wilder ATR/RSI seeded with an SMA, population stdev, NaN until a window is full,
``safeDiv`` fallbacks). Not ported: the experimental auto-calibration (off by default in the script) and
everything that only draws. Source is always the close. Parity with TradingView has not been checked bar
for bar - see docs/trading/SELF_AWARE_TREND.md.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

WARMUP_FLOOR = 50
BYPASS_SCORE = 12.0
MULT_SMOOTH_ALPHA = 0.15
TQI_MULT_FLOOR, TQI_MULT_RANGE = 0.6, 0.8      # band width factor at TQI = 1, and what TQI = 0 adds to it
ASYM_TIGHTEN_MAX, ASYM_WIDEN_MAX = 0.3, 0.4

# preset -> (ATR length, base band width, efficiency window, RSI length, SL buffer)
PRESETS = {
    "Scalping": (10, 1.5, 14, 9, 1.0),
    "Default": (14, 2.0, 20, 14, 1.5),
    "Swing": (21, 2.5, 30, 21, 2.0),
    "Crypto 24/7": (14, 2.8, 20, 14, 2.5),
}


@dataclass(frozen=True)
class Settings:
    """The script's inputs, with its defaults. ``preset`` other than "Custom" overrides the five marked fields."""
    preset: str = "Auto"                  # Auto: Scalping up to 5 minutes, Default up to 4 hours, Swing above
    atr_len: int = 13                     # preset
    base_mult: float = 2.0                # preset
    use_adaptive: bool = True
    er_len: int = 20                      # preset
    adapt_strength: float = 0.5
    atr_baseline_len: int = 100
    use_tqi: bool = True
    quality_strength: float = 0.4
    quality_curve: float = 1.5
    mult_smooth: bool = True
    use_asym: bool = True
    asym_strength: float = 0.5
    use_eff_atr: bool = True
    use_char_flip: bool = True
    char_flip_min_age: int = 5
    char_flip_high: float = 0.55
    char_flip_low: float = 0.25
    w_er: float = 0.35
    w_vol: float = 0.20
    w_struct: float = 0.25
    w_mom: float = 0.20
    struct_len: int = 20
    mom_len: int = 10
    use_structure: bool = True
    pivot_len: int = 3
    use_rsi: bool = True
    rsi_len: int = 14                     # preset
    rsi_ob: float = 70.0
    rsi_os: float = 30.0
    rsi_lookback: int = 20
    use_vol: bool = True
    vol_len: int = 20
    sl_atr_mult: float = 1.5              # preset
    sl_max_dist: float = 4.0
    tp_mode: str = "Fixed"                # or "Dynamic"
    tp1_r: float = 1.0
    tp2_r: float = 2.0
    tp3_r: float = 3.0
    dyn_tqi_weight: float = 0.6
    dyn_vol_weight: float = 0.4
    dyn_min_scale: float = 0.5
    dyn_max_scale: float = 2.0
    dyn_floor_r1: float = 0.5
    dyn_ceil_r: float = 8.0

    def resolve(self, tf_minutes: int) -> "Settings":
        """The settings actually in force on a ``tf_minutes`` chart (preset applied)."""
        name = self.preset
        if name == "Auto":
            name = "Scalping" if tf_minutes <= 5 else ("Default" if tf_minutes <= 240 else "Swing")
        if name not in PRESETS:
            return replace(self, preset="Custom")
        atr_len, base_mult, er_len, rsi_len, sl_mult = PRESETS[name]
        return replace(self, preset=name, atr_len=atr_len, base_mult=base_mult, er_len=er_len, rsi_len=rsi_len,
                       sl_atr_mult=sl_mult)

    def warmup_bars(self) -> int:
        return max(WARMUP_FLOOR, max(self.atr_len, self.atr_baseline_len, self.er_len, self.rsi_len, self.rsi_lookback,
                                     self.vol_len, self.pivot_len * 2 + 1, self.mom_len, self.struct_len) + 10)


def rma(x: np.ndarray, n: int) -> np.ndarray:
    """Pine ``ta.rma``: NaN until ``n`` values exist, seeded with their mean, then (prev x (n-1) + x) / n."""
    out = np.full(len(x), np.nan)
    valid = np.flatnonzero(~np.isnan(x))
    if len(valid) < n:
        return out
    start = valid[n - 1]
    out[start] = x[valid[:n]].mean()
    for i in range(start + 1, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def _map(v, in_lo: float, in_hi: float, out_lo: float, out_hi: float) -> np.ndarray:
    """Pine ``mapClamp``: linear map of [in_lo, in_hi] onto [out_lo, out_hi], clamped; NaN maps to ``out_lo``."""
    t = np.clip(np.nan_to_num((np.asarray(v, float) - in_lo) / (in_hi - in_lo)), 0.0, 1.0)
    return out_lo + t * (out_hi - out_lo)


def _ratio(num: np.ndarray, den: np.ndarray, fallback: float) -> np.ndarray:
    """Pine ``safeDiv``."""
    ok = (den != 0) & ~np.isnan(num) & ~np.isnan(den)
    return np.where(ok, num / np.where(ok, den, 1.0), fallback)


def rsi(close: np.ndarray, n: int) -> np.ndarray:
    change = np.diff(close, prepend=np.nan)
    up, down = rma(np.where(np.isnan(change), np.nan, np.maximum(change, 0.0)), n), \
        rma(np.where(np.isnan(change), np.nan, np.maximum(-change, 0.0)), n)
    value = np.where(down == 0, 100.0, np.where(up == 0, 0.0, 100.0 - 100.0 / (1.0 + _ratio(up, down, 0.0))))
    return np.where(np.isnan(up) | np.isnan(down), np.nan, value)


def pivots(high: np.ndarray, low: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Last confirmed pivot high / low as known at each bar (a pivot is confirmed ``n`` bars after it prints)."""
    h, l = pd.Series(high), pd.Series(low)
    is_high = (h > h.shift(-n).rolling(n).max()) & (h >= h.shift(1).rolling(n).max())
    is_low = (l < l.shift(-n).rolling(n).min()) & (l <= l.shift(1).rolling(n).min())
    return (h.where(is_high).shift(n).ffill().to_numpy(), l.where(is_low).shift(n).ffill().to_numpy())


def compute(bars: pd.DataFrame, settings: Settings = Settings(), tf_minutes: int = 5) -> pd.DataFrame:
    """Run the indicator over ``bars`` (columns o, h, l, c and optionally v, oldest first).

    Returns ``bars`` plus: ``atr`` (efficiency-weighted), ``er``, ``vol_ratio``, ``tqi``, ``trend`` (+1/-1),
    ``lower`` / ``upper`` bands, ``st_line``, ``flip`` (+1/-1 on the bar the trend turns), ``char_flip``,
    ``signal`` (a flip after the warm-up), ``score`` (the 0..100 display score for the current trend side),
    ``sl_long`` / ``sl_short`` (the stop a signal on this bar would get) and ``tp1_r`` .. ``tp3_r``."""
    e = settings.resolve(tf_minutes)
    h, l, c = (bars[k].to_numpy(float) for k in ("h", "l", "c"))
    n = len(c)
    s = pd.Series

    prev_c = np.r_[np.nan, c[:-1]]
    tr = np.fmax(h - l, np.fmax(np.abs(h - prev_c), np.abs(l - prev_c)))
    raw_atr = np.nan_to_num(rma(tr, e.atr_len))
    baseline = s(raw_atr).rolling(e.atr_baseline_len).mean().to_numpy()
    baseline = np.where(np.isnan(baseline), raw_atr, baseline)
    vol_ratio = _ratio(raw_atr, baseline, 1.0)
    er = _ratio(np.abs(c - s(c).shift(e.er_len).to_numpy()),
                s(np.abs(np.diff(c, prepend=np.nan))).rolling(e.er_len).sum().to_numpy(), 0.0)
    atr = raw_atr * (0.5 + 0.5 * er) if e.use_eff_atr else raw_atr

    # Trend Quality Index
    volume = bars["v"].to_numpy(float) if "v" in bars else np.zeros(n)
    has_volume = np.maximum.accumulate(np.nan_to_num(volume) > 0)
    vol_z = _ratio(volume - s(volume).rolling(e.vol_len).mean().to_numpy(),
                   s(volume).rolling(e.vol_len).std(ddof=0).to_numpy(), 0.0)
    tqi_vol = np.where(has_volume, _map(vol_z, -1.0, 2.0, 0.0, 1.0), _map(vol_ratio, 0.6, 1.8, 0.0, 1.0))
    struct_hi, struct_lo = s(h).rolling(e.struct_len).max().to_numpy(), s(l).rolling(e.struct_len).min().to_numpy()
    tqi_struct = np.clip(np.abs(_ratio(c - struct_lo, struct_hi - struct_lo, 0.5) - 0.5) * 2.0, 0.0, 1.0)
    window_change = c - s(c).shift(e.mom_len).to_numpy()
    ups = np.nan_to_num(s((c > prev_c).astype(float)).rolling(e.mom_len).sum().to_numpy())
    downs = np.nan_to_num(s((c < prev_c).astype(float)).rolling(e.mom_len).sum().to_numpy())
    tqi_mom = np.where(window_change > 0, ups / e.mom_len, np.where(window_change < 0, downs / e.mom_len, 0.0))
    w_sum = e.w_er + e.w_vol + e.w_struct + e.w_mom
    tqi = (np.clip((np.clip(er, 0, 1) * e.w_er + tqi_vol * e.w_vol + tqi_struct * e.w_struct + tqi_mom * e.w_mom)
                   / (w_sum if w_sum > 0 else 1.0), 0.0, 1.0) if e.use_tqi else np.full(n, 0.5))

    # adaptive multipliers
    legacy = 1.0 + e.adapt_strength * (0.5 - er) if e.use_adaptive else np.ones(n)
    deviation = (1.0 - tqi) ** e.quality_curve if e.use_tqi else np.full(n, 0.5)
    q = e.quality_strength
    sym = e.base_mult * legacy * (1.0 - q + q * (TQI_MULT_FLOOR + TQI_MULT_RANGE * deviation))
    active, passive = sym, sym
    if e.use_tqi and e.use_asym:
        active = sym * (1.0 - e.asym_strength * tqi * ASYM_TIGHTEN_MAX)
        passive = sym * (1.0 + e.asym_strength * tqi * ASYM_WIDEN_MAX)
    if e.mult_smooth:
        active = s(active).ewm(alpha=MULT_SMOOTH_ALPHA, adjust=False).mean().to_numpy()
        passive = s(passive).ewm(alpha=MULT_SMOOTH_ALPHA, adjust=False).mean().to_numpy()

    # the SuperTrend itself
    lower, upper = np.empty(n), np.empty(n)
    trend, char_flip = np.ones(n, dtype=int), np.zeros(n, dtype=bool)
    window = max(e.char_flip_min_age, 3)
    tqi_high = s(tqi).rolling(window).max().to_numpy()
    trend_start = 0
    for i in range(n):
        prev = trend[i - 1] if i else 1
        lower_mult, upper_mult = (active[i], passive[i]) if prev == 1 else (passive[i], active[i])
        lower_raw, upper_raw = c[i] - lower_mult * atr[i], c[i] + upper_mult * atr[i]
        if i == 0:
            lower[i], upper[i] = lower_raw, upper_raw
            continue
        lower[i] = max(lower_raw, lower[i - 1]) if c[i - 1] > lower[i - 1] else lower_raw
        upper[i] = min(upper_raw, upper[i - 1]) if c[i - 1] < upper[i - 1] else upper_raw
        price_up, price_down = prev == -1 and c[i] > upper[i - 1], prev == 1 and c[i] < lower[i - 1]
        collapsed = (e.use_char_flip and e.use_tqi and i >= window and i - trend_start >= e.char_flip_min_age
                     and tqi_high[i] > e.char_flip_high and tqi[i] < e.char_flip_low)
        char_up = collapsed and prev == -1 and c[i] > c[i - window]
        char_down = collapsed and prev == 1 and c[i] < c[i - window]
        trend[i] = 1 if (price_up or char_up) else (-1 if (price_down or char_down) else prev)
        if trend[i] != prev:
            trend_start = i
            char_flip[i] = (char_up and not price_up) or (char_down and not price_down)
    flip = np.where(np.r_[False, trend[1:] != trend[:-1]], trend, 0)
    signal = np.where(np.arange(n) >= e.warmup_bars(), flip, 0)

    # stops and targets a signal on each bar would carry
    pivot_high, pivot_low = pivots(h, l, e.pivot_len)
    buffer, cap = e.sl_atr_mult * atr, max(e.sl_max_dist, e.sl_atr_mult) * atr
    sl_long = np.maximum(np.minimum(np.where(np.isnan(pivot_low), l, pivot_low) - buffer, c - buffer), c - cap)
    sl_short = np.minimum(np.maximum(np.where(np.isnan(pivot_high), h, pivot_high) + buffer, c + buffer), c + cap)
    fixed = sorted((e.tp1_r, e.tp2_r, e.tp3_r))
    if e.tp_mode == "Dynamic":
        w = e.dyn_tqi_weight + e.dyn_vol_weight
        raw = (np.clip(tqi, 0, 1) * e.dyn_tqi_weight + _map(vol_ratio, 0.5, 2.0, 0.0, 1.0) * e.dyn_vol_weight) / (w if w > 0 else 1.0)
        scale = e.dyn_min_scale + raw * (e.dyn_max_scale - e.dyn_min_scale)
        floors = [min(e.dyn_floor_r1 * r / max(fixed[0], 0.01), e.dyn_ceil_r) for r in fixed]
        tps = np.sort(np.vstack([np.clip(r * scale, lo, e.dyn_ceil_r) for r, lo in zip(fixed, floors)]), axis=0)
    else:
        tps = np.vstack([np.full(n, r) for r in fixed])

    # 0..100 display score for the side of the current trend (the script only shows it on signal bars)
    buy = trend == 1
    close_3 = s(c).shift(3).to_numpy()
    rsi_val = np.nan_to_num(rsi(c, e.rsi_len), nan=50.0)
    rsi_low, rsi_high = s(rsi_val).rolling(e.rsi_lookback).min().to_numpy(), s(rsi_val).rolling(e.rsi_lookback).max().to_numpy()
    prev_upper, prev_lower = np.r_[np.nan, upper[:-1]], np.r_[np.nan, lower[:-1]]
    pivot_dist = np.where(buy, np.abs(c - pivot_low), np.abs(pivot_high - c))
    score = (
        _map(_ratio(np.where(buy, close_3 - c, c - close_3), atr, 0.0), 0.3, 2.0, 0.0, 17.0)
        + _map(er, 0.15, 0.7, 0.0, 17.0)
        + np.where(has_volume & e.use_vol, _map(vol_z, 0.0, 3.0, 0.0, 17.0), BYPASS_SCORE)
        + (_map(np.where(buy, np.fmax(0.0, e.rsi_os - rsi_low), np.fmax(0.0, rsi_high - e.rsi_ob)), 0.0, 15.0, 0.0, 17.0)
           if e.use_rsi else BYPASS_SCORE)
        + (16.0 - _map(_ratio(np.nan_to_num(pivot_dist), atr, 0.0), 0.0, 1.5, 0.0, 10.0) if e.use_structure else BYPASS_SCORE)
        + _map(_ratio(np.where(buy, np.fmax(0.0, prev_upper - prev_c), np.fmax(0.0, prev_c - prev_lower)), atr, 0.0), 0.0, 1.0, 0.0, 16.0))

    return bars.assign(atr=atr, er=er, vol_ratio=vol_ratio, tqi=tqi, trend=trend, lower=lower, upper=upper,
                       st_line=np.where(trend == 1, lower, upper), flip=flip, char_flip=char_flip, signal=signal,
                       score=score, sl_long=sl_long, sl_short=sl_short, tp1_r=tps[0], tp2_r=tps[1], tp3_r=tps[2])
