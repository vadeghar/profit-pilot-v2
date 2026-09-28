"""NIFTY No Brainer 1:-2:1 call-ratio backtest fed live from Breeze.

Nothing is read from or written to a local candle store.  For every month the
runner pulls the candles it needs from Breeze (one request per *trading day*;
weekends and exchange holidays are never requested), normalizes them through
``market_data.normalize`` and feeds them to the rules in
``strategies.nifty_no_brainer_reference``.  Candles are discarded after each
month; only derived numbers land in the report.

Conventions (see strategy_rulebook.txt):
  * entry: last Friday of the month (previous trading day if not tradable),
    first option bar at/after 15:16 IST; spot = last 1m close in 15:10-15:15.
  * expiry: next month's monthly expiry.  Candidate dates (JSON hint, calendar
    rule, earlier trading days) are probed on Breeze and the first one that
    returns bars for the near-buy strike wins - no contract master needed.
  * margin: broker figure from Breeze margin_calculator (fixed per-set value
    calibrated on today's live structure, see brokers/breeze_margin.py); the
    700-point proxy is only the fallback.  Historical broker margin for expired
    contracts is not recoverable, so this is an estimate.
  * exits: target/stop on lifecycle bars; a stop is booked at no worse than
    -3.3% of margin (stop 3% + 10% tolerance; cap_stop_loss), else the last bar <= 15:16 on the final
    holding day (MAX_HOLD / EXPIRY).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from backtest.charges import ChargeConfig, add_charges, option_charges, structure_fills
from backtest.nifty_no_brainer_breeze import build_breeze_option_request, load_expiry_metadata
from market_data.normalize import NormalizedCandle
from market_data.trading_days import SESSION_CLOSE, TradingCalendar, TradingDayFetcher
from strategies.nifty_no_brainer_reference import (
    Strikes, entry_decision, exit_evaluator, monthly_expiry_from_calendar, select_strikes,
)
from utils.timezone import IST

ENTRY_TIME = time(15, 16)
LEGS = ("near_buy", "sell", "hedge")
DEFAULT_LOT_SIZE = 65
MAX_ENTRY_LOOKBACK_DAYS = 4


class NoPrice(Exception):
    """A required option bar is not available from Breeze."""


@dataclass
class MonthTrade:
    month: str
    status: str = "UNTESTED"          # CLOSED | OPEN | SKIPPED | ERROR
    decision: str = ""
    entry_date: str | None = None
    entry_time: str | None = None
    expiry: str | None = None
    expiry_source: str | None = None
    lot_size: int | None = None
    spot: float | None = None
    atm: int | None = None
    strikes: dict[str, int] = field(default_factory=dict)
    entry_prices: dict[str, float] = field(default_factory=dict)
    shift_steps: int = 0
    margin: float | None = None                    # deployed capital (margin per set x lots)
    margin_per_set: float | None = None
    lots: int | None = None
    deployed_capital: float | None = None
    balance_before: float | None = None            # available (compounded) capital at entry
    balance_after: float | None = None
    gross_pnl: float | None = None
    charges: dict[str, float] = field(default_factory=dict)
    charges_total: float | None = None
    net_premium_pct_margin: float | None = None   # signed: credit +, debit -
    net_premium_rupees: float | None = None
    debit_on_downside_pct: float | None = None     # rule: <= 1% of margin
    credit_pct: float | None = None                # rule: <= 1% of margin (else shift)
    initial_net_premium_pct: float | None = None   # before any credit shift
    target_rupees: float | None = None
    stop_rupees: float | None = None
    stop_hard_cap_rupees: float | None = None      # worst booked loss: stop + tolerance
    stop_uncapped_pnl: float | None = None         # what the exit bar actually marked
    exit_time: str | None = None
    exit_reason: str | None = None
    exit_prices: dict[str, float] = field(default_factory=dict)
    pnl_rupees: float | None = None
    pnl_pct_margin: float | None = None
    margin_method: str | None = None
    flags: list[str] = field(default_factory=list)


BAND = 0.01
TARGET_PCT = 0.025
STOP_PCT = 0.03
STOP_TOLERANCE = 0.10  # a gap through the stop may cost up to 10% more than the stop (3% -> 3.3%)


def _record_premium(trade: MonthTrade, prices: Mapping[str, float], lot: int, margin: float) -> None:
    net = (2 * prices["sell"] - prices["near_buy"] - prices["hedge"]) * lot
    trade.net_premium_rupees = net
    trade.net_premium_pct_margin = net / margin
    trade.debit_on_downside_pct = max(-net, 0.0) / margin
    trade.credit_pct = max(net, 0.0) / margin
    trade.target_rupees, trade.stop_rupees = TARGET_PCT * margin, -STOP_PCT * margin
    trade.stop_hard_cap_rupees = -STOP_PCT * (1 + STOP_TOLERANCE) * margin


def margin_proxy(prices: Mapping[str, float], lot_size: int, multiplier: float = 1.0) -> float:
    """700 points + net debit, per lot (worst-case structure loss proxy)."""
    debit = max(prices["near_buy"] + prices["hedge"] - 2 * prices["sell"], 0.0)
    return (700.0 + debit) * lot_size * multiplier


def structure_pnl(entry: Mapping[str, float], now: Mapping[str, float], lot_size: int) -> float:
    return ((now["near_buy"] - entry["near_buy"]) + (now["hedge"] - entry["hedge"])
            - 2 * (now["sell"] - entry["sell"])) * lot_size


def lot_size_on(day: date, metadata: Mapping[str, Any] | None) -> int:
    rows = (metadata or {}).get("lot_size_effective") or []
    eligible = [r for r in rows if r["effective_from"] <= day.isoformat()]
    return int(eligible[-1]["lot_size"]) if eligible else DEFAULT_LOT_SIZE


def _months(start: date, end: date):
    cur = date(start.year, start.month, 1)
    while cur <= end:
        yield cur
        cur = date(cur.year + (cur.month == 12), 1 if cur.month == 12 else cur.month + 1, 1)


def _next_month(month: date) -> date:
    return date(month.year + (month.month == 12), 1 if month.month == 12 else month.month + 1, 1)


def _expiry_candidates(month: date, calendar: TradingCalendar,
                       hints: Mapping[str, Any]) -> list[tuple[date, str]]:
    nxt = _next_month(month)
    out: list[tuple[date, str]] = []
    hint = hints.get(f"{nxt:%Y-%m}")
    if hint:
        out.append((date.fromisoformat(hint["expiry"]), "json_hint"))
    rule = monthly_expiry_from_calendar(nxt.year, nxt.month, calendar.holidays)
    out.append((rule, "calendar_rule"))
    prior = rule
    for _ in range(3):  # holiday list may be incomplete: also try earlier trading days
        prior = calendar.previous_trading_day(prior - timedelta(days=1))
        out.append((prior, "calendar_rule_fallback"))
    seen: set[date] = set()
    return [(d, s) for d, s in out if not (d in seen or seen.add(d))]


class _MonthSession:
    """Per-month Breeze access: memoized entry prices for any strike."""

    def __init__(self, fetcher: TradingDayFetcher, entry_day: date, slippage: float):
        self.fetcher, self.entry_day, self.slippage = fetcher, entry_day, slippage
        self.expiry: date | None = None
        self._memo: dict[tuple[date, int], tuple[float, datetime] | None] = {}

    def raw_entry(self, expiry: date, strike: int) -> tuple[float, datetime] | None:
        key = (expiry, strike)
        if key not in self._memo:
            rows = self.fetcher.fetch(
                build_breeze_option_request(expiry, strike), "1m",
                datetime.combine(self.entry_day, ENTRY_TIME, IST),
                datetime.combine(self.entry_day, SESSION_CLOSE, IST))
            self._memo[key] = (float(rows[0].close), rows[0].timestamp) if rows else None
        return self._memo[key]

    def leg_prices(self, strikes: Strikes) -> dict[str, float]:
        """Entry prices incl. slippage (buy legs pay up, sell leg receives less)."""
        assert self.expiry is not None
        out: dict[str, float] = {}
        for name, strike in (("near_buy", strikes.near_buy), ("sell", strikes.sell), ("hedge", strikes.hedge)):
            got = self.raw_entry(self.expiry, strike)
            if got is None:
                raise NoPrice(f"{name} {strike} has no bars at/after 15:16 on {self.entry_day}")
            out[name] = got[0] + (-self.slippage if name == "sell" else self.slippage)
        return out


def _entry_spot(fetcher: TradingDayFetcher, calendar: TradingCalendar, entry_day: date,
                trade: MonthTrade) -> tuple[date, float] | None:
    """Last NIFTY 1m close in 15:10-15:15; walks back if Breeze has no bars that day."""
    day = entry_day
    for _ in range(MAX_ENTRY_LOOKBACK_DAYS):
        rows = fetcher.fetch("NSE:NIFTY", "1m", datetime.combine(day, time(15, 10), IST),
                             datetime.combine(day, time(15, 15), IST))
        if rows:
            if day != entry_day:
                trade.flags.append(f"ENTRY_DAY_FALLBACK:{entry_day}->{day}")
            return day, float(rows[-1].close)
        trade.flags.append(f"NO_SPOT_BARS:{day}")
        day = calendar.previous_trading_day(day - timedelta(days=1))
    return None


def _series(rows: list[NormalizedCandle], cutoff: datetime) -> dict[datetime, float]:
    return {c.timestamp: float(c.close) for c in rows if c.timestamp <= cutoff}


def _run_month(fetcher: TradingDayFetcher, calendar: TradingCalendar, month: date, end: date,
               metadata: Mapping[str, Any] | None, *, max_hold_days: int, lifecycle_timeframe: str,
               margin_multiplier: float, slippage_points: float, costs_per_trade: float,
               max_shift_steps: int | None, cap_stop_loss: bool, stop_tolerance: float,
               fixed_margin: float | None, margin_method: str, balance: float,
               charge_cfg: ChargeConfig, max_lots: int | None, margin_fn: Callable[..., float] | None,
               emit: Callable[[str, MonthTrade], None] | None = None) -> MonthTrade:
    trade = MonthTrade(month=f"{month:%Y-%m}", margin_method=margin_method)
    entry_day = calendar.last_friday(month.year, month.month)
    spot_info = _entry_spot(fetcher, calendar, entry_day, trade)
    if spot_info is None:
        trade.status, trade.decision = "SKIPPED", "NO_SPOT"
        return trade
    entry_day, spot = spot_info
    trade.entry_date, trade.spot = entry_day.isoformat(), spot
    lot = trade.lot_size = lot_size_on(entry_day, metadata)

    entry_dt = datetime.combine(entry_day, ENTRY_TIME, IST)

    def margin_of(px: Mapping[str, float], strikes_: Strikes) -> float:
        if margin_fn:  # own SPAN model: margin for the exact legs from the SPAN file valid at entry
            return margin_fn(entry_dt, session.expiry, strikes_.near_buy, strikes_.sell, strikes_.hedge,
                             lot) * margin_multiplier
        return fixed_margin * margin_multiplier if fixed_margin else margin_proxy(px, lot, margin_multiplier)

    selected = select_strikes(spot)
    trade.atm = selected.atm

    # -- expiry: probe Breeze with the near-buy strike ------------------------
    session = _MonthSession(fetcher, entry_day, slippage_points)
    hints = (metadata or {}).get("monthly_expiries", {})
    for candidate, source in _expiry_candidates(month, calendar, hints):
        if session.raw_entry(candidate, selected.near_buy) is not None:
            session.expiry, trade.expiry, trade.expiry_source = candidate, candidate.isoformat(), source
            break
    if session.expiry is None:
        trade.status, trade.decision = "SKIPPED", "NO_EXPIRY_CONFIRMED"
        return trade
    expiry = session.expiry

    # -- entry decision (R10-R12) ---------------------------------------------
    try:
        prices = session.leg_prices(selected)
    except NoPrice as exc:
        trade.status, trade.decision = "SKIPPED", "NO_ENTRY_PRICE"
        trade.flags.append(str(exc))
        return trade
    margin = margin_of(prices, selected)
    trade.strikes = {"near_buy": selected.near_buy, "sell": selected.sell, "hedge": selected.hedge}
    trade.entry_prices, trade.margin = prices, margin
    trade.entry_time = datetime.combine(entry_day, ENTRY_TIME, IST).isoformat()
    _record_premium(trade, prices, lot, margin)
    trade.initial_net_premium_pct = trade.net_premium_pct_margin

    def price_for(strikes: Strikes) -> Mapping[str, float]:
        return session.leg_prices(strikes)

    try:
        decision = entry_decision(spot, prices, margin, lot, max_shift_steps=max_shift_steps,
                                  price_for=price_for)
    except NoPrice as exc:  # shifted strike has no bars: cannot comply -> abort month
        trade.status, trade.decision = "SKIPPED", "ABORT_NO_PRICE_ON_SHIFT"
        trade.flags.append(str(exc))
        return trade
    trade.decision, trade.shift_steps = decision.decision, decision.steps
    if decision.decision != "TRADE":
        trade.status = "SKIPPED"
        return trade
    strikes = decision.strikes
    prices = session.leg_prices(strikes)
    margin_set = margin_of(prices, strikes)
    trade.strikes = {"near_buy": strikes.near_buy, "sell": strikes.sell, "hedge": strikes.hedge}
    trade.entry_prices, trade.margin, trade.margin_per_set = prices, margin_set, margin_set
    trade.balance_before = balance
    lots = int(balance // margin_set) if margin_set > 0 else 0
    if max_lots:
        lots = min(lots, max_lots)
    if lots < 1:
        _record_premium(trade, prices, lot, margin_set)
        trade.status, trade.decision = "SKIPPED", "INSUFFICIENT_CAPITAL"
        trade.flags.append(f"balance {balance:,.0f} < margin per set {margin_set:,.0f}")
        return trade
    qty = lot * lots
    margin = margin_set * lots
    trade.lots, trade.deployed_capital, trade.margin = lots, margin, margin
    _record_premium(trade, prices, qty, margin)
    trade.status = "OPEN"
    if emit:
        emit("entry", trade)
    entry_ts = min(session.raw_entry(expiry, s)[1] for s in trade.strikes.values())  # type: ignore[index]
    entry_at = datetime.combine(entry_day, ENTRY_TIME, IST)
    if entry_ts > entry_at + timedelta(minutes=5):
        trade.flags.append(f"ENTRY_LATE_FILL:{entry_ts:%H:%M}")

    # -- lifecycle -----------------------------------------------------------
    limit_day = calendar.previous_trading_day(min(expiry, entry_day + timedelta(days=max_hold_days)))
    fetch_day = min(limit_day, end)
    cutoff = datetime.combine(limit_day, ENTRY_TIME, IST)
    fetch_until = cutoff if fetch_day == limit_day else datetime.combine(fetch_day, SESSION_CLOSE, IST)
    series: dict[str, dict[datetime, float]] = {}
    for name, strike in trade.strikes.items():
        rows = fetcher.fetch(build_breeze_option_request(expiry, strike), lifecycle_timeframe,
                             entry_at, fetch_until)
        series[name] = _series(rows, cutoff)
    timeline = sorted({ts for s in series.values() for ts in s if ts > entry_at})
    last = dict(prices)
    carried = 0
    exit_ts: datetime | None = None
    reason: str | None = None
    still_open = False
    for ts in timeline:
        for name, s in series.items():
            if ts in s:
                last[name] = s[ts]
            else:
                carried += 1
        mtm = structure_pnl(prices, last, qty)
        verdict = exit_evaluator(mtm, margin, entry_at, ts, expiry, max_hold_days)
        if verdict.reason in ("TARGET", "STOP_LOSS"):
            reason, exit_ts = verdict.reason, ts
            break
    if carried:
        trade.flags.append(f"CARRIED_LAST_PRICE:{carried}")
    if not timeline:
        trade.status, trade.decision = "ERROR", "NO_LIFECYCLE_BARS"
        return trade
    if reason is None:
        exit_ts = timeline[-1]
        if fetch_day == limit_day:
            reason = "EXPIRY" if limit_day >= calendar.previous_trading_day(expiry) else "MAX_HOLD"
        else:
            still_open = True  # holding window extends past the requested end date
    # final marks at the exit bar, exit slippage against us on every leg
    exit_prices = dict(prices)
    for name, s in series.items():
        exit_prices[name] = next((s[t] for t in reversed(timeline) if t <= exit_ts and t in s), prices[name])
    net = dict(exit_prices)
    net["near_buy"] -= slippage_points
    net["hedge"] -= slippage_points
    net["sell"] += slippage_points
    pnl = structure_pnl(prices, net, qty)
    hard_cap = -STOP_PCT * (1 + stop_tolerance) * margin
    if reason == "STOP_LOSS" and cap_stop_loss and pnl < hard_cap:
        # Stop is 3% of deployed margin; a gap through it may cost at most 10%
        # more (3.3%).  Anything worse is booked at the hard cap; the raw
        # exit-bar mark is kept for the record.
        trade.stop_uncapped_pnl = pnl
        trade.flags.append(f"STOP_HARD_CAP_{STOP_PCT * (1 + stop_tolerance):.1%}(raw {pnl / margin:+.2%})")
        pnl = hard_cap
    # charges: entry fills on the entry day, exit fills on the exit day (estimated if still open)
    charges = add_charges(
        option_charges(structure_fills(prices["near_buy"], prices["sell"], prices["hedge"], qty, True),
                       entry_day, charge_cfg),
        option_charges(structure_fills(net["near_buy"], net["sell"], net["hedge"], qty, False),
                       exit_ts.date(), charge_cfg))
    charges["total"] += costs_per_trade
    net_pnl = pnl - charges["total"]
    trade.exit_time, trade.exit_reason = exit_ts.isoformat(), reason
    trade.exit_prices = exit_prices
    trade.gross_pnl, trade.charges, trade.charges_total = pnl, charges, charges["total"]
    trade.pnl_rupees, trade.pnl_pct_margin = net_pnl, net_pnl / margin
    trade.status = "OPEN" if still_open else "CLOSED"
    trade.balance_after = balance + net_pnl if not still_open else balance
    if still_open:
        trade.flags.append("OPEN: P&L is mark-to-market incl. estimated exit charges")
    return trade


def summarize(trades: list[MonthTrade]) -> dict[str, Any]:
    closed = [t for t in trades if t.status == "CLOSED"]
    pnls = [t.pnl_rupees or 0.0 for t in closed]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
    equity = peak = drawdown = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "months_tested": len(trades),
        "closed_trades": len(closed),
        "open_trades": sum(t.status == "OPEN" for t in trades),
        "skipped_months": sum(t.status == "SKIPPED" for t in trades),
        "error_months": sum(t.status == "ERROR" for t in trades),
        "win_rate": len(wins) / len(closed) if closed else 0.0,
        "total_pnl": sum(pnls),
        "expectancy": sum(pnls) / len(pnls) if pnls else 0.0,
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
        "max_drawdown_rupees": drawdown,
        "avg_pnl_pct_margin": (sum(t.pnl_pct_margin or 0.0 for t in closed) / len(closed)) if closed else 0.0,
        "exit_reason_split": {r: sum(t.exit_reason == r for t in closed)
                              for r in sorted({t.exit_reason for t in closed if t.exit_reason})},
        "margin_method": trades[0].margin_method if trades else None,
        "total_gross_pnl": sum(t.gross_pnl or 0.0 for t in closed),
        "total_charges": sum(t.charges_total or 0.0 for t in closed),
    }


def run_backtest(provider: Any, start: date = date(2026, 1, 1), end: date | None = None, *,
                 max_hold_days: int = 19, lifecycle_timeframe: str = "5m",
                 margin_multiplier: float = 1.0, slippage_points: float = 0.0,
                 costs_per_trade: float = 0.0, capital: float = 100_000.0, compound: bool = True,
                 max_lots: int | None = None, margin_fn: Callable[..., float] | None = None,
                 charges: ChargeConfig | None = None,
                 on_event: Callable[[str, dict[str, Any]], None] | None = None,
                 max_shift_steps: int | None = None,
                 cap_stop_loss: bool = True, stop_tolerance: float = STOP_TOLERANCE, fixed_margin: float | None = None,
                 margin_method: str = "proxy: (700 points + debit) x lot size; NOT broker margin",
                 calendar: TradingCalendar | None = None,
                 expiry_path: str | Path | None = "nifty_expiries.json",
                 fetcher: TradingDayFetcher | None = None,
                 progress: Callable[[MonthTrade], None] | None = None) -> dict[str, Any]:
    """Backtest every monthly cycle whose entry day falls in [start, end]."""
    end = end or date.today()
    calendar = calendar or TradingCalendar()
    fetcher = fetcher or TradingDayFetcher(provider, calendar)
    metadata = load_expiry_metadata(expiry_path) if expiry_path and Path(expiry_path).exists() else None
    charge_cfg = charges if charges is not None else ChargeConfig()
    balance = capital
    trades: list[MonthTrade] = []

    def running(extra: dict[str, Any]) -> dict[str, Any]:
        return {**extra, "balance": balance, "realized_pnl": balance - capital, "initial_capital": capital}

    def emit_entry(kind: str, t: MonthTrade) -> None:
        if on_event:
            on_event(kind, running({"trade": asdict(t)}))

    for month in _months(start, end):
        if not (start <= calendar.last_friday(month.year, month.month) <= end):
            continue
        try:
            trade = _run_month(
                fetcher, calendar, month, end, metadata, max_hold_days=max_hold_days,
                lifecycle_timeframe=lifecycle_timeframe, margin_multiplier=margin_multiplier,
                slippage_points=slippage_points, costs_per_trade=costs_per_trade,
                max_shift_steps=max_shift_steps, cap_stop_loss=cap_stop_loss, stop_tolerance=stop_tolerance,
                fixed_margin=fixed_margin, margin_method=margin_method,
                balance=balance if compound else capital, charge_cfg=charge_cfg, max_lots=max_lots,
                margin_fn=margin_fn,
                emit=emit_entry)
        except Exception as exc:  # keep going; the month is reported, never silently dropped
            trade = MonthTrade(month=f"{month:%Y-%m}", status="ERROR", decision="EXCEPTION",
                               flags=[f"{type(exc).__name__}: {exc}"])
            if "session" in str(exc).lower() or "authenticat" in str(exc).lower():
                trades.append(trade)
                if progress:
                    progress(trade)
                break  # dead session: remaining months would fail identically
        trades.append(trade)
        if trade.status == "CLOSED":
            balance += trade.pnl_rupees or 0.0  # realized P&L (used for sizing only when compound=True)
        if on_event:
            kind = {"CLOSED": "exit", "OPEN": "exit"}.get(trade.status, "skip")
            on_event(kind, running({"trade": asdict(trade)}))
        if progress:
            progress(trade)
    return {
        "params": {"start": start.isoformat(), "end": end.isoformat(), "max_hold_days": max_hold_days,
                   "capital": capital, "compound": compound, "max_lots": max_lots,
                   "charges": asdict(charge_cfg),
                   "lifecycle_timeframe": lifecycle_timeframe, "margin_multiplier": margin_multiplier,
                   "slippage_points": slippage_points, "costs_per_trade": costs_per_trade,
                   "max_shift_steps": max_shift_steps, "cap_stop_loss": cap_stop_loss, "stop_tolerance": stop_tolerance,
                   "fixed_margin": fixed_margin, "margin_method": margin_method},
        "data_source": {"provider": "breeze", "local_candle_cache": False,
                        "requests": fetcher.requests,
                        "non_trading_days_skipped": len(fetcher.skipped_days)},
        "months": [asdict(t) for t in trades],
        "summary": {**summarize(trades), "initial_capital": capital, "final_balance": balance},
    }


def save_report(report: Mapping[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return target


def to_ui_result(report: Mapping[str, Any], capital: float) -> dict[str, Any]:
    """Shape a runner report like the dashboard's backtest result payload.

    ``nifty_trades`` carries the per-month attributes (entry date/time, debit on
    downside, credit, ...) that the generic trade table has no columns for.
    """
    months = list(report["months"])
    closed = sorted((m for m in months if m["status"] == "CLOSED"), key=lambda m: m["exit_time"])
    equity, peak, max_dd = capital, capital, 0.0
    curve = [{"timestamp": report["params"]["start"], "value": capital}]
    trades = []
    for m in closed:
        equity += m["pnl_rupees"]
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100 if peak else 0.0)
        curve.append({"timestamp": m["exit_time"], "value": equity})
        trades.append({"trade_id": f"NBN-{m['month']}", "instrument": "NIFTY 1:-2:1 CE " + "/".join(map(str, m["strikes"].values())),
                       "quantity": m["lots"], "entry_time": m["entry_time"], "entry_price": m["net_premium_rupees"] or 0.0,
                       "exit_time": m["exit_time"], "exit_price": 0.0, "pnl": m["pnl_rupees"]})
    pnls = [m["pnl_rupees"] for m in closed]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
    total = sum(pnls)
    return {
        "strategy_id": "nifty_no_brainer", "initial_capital": capital, "final_capital": capital + total,
        "total_return": total, "total_return_pct": total / capital * 100 if capital else 0.0,
        "win_rate": len(wins) / len(closed) * 100 if closed else 0.0,
        "total_trades": len(closed), "winning_trades": len(wins), "losing_trades": len(losses),
        "max_drawdown": max_dd, "sharpe_ratio": 0.0,
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses else 0.0,
        "candles_evaluated": report["data_source"]["requests"],
        "period": f"{report['params']['start']} -> {report['params']['end']}",
        "equity_curve": curve, "trades": trades,
        "nifty_trades": months, "nifty_summary": report["summary"], "nifty_params": report["params"],
    }
