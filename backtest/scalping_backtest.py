"""Tick-replay backtest for the NIFTY option scalping strategies.

Replays every recorded day in data/ticks (real Angel One ticks preferred,
Breeze 1-second pseudo-ticks otherwise) through the same ScalpEngine the live
paper session uses. The balance compounds across days.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

from market_data.tick_store import best_source, list_days, load_instruments, read_ticks
from strategies.scalping import SCALP_STRATEGIES, ScalpConfig

LTQ_DEPENDENT = {"scalp_stealth_accum", "scalp_oi_volume_burst"}


def available_days(start: date, end: date, root: Optional[Path] = None) -> list[tuple[date, str]]:
    days = sorted({date.fromisoformat(d["date"]) for d in list_days(root)})
    return [(d, best_source(d, root)) for d in days if start <= d <= end]


def run_scalping_backtest(strategy_id: str, start: date, end: date, *, capital: float = 100_000.0,
                          overrides: Optional[dict] = None, root: Optional[Path] = None,
                          on_event: Optional[Callable[[str, dict], None]] = None,
                          progress: Optional[Callable[[date, int, int], None]] = None) -> dict[str, Any]:
    cls = SCALP_STRATEGIES[strategy_id]
    cfg = cls.default_config().update(overrides)
    days = available_days(start, end, root)
    if not days:
        raise RuntimeError(
            f"No recorded tick data between {start} and {end}. Record ticks during market hours "
            "(the tick recorder auto-starts at 09:12 IST) or import past days with "
            "`python -m tools.scalping.import_breeze_1s --date YYYY-MM-DD`.")
    engine = cls({}, capital=capital, config=cfg, on_event=on_event)
    day_rows, ticks_total = [], 0
    for i, (day, source) in enumerate(days, 1):
        insts, meta = load_instruments(source, day, root)
        engine.set_instruments(insts)
        before, bal_before = len(engine.trades), engine.balance
        n, last = 0, None
        for t in read_ticks(source, day, root):
            engine.on_tick(t)
            n, last = n + 1, t.ts
        if last is not None:
            engine.finish(last)
        ticks_total += n
        day_rows.append({"date": day.isoformat(), "source": source, "ticks": n, "trades": len(engine.trades) - before,
                         "net": round(engine.balance - bal_before, 2), "expiry": meta.get("expiry")})
        if progress:
            progress(day, i, len(days))
    trades = [asdict(t) for t in engine.trades]
    warnings = []
    pseudo = [r["date"] for r in day_rows if r["source"] == "breeze_1s"]
    if pseudo:
        warnings.append(f"{len(pseudo)} day(s) are Breeze 1-second pseudo-ticks (no bid/ask; fills at LTP +/- "
                        f"{cfg.no_quote_slippage} pts).")
        if strategy_id in LTQ_DEPENDENT:
            warnings.append("This strategy's big-print / LTQ-burst rule needs real per-trade LTQ: on Breeze "
                            "1-second days LTQ is a whole second's volume, so that rule almost never fires there. "
                            "Judge it on recorded Angel tick days.")
    return {"strategy_id": strategy_id, "name": cls.name, "start": start.isoformat(), "end": end.isoformat(),
            "capital": capital, "final_balance": engine.balance, "config": asdict(cfg), "days": day_rows,
            "ticks": ticks_total, "trades": trades, "summary": summarize(trades, capital), "warnings": warnings}


def summarize(trades: list[dict], capital: float) -> dict[str, Any]:
    nets = [t["net"] for t in trades]
    wins, losses = [x for x in nets if x > 0], [x for x in nets if x <= 0]
    eq = peak = capital
    mdd = mdd_abs = 0.0
    for x in nets:
        eq += x
        peak = max(peak, eq)
        mdd_abs = max(mdd_abs, peak - eq)
        mdd = max(mdd, (peak - eq) / peak if peak > 0 else 0.0)
    gw, gl = sum(wins), -sum(losses)
    by_strategy_day = defaultdict(float)
    for t in trades:
        by_strategy_day[t["date"]] += t["net"]
    return {
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "gross_pnl": round(sum(t["gross"] for t in trades), 2),
        "charges": round(sum(t["charges"] for t in trades), 2),
        "spread_cost": round(sum(t["spread_cost"] for t in trades), 2),
        "net_pnl": round(sum(nets), 2),
        "return_pct": sum(nets) / capital if capital else 0.0,
        "profit_factor": (gw / gl) if gl > 0 else None,  # None = no losing trades (JSON has no Infinity)
        "expectancy": sum(nets) / len(nets) if nets else 0.0,
        "avg_win": gw / len(wins) if wins else 0.0, "avg_loss": -gl / len(losses) if losses else 0.0,
        "max_drawdown_pct": mdd, "max_drawdown_abs": mdd_abs,
        "avg_hold_sec": sum(t["hold_sec"] for t in trades) / len(trades) if trades else 0.0,
        "exit_reasons": dict(Counter(t["reason"] for t in trades)),
        "by_day": {d: round(v, 2) for d, v in sorted(by_strategy_day.items())},
    }


def to_ui_result(report: dict[str, Any]) -> dict[str, Any]:
    """Shape like the dashboard's generic backtest result payload."""
    s, cap = report["summary"], report["capital"]
    curve, bal = [{"timestamp": report["start"], "value": cap}], cap
    for t in report["trades"]:
        bal += t["net"]
        curve.append({"timestamp": t["exit_time"], "value": bal})
    return {
        "strategy_id": report["strategy_id"], "initial_capital": cap, "final_capital": report["final_balance"],
        "total_return": s["net_pnl"], "total_return_pct": s["return_pct"] * 100, "win_rate": s["win_rate"] * 100,
        "total_trades": s["trades"], "winning_trades": s["wins"], "losing_trades": s["losses"],
        "max_drawdown": s["max_drawdown_pct"] * 100, "sharpe_ratio": 0.0,
        "profit_factor": s["profit_factor"] or 0.0,
        "candles_evaluated": report["ticks"], "period": f"{report['start']} -> {report['end']}",
        "equity_curve": curve,
        "trades": [{"trade_id": f"{t['date']}-{t['entry_time'][11:19]}-{t['kind']}{t['strike']:.0f}",
                    "instrument": t["symbol"], "quantity": t["qty"], "entry_time": t["entry_time"],
                    "entry_price": t["entry"], "exit_time": t["exit_time"], "exit_price": t["exit"],
                    "pnl": t["net"]} for t in report["trades"]],
        "scalp_summary": s, "scalp_days": report["days"], "warnings": report.get("warnings", []),
    }
