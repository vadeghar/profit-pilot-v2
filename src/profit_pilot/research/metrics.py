"""Backtest research metrics module — slim and deterministic."""
from typing import Sequence


def total_return(initial_cash: float, final_cash: float) -> float:
    return (final_cash - initial_cash) / initial_cash if initial_cash > 0 else 0.0


def max_drawdown(equity_curve: Sequence[tuple]) -> float:
    """Compute max drawdown from a sequence of (datetime, equity) pairs."""
    peak = 0.0
    max_dd = 0.0
    for _, equity in equity_curve:
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def sharpe(returns_pct: Sequence[float], bars_per_year: int) -> float:
    """Simplified Sharpe: annualized return / annualized vol.
    Zero-vol symmetric return series => 0.0 (not NaN/Inf)."""
    if not returns_pct:
        return 0.0
    mean_r = sum(returns_pct) / len(returns_pct)
    # Volatility approximation
    var = sum((r - mean_r) ** 2 for r in returns_pct) / max(len(returns_pct) - 1, 1)
    std = var ** 0.5
    # Annualize
    # If std is zero (e.g. all returns identical), return 0.0 to avoid division by zero
    if std == 0.0:
        return 0.0
    return (mean_r / std) * (bars_per_year ** 0.5)
