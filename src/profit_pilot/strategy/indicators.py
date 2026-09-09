from __future__ import annotations

from collections.abc import Sequence


def simple_moving_average(values: Sequence[float], window: int) -> float | None:
    """Mean of the last ``window`` values, or ``None`` if fewer are available.

    Deterministic and allocation-light; used by strategies on the frozen data.
    """
    if window <= 0:
        raise ValueError("window must be positive")  # noqa: TRY003
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def crossover(series: Sequence[float], fast: int, slow: int) -> int:
    """Compare a fast vs slow moving average on the latest bar.

    Returns:
        ``1``  fast crossed ABOVE slow (bullish) on the latest bar,
        ``-1`` fast crossed BELOW slow (bearish),
        ``0``  no cross, insufficient history, or equal values.
    """
    fast_now = simple_moving_average(series, fast)
    fast_prev = simple_moving_average(series[:-1], fast)
    slow_now = simple_moving_average(series, slow)
    slow_prev = simple_moving_average(series[:-1], slow)
    if None in (fast_now, fast_prev, slow_now, slow_prev):
        return 0
    crossed_up = fast_now > slow_now and fast_prev <= slow_prev
    crossed_down = fast_now < slow_now and fast_prev >= slow_prev
    if crossed_up:
        return 1
    if crossed_down:
        return -1
    return 0