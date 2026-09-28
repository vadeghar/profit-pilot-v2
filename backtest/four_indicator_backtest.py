"""Real historical backtest runner for the Four Indicator System.

Every entry/exit decision is derived once from real NIFTY spot candles via
``strategies.four_indicator_system.FourIndicatorSignalEngine`` - the same
engine used for live trading - so backtest and live can never diverge. For
each entry the runner resolves the nearest NIFTY weekly expiry (CE for a
call entry, PE for a put entry), probes real Breeze premiums outward from
ATM to find the strike closest to the 1%-of-spot target defined in the
source system, and marks the trade against that contract's real historical
candles until the SuperTrend exit or an intraday square-off (the source
describes this as an intraday setup).

Like ``backtest/nifty_no_brainer_runner.py`` this never uses a local candle
cache: it fetches one trading day at a time straight from the configured
provider via ``TradingDayFetcher``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Optional

from backtest.charges import ChargeConfig, Fill, option_charges
from market_data.option_symbol import get_option_symbol
from market_data.trading_days import SESSION_CLOSE, TradingCalendar, TradingDayFetcher
from platform_config import get_index_lot_size
from strategies.four_indicator_system import FourIndicatorConfig, FourIndicatorSignalEngine
from utils.timezone import IST

DEFAULT_LOT_SIZE = get_index_lot_size("NSE:NIFTY", 75)
SQUARE_OFF_TIME = time(15, 20)
WEEKLY_EXPIRY_WEEKDAY_CHANGE_DATE = date(2025, 9, 1)  # NIFTY weekly expiry moved Thu -> Tue
STRIKE_STEP = 50
MAX_STRIKE_PROBES = 8
TARGET_TOLERANCE = 0.05  # stop probing once within 5% of the target premium


@dataclass
class TradeResult:
    entry_date: str
    entry_time: str
    expiry: str
    strike: int
    side: str  # "CE" | "PE"
    entry_spot: float
    entry_premium: float
    lots: int
    quantity: int
    indicators_at_entry: dict = field(default_factory=dict)
    exit_time: str = ""
    exit_premium: float = 0.0
    exit_reason: str = ""
    gross_pnl: float = 0.0
    charges_total: float = 0.0
    pnl: float = 0.0
    status: str = "OPEN"
    flags: list = field(default_factory=list)


def weekly_expiry_weekday(day: date) -> int:
    """0=Mon..6=Sun. NIFTY weeklies: Tuesday from Sep-2025, Thursday before."""
    return 1 if day >= WEEKLY_EXPIRY_WEEKDAY_CHANGE_DATE else 3


def resolve_weekly_expiry(entry_day: date, calendar: TradingCalendar) -> date:
    """Next weekly expiry on/after ``entry_day``, rolled back a day on holidays."""
    weekday = weekly_expiry_weekday(entry_day)
    delta = (weekday - entry_day.weekday()) % 7
    candidate = entry_day + timedelta(days=delta)
    return calendar.previous_trading_day(candidate)


def _option_request(expiry: date, strike: int, option_type: str) -> dict:
    request = get_option_symbol("breeze", "NIFTY", expiry, strike, option_type)
    if not isinstance(request, dict):
        raise TypeError("Breeze option request builder must return a mapping")
    return request


def round_to_strike_step(value: float, step: int = STRIKE_STEP) -> int:
    return int(round(value / step) * step)


def _premium_at(fetcher: TradingDayFetcher, expiry: date, strike: int, option_type: str,
                at: datetime) -> Optional[float]:
    rows = fetcher.fetch(_option_request(expiry, strike, option_type), "5m", at, at + timedelta(minutes=15))
    return float(rows[0].close) if rows else None


def find_strike_for_target_premium(fetcher: TradingDayFetcher, expiry: date, spot: float,
                                   at: datetime, option_type: str = "CE", target_pct: float = 0.01
                                   ) -> Optional[tuple[int, float]]:
    """Probe real premiums outward from ATM for the strike closest to
    ``target_pct`` of spot - the source system's "1% of underlying" rule.

    A call's premium falls as the strike moves further OTM (higher) and
    rises moving ITM (lower); a put mirrors this (premium rises moving
    further ITM, i.e. as the strike rises). Each probe steers toward the
    target; overshooting it halves the step (bisection-style convergence).
    """
    target = spot * target_pct
    strike = round_to_strike_step(spot)
    best: Optional[tuple[int, float]] = None
    step = STRIKE_STEP * 4
    direction = 0
    tried: set[int] = set()
    call_sign = 1 if option_type == "CE" else -1  # CE: higher strike -> lower premium; PE: the reverse
    for _ in range(MAX_STRIKE_PROBES):
        if strike <= 0 or strike in tried:
            break
        tried.add(strike)
        premium = _premium_at(fetcher, expiry, strike, option_type, at)
        if premium is None:
            break
        if best is None or abs(premium - target) < abs(best[1] - target):
            best = (strike, premium)
        if target > 0 and abs(premium - target) / target < TARGET_TOLERANCE:
            break
        # too rich -> move toward less premium; too cheap -> move toward more premium
        new_direction = call_sign if premium > target else -call_sign
        if direction and new_direction != direction:
            step = max(STRIKE_STEP, step // 2)
        direction = new_direction
        strike = round_to_strike_step(strike + direction * step)
    return best


def _finalize_trade(trade: TradeResult, exit_ts: datetime, exit_premium: float, reason: str,
                    charge_cfg: ChargeConfig) -> None:
    trade.exit_time = exit_ts.isoformat()
    trade.exit_premium = exit_premium
    trade.exit_reason = reason
    gross = (exit_premium - trade.entry_premium) * trade.quantity
    fills = [Fill("BUY", trade.entry_premium, trade.quantity), Fill("SELL", exit_premium, trade.quantity)]
    charges = option_charges(fills, exit_ts.date(), charge_cfg)
    trade.gross_pnl = gross
    trade.charges_total = charges["total"]
    trade.pnl = gross - charges["total"]
    trade.status = "CLOSED"


def _warmup_start(start: date, calendar: TradingCalendar, warmup_days: int) -> date:
    probe, seen = start, 0
    limit = start - timedelta(days=warmup_days * 4 + 10)
    while seen < warmup_days and probe > limit:
        probe -= timedelta(days=1)
        if calendar.is_trading_day(probe):
            seen += 1
            start = probe
    return start


def summarize(trades: list[TradeResult], capital: float, final_balance: float) -> dict[str, Any]:
    closed = [t for t in trades if t.status == "CLOSED"]
    pnls = [t.pnl for t in closed]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
    equity = peak = drawdown = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "total_trades": len(closed),
        "open_trades": sum(t.status == "OPEN" for t in trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": (len(wins) / len(closed) * 100) if closed else 0.0,
        "total_pnl": sum(pnls),
        "gross_pnl": sum(t.gross_pnl for t in closed),
        "total_charges": sum(t.charges_total for t in closed),
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses else (float("inf") if wins else 0.0),
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "max_drawdown_rupees": drawdown,
        "initial_capital": capital,
        "final_balance": final_balance,
        "exit_reason_split": {r: sum(t.exit_reason == r for t in closed)
                              for r in sorted({t.exit_reason for t in closed if t.exit_reason})},
    }


def run_four_indicator_backtest(provider: Any, start: date, end: date, *,
                                timeframe: str = "5m", capital: float = 100_000.0,
                                capital_per_lot: float = 50_000.0, lot_size: Optional[int] = None,
                                target_premium_pct: float = 0.01,
                                config: Optional[FourIndicatorConfig] = None,
                                calendar: Optional[TradingCalendar] = None,
                                fetcher: Optional[TradingDayFetcher] = None,
                                charges: Optional[ChargeConfig] = None,
                                warmup_days: int = 10,
                                on_event: Optional[Callable[[str, dict], None]] = None,
                                progress: Optional[Callable[[TradeResult], None]] = None) -> dict[str, Any]:
    """Backtest every Four Indicator System entry whose signal falls in [start, end]."""
    calendar = calendar or TradingCalendar()
    fetcher = fetcher or TradingDayFetcher(provider, calendar)
    lot = lot_size or DEFAULT_LOT_SIZE
    charge_cfg = charges if charges is not None else ChargeConfig()
    cfg = config or FourIndicatorConfig(timeframe=timeframe)
    engine = FourIndicatorSignalEngine(cfg)

    fetch_start = _warmup_start(start, calendar, warmup_days)
    spot_rows = fetcher.fetch("NSE:NIFTY", timeframe,
                              datetime.combine(fetch_start, time(9, 15), IST),
                              datetime.combine(end, SESSION_CLOSE, IST))

    trades: list[TradeResult] = []
    balance = capital
    equity = [{"timestamp": datetime.combine(start, time(9, 15), IST).isoformat(), "value": capital}]
    open_trade: Optional[dict[str, Any]] = None

    def emit(kind: str, payload: dict[str, Any]) -> None:
        if on_event:
            on_event(kind, payload)

    def close_trade(exit_ts: datetime, exit_premium: Optional[float], reason: str) -> None:
        nonlocal balance, open_trade
        trade: TradeResult = open_trade["trade"]
        if exit_premium is None:
            trade.flags.append("NO_EXIT_PRICE_CARRIED_ENTRY_PREMIUM")
            exit_premium = trade.entry_premium
        _finalize_trade(trade, exit_ts, exit_premium, reason, charge_cfg)
        balance += trade.pnl
        equity.append({"timestamp": exit_ts.isoformat(), "value": balance})
        trades.append(trade)
        emit("exit", {"trade": asdict(trade), "balance": balance})
        if progress:
            progress(trade)
        open_trade = None

    for candle in spot_rows:
        ts = candle.timestamp
        result = engine.process(candle)

        if result and result["action"] == "ENTER" and ts.date() >= start:
            side = result["side"]
            expiry = resolve_weekly_expiry(ts.date(), calendar)
            found = find_strike_for_target_premium(fetcher, expiry, result["price"], ts, side, target_premium_pct)
            if found is None:
                engine.in_position = False  # could not actually resolve a tradable contract
                emit("entry_skipped", {"time": ts.isoformat(), "side": side, "reason": "no_option_data"})
            else:
                strike, premium = found
                lots = max(1, int(balance // capital_per_lot))
                trade = TradeResult(
                    entry_date=ts.date().isoformat(), entry_time=ts.isoformat(),
                    expiry=expiry.isoformat(), strike=strike, side=side, entry_spot=result["price"],
                    entry_premium=premium, lots=lots, quantity=lots * lot,
                    indicators_at_entry=result["indicators"],
                )
                open_trade = {"trade": trade, "expiry": expiry, "strike": strike, "side": side, "entry_ts": ts}
                emit("entry", {"trade": asdict(trade), "balance": balance})
            continue

        if result and result["action"] == "EXIT" and open_trade is not None:
            exit_premium = _premium_at(fetcher, open_trade["expiry"], open_trade["strike"],
                                       open_trade["side"], ts)
            close_trade(ts, exit_premium, "supertrend_flip")
            continue

        if open_trade is not None:
            entry_ts = open_trade["entry_ts"]
            same_session_day = ts.date() == entry_ts.date()
            past_cutoff = ts.timetz().replace(tzinfo=None) >= SQUARE_OFF_TIME
            if same_session_day and past_cutoff:
                exit_premium = _premium_at(fetcher, open_trade["expiry"], open_trade["strike"],
                                           open_trade["side"], ts)
                close_trade(ts, exit_premium, "intraday_square_off")
                engine.in_position = False

    if open_trade is not None:
        # Ran off the end of the requested window still holding the trade.
        trade: TradeResult = open_trade["trade"]
        trade.flags.append("STILL_OPEN_AT_BACKTEST_END")
        trades.append(trade)

    return {
        "params": {
            "start": start.isoformat(), "end": end.isoformat(), "timeframe": timeframe,
            "capital": capital, "capital_per_lot": capital_per_lot, "lot_size": lot,
            "target_premium_pct": target_premium_pct,
            "supertrend_period": cfg.supertrend_period, "supertrend_multiplier": cfg.supertrend_multiplier,
            "rsi_period": cfg.rsi_period, "rsi_threshold": cfg.rsi_threshold,
            "put_rsi_threshold": cfg.put_rsi_threshold,
            "bollinger_period": cfg.bollinger_period, "bollinger_std": cfg.bollinger_std,
            "enable_calls": cfg.enable_calls, "enable_puts": cfg.enable_puts,
        },
        "data_source": {"provider": "breeze", "local_candle_cache": False, "requests": fetcher.requests,
                        "non_trading_days_skipped": len(fetcher.skipped_days)},
        "trades": [asdict(t) for t in trades],
        "summary": summarize(trades, capital, balance),
        "equity_curve": equity,
    }


def to_ui_result(report: dict[str, Any], capital: float) -> dict[str, Any]:
    """Shape a runner report like the dashboard's generic backtest result payload."""
    closed = [t for t in report["trades"] if t["status"] == "CLOSED"]
    summary = report["summary"]
    total = summary["total_pnl"]
    trades = [{
        "trade_id": f"4IND-{t['entry_date']}-{t['strike']}{t['side']}",
        "instrument": f"NIFTY {t['strike']} {t['side']}",
        "quantity": t["quantity"], "entry_time": t["entry_time"], "entry_price": t["entry_premium"],
        "exit_time": t["exit_time"], "exit_price": t["exit_premium"], "pnl": t["pnl"],
    } for t in closed]
    return {
        "strategy_id": "four_indicator_system", "initial_capital": capital, "final_capital": capital + total,
        "total_return": total, "total_return_pct": (total / capital * 100) if capital else 0.0,
        "win_rate": summary["win_rate"], "total_trades": summary["total_trades"],
        "winning_trades": summary["winning_trades"], "losing_trades": summary["losing_trades"],
        "max_drawdown": summary["max_drawdown_rupees"], "sharpe_ratio": 0.0,
        "profit_factor": summary["profit_factor"] if summary["profit_factor"] != float("inf") else 0.0,
        "candles_evaluated": report["data_source"]["requests"],
        "period": f"{report['params']['start']} -> {report['params']['end']}",
        "equity_curve": report["equity_curve"], "trades": trades,
        "four_indicator_trades": report["trades"], "four_indicator_summary": summary,
        "four_indicator_params": report["params"],
    }


__all__ = [
    "TradeResult", "resolve_weekly_expiry", "weekly_expiry_weekday",
    "find_strike_for_target_premium", "run_four_indicator_backtest", "to_ui_result", "summarize",
]
