"""Signal state machine, entries and exits (§7, §9)."""
import numpy as np
import pandas as pd


def bars_since(cond: pd.Series) -> pd.Series:
    """Number of bars since cond was last True (0 on the bar it's True)."""
    out = np.full(len(cond), -1, dtype=np.int64)
    c = cond.to_numpy()
    last = -1
    for t in range(len(c)):
        if c[t]:
            last = t
        out[t] = t - last if last >= 0 else -1
    return pd.Series(out, index=cond.index)


def generate_signals(prediction: pd.Series, filter_all: pd.Series) -> pd.DataFrame:
    """§7 — persisting signal, bar-count tracking, early-flip detection."""
    idx = prediction.index
    raw = pd.Series(np.where(filter_all & (prediction > 0), 1,
                    np.where(filter_all & (prediction < 0), -1, 0)), index=idx)
    signal = raw.replace(0, np.nan).ffill().fillna(0).astype(int)

    bars_held = np.empty(len(signal), dtype=np.int64)
    sig = signal.to_numpy()
    # Pine: barsHeld := ta.change(signal) ? 0 : barsHeld + 1  (var int = 0).
    # On bar 0 ta.change(signal) is na -> falsy, so Pine evaluates
    # barsHeld + 1 = 1.  Reproduce that quirk exactly.
    changed = np.zeros(len(sig), dtype=bool)
    for t in range(1, len(sig)):
        changed[t] = sig[t] != sig[t - 1]
    prev = 0
    for t in range(len(sig)):
        if changed[t]:
            prev = 0
        else:
            prev = prev + 1
        bars_held[t] = prev
    bars_held = pd.Series(bars_held, index=idx)

    is_held_four = bars_held == 4
    is_held_lt_four = (bars_held > 0) & (bars_held < 4)

    # Pine: signalChange = ta.change(signal), isEarlySignalFlip = signalChange and
    # (change[1] or change[2] or change[3]).  NaN-from-history comparisons would
    # be falsy in Pine, so early bars (shift(4) == NaN) are explicitly False.
    signal_change = pd.Series(changed, index=idx)
    sc1 = (signal.shift(1) != signal.shift(2)).fillna(False)
    sc2 = (signal.shift(2) != signal.shift(3)).fillna(False)
    sc3 = (signal.shift(3) != signal.shift(4)).fillna(False)
    is_early_signal_flip = signal_change & (sc1 | sc2 | sc3)

    return pd.DataFrame({
        "signal": signal, "bars_held": bars_held,
        "is_held_four_bars": is_held_four,
        "is_held_less_than_four_bars": is_held_lt_four,
        "signal_change": signal_change,
        "is_early_signal_flip": is_early_signal_flip,
    })


def entry_exit_signals(signals: pd.DataFrame, kernels: dict, trend, s) -> pd.DataFrame:
    """§9 — start/end long/short trade booleans.

    trend: tuple (ema_up, ema_down, sma_up, sma_down) of boolean Series.
    """
    ema_up, ema_down, sma_up, sma_down = trend
    sig = signals["signal"]
    change = signals["signal_change"]
    held4 = signals["is_held_four_bars"]
    held_lt4 = signals["is_held_less_than_four_bars"]
    early_flip = signals["is_early_signal_flip"]

    is_buy = (sig == 1) & ema_up & sma_up
    is_sell = (sig == -1) & ema_down & sma_down
    is_last_buy = (sig.shift(4) == 1) & ema_up.shift(4) & sma_up.shift(4)
    is_last_sell = (sig.shift(4) == -1) & ema_down.shift(4) & sma_down.shift(4)
    is_new_buy = is_buy & change
    is_new_sell = is_sell & change

    is_bullish = kernels["is_bullish"]
    is_bearish = kernels["is_bearish"]

    start_long = is_new_buy & is_bullish & ema_up & sma_up
    start_short = is_new_sell & is_bearish & ema_down & sma_down

    # §9.2 dynamic exits
    bs_long = bars_since(start_long)
    bs_short = bars_since(start_short)
    bs_bull_alert = bars_since(kernels["alert_bullish"])
    bs_bear_alert = bars_since(kernels["alert_bearish"])
    valid_short_exit = bs_bull_alert > bs_short
    valid_long_exit = bs_bear_alert > bs_long
    end_long_dyn = kernels["is_bearish_change"] & valid_long_exit.shift(1).fillna(False)
    end_short_dyn = kernels["is_bullish_change"] & valid_short_exit.shift(1).fillna(False)

    # §9.3 strict exits
    end_long_strict = ((held4 & is_last_buy) | (held_lt4 & is_new_sell & is_last_buy)) & start_long.shift(4).fillna(False)
    end_short_strict = ((held4 & is_last_sell) | (held_lt4 & is_new_buy & is_last_sell)) & start_short.shift(4).fillna(False)

    is_dyn_exit_valid = (not s.use_ema_filter) and (not s.use_sma_filter) and (not s.use_kernel_smoothing)
    use_dyn = s.use_dynamic_exits and is_dyn_exit_valid
    end_long = end_long_dyn if use_dyn else end_long_strict
    end_short = end_short_dyn if use_dyn else end_short_strict

    return pd.DataFrame({
        "is_buy_signal": is_buy, "is_sell_signal": is_sell,
        "is_new_buy_signal": is_new_buy, "is_new_sell_signal": is_new_sell,
        "start_long_trade": start_long, "start_short_trade": start_short,
        "end_long_trade": end_long, "end_short_trade": end_short,
        "is_early_signal_flip": early_flip,
    })
