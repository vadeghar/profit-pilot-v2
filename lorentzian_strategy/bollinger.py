"""Optional Bollinger Band overlay (§15) — additive, display/analysis only.

Must NOT be consumed by entry/exit/filter logic (§15.4).
"""
import numpy as np
import pandas as pd

from .features import wma, sma, ema

_MA_FUNCS = {"WMA": wma, "SMA": sma, "EMA": ema}


def bollinger_bands(close: pd.Series, length: int, mult: float, ma_type: str = "WMA"):
    """§15.2 — basis / upper / lower using a WMA basis by default.

    Uses only data through the current closed bar (rolling, non-repainting).
    """
    if ma_type not in _MA_FUNCS:
        raise ValueError(f"Unsupported bollinger_ma_type {ma_type!r}; supported: {list(_MA_FUNCS)}")
    basis = _MA_FUNCS[ma_type](close, length)
    stddev = close.rolling(length).std(ddof=0)
    upper = basis + mult * stddev
    lower = basis - mult * stddev
    return basis, upper, lower


def compute_bollinger(df: pd.DataFrame, settings):
    """Returns a dict of band series, or None when disabled (§15.4)."""
    if not settings.use_bollinger_bands:
        return None
    basis, upper, lower = bollinger_bands(
        df["close"], int(settings.bollinger_length), float(settings.bollinger_mult),
        settings.bollinger_ma_type,
    )
    # bollinger_offset is display-only: shifting the plot must not shift values
    # used in any calculation, so we simply report it alongside.
    return {"basis": basis, "upper": upper, "lower": lower,
            "offset": int(settings.bollinger_offset)}
