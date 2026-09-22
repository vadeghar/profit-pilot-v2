"""Trade simulation and metrics (§10)."""
import numpy as np
import pandas as pd


def simulate(close: pd.Series, entries: pd.DataFrame, use_worst_case: bool = False) -> dict:
    """§10.1 — one position at a time, signal-driven exits, forced flip on opposite entry.

    Entry/exit price is the signal bar's close (close-only when use_worst_case,
    which is the same bar-close price here since no intrabar fill is modeled).
    """
    start_long = entries["start_long_trade"].to_numpy()
    start_short = entries["start_short_trade"].to_numpy()
    end_long = entries["end_long_trade"].to_numpy()
    end_short = entries["end_short_trade"].to_numpy()
    px = close.to_numpy()
    n = len(px)

    trades = []          # dicts: dir, entry_idx, entry_px, exit_idx, exit_px, reason
    position = 0         # 0 flat, 1 long, -1 short
    entry_idx = entry_px = -1
    for t in range(n):
        if position == 1:
            if start_short[t]:
                trades.append(dict(direction=1, entry_index=entry_idx, entry_price=entry_px,
                                   exit_index=t, exit_price=px[t], reason="flip_short"))
                position, entry_idx, entry_px = -1, t, px[t]
            elif end_long[t]:
                trades.append(dict(direction=1, entry_index=entry_idx, entry_price=entry_px,
                                   exit_index=t, exit_price=px[t], reason="signal_exit"))
                position = 0
        elif position == -1:
            if start_long[t]:
                trades.append(dict(direction=-1, entry_index=entry_idx, entry_price=entry_px,
                                   exit_index=t, exit_price=px[t], reason="flip_long"))
                position, entry_idx, entry_px = 1, t, px[t]
            elif end_short[t]:
                trades.append(dict(direction=-1, entry_index=entry_idx, entry_price=entry_px,
                                   exit_index=t, exit_price=px[t], reason="signal_exit"))
                position = 0
        else:
            if start_long[t]:
                position, entry_idx, entry_px = 1, t, px[t]
            elif start_short[t]:
                position, entry_idx, entry_px = -1, t, px[t]
    # close any open position at the last bar (mark-to-market)
    if position != 0:
        trades.append(dict(direction=position, entry_index=entry_idx, entry_price=entry_px,
                           exit_index=n - 1, exit_price=px[n - 1], reason="end_of_data"))

    wins = sum(1 for tr in trades
               if (tr["direction"] == 1 and tr["exit_price"] > tr["entry_price"])
               or (tr["direction"] == -1 and tr["exit_price"] < tr["entry_price"]))
    losses = len(trades) - wins
    total = len(trades)
    win_rate = wins / total if total else 0.0
    win_loss_ratio = wins / losses if losses else float("inf") if wins else 0.0

    # per-trade returns -> equity curve over exit bars
    returns = pd.Series(0.0, index=close.index)
    for tr in trades:
        r = (tr["exit_price"] - tr["entry_price"]) / tr["entry_price"] * tr["direction"]
        returns.iloc[tr["exit_index"]] = r
    equity = (1.0 + returns).cumprod()

    metrics = {
        "total_trades": total,
        "total_wins": wins,
        "total_losses": losses,
        "win_rate": win_rate,
        "win_loss_ratio": win_loss_ratio,
        "total_early_signal_flips": int(entries["is_early_signal_flip"].sum()),
        "sharpe_ratio": _sharpe(returns),
        "max_drawdown": _max_drawdown(equity),
    }
    return {"metrics": metrics, "trades": pd.DataFrame(trades), "equity": equity}


def _sharpe(returns: pd.Series, periods_per_year: float = 252.0) -> float:
    std = returns.std()
    if not std or np.isnan(std):
        return 0.0
    return float(returns.mean() / std * np.sqrt(periods_per_year))


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(dd.min())


def report(metrics: dict) -> str:
    wlr = metrics["win_loss_ratio"]
    wlr_str = f"{wlr:.2f}" if np.isfinite(wlr) else "inf"
    return (
        "=== Lorentzian Classification ML Backtest Report ===\n"
        f"Total trades           : {metrics['total_trades']}\n"
        f"Total wins             : {metrics['total_wins']}\n"
        f"Total losses           : {metrics['total_losses']}\n"
        f"Win rate               : {metrics['win_rate']:.2%}\n"
        f"Win/loss ratio         : {wlr_str}\n"
        f"Early signal flips     : {metrics['total_early_signal_flips']}\n"
        f"Sharpe ratio (opt.)    : {metrics['sharpe_ratio']:.3f}\n"
        f"Max drawdown (opt.)    : {metrics['max_drawdown']:.2%}\n"
    )
