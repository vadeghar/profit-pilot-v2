"""Bar-by-bar execution simulator for the strategy audit.

Drives a platform strategy (StrategyBase.on_candle) over real candles and
books fills with an itemised cost model (commission / spread / slippage).

Semantics mirror the platform BacktestEngine: a BUY while flat opens a long,
a SELL while flat opens a short, an opposite-side signal closes the open
position (a ``partial_profit_take`` closes only the signalled quantity) and a
same-side signal is ignored. ``semantics="target"`` is used for strategies
that manage their own position state and signal reversals directly
(Lorentzian: a new long while short means close-and-reverse).

Fills happen at the signal bar's close unless the strategy supplies its own
price (stop levels); a stop price is gap-adjusted - a long stopped at S fills
at min(S, open) because an open below the stop fills at the open.

Warm-up: bars before ``window_start`` are fed so indicators are primed, and
any position opened before ``window_start`` is tracked (so the strategy's own
state stays in sync) but excluded from P&L, trades and the equity curve.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from core.models import Candle, OrderSide
from tools.strategy_audit.costs import commission, friction


@dataclass
class Position:
    side: int            # +1 long / -1 short
    qty: float
    price: float
    ts: datetime
    reason: str
    counted: bool


@dataclass
class TradeRec:
    instrument: str
    side: str
    qty: float
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    entry_reason: str
    exit_reason: str
    notional: float
    gross_pnl: float
    commission: float
    spread: float
    slippage: float
    net_pnl: float
    holding_days: float
    intraday: bool


@dataclass
class Account:
    capital: float
    segment_of: Callable[[str], str]
    point_value_of: Callable[[str], float] = lambda _i: 1.0
    cash: float = field(init=False)
    positions: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)
    last_px: dict = field(default_factory=dict)
    equity: list = field(default_factory=list)

    def __post_init__(self):
        self.cash = self.capital

    def unrealized(self) -> float:
        u = 0.0
        for inst, p in self.positions.items():
            if p.counted:
                u += p.side * (self.last_px.get(inst, p.price) - p.price) * p.qty * self.point_value_of(inst)
        return u

    def open(self, inst: str, side: int, qty: float, price: float, ts: datetime, reason: str, counted: bool):
        if qty > 0:
            self.positions[inst] = Position(side, qty, price, ts, reason, counted)

    def close(self, inst: str, qty: float, price: float, ts: datetime, reason: str) -> Optional[TradeRec]:
        p = self.positions[inst]
        qty = min(qty, p.qty)
        p.qty -= qty
        if p.qty <= 1e-9:
            del self.positions[inst]
        if not p.counted:
            return None
        pv = self.point_value_of(inst)
        seg = self.segment_of(inst)
        intraday = p.ts.date() == ts.date()
        entry_to, exit_to = p.price * qty * pv, price * qty * pv
        buy_first = p.side > 0
        comm = (commission(seg, "BUY" if buy_first else "SELL", entry_to, p.ts.date(), intraday=intraday)
                + commission(seg, "SELL" if buy_first else "BUY", exit_to, ts.date(), intraday=intraday))
        s1, l1 = friction(seg, entry_to)
        s2, l2 = friction(seg, exit_to)
        gross = p.side * (price - p.price) * qty * pv
        net = gross - comm - s1 - s2 - l1 - l2
        self.cash += net
        rec = TradeRec(inst, "LONG" if p.side > 0 else "SHORT", qty, p.ts.isoformat(), p.price, ts.isoformat(),
                       price, p.reason, reason, entry_to, gross, comm, s1 + s2, l1 + l2, net,
                       (ts - p.ts).total_seconds() / 86400, intraday)
        self.trades.append(rec)
        return rec


def _reason(sig) -> str:
    return str((getattr(sig, "metadata", None) or {}).get("reason") or getattr(sig.action, "value", sig.action)).split(" (")[0]


def _explicit_price(sig) -> Optional[float]:
    md = getattr(sig, "metadata", None) or {}
    for key in ("exit_price", "entry_price"):
        if md.get(key) is not None:
            return float(md[key])
    return float(sig.price) if getattr(sig, "price", None) else None


def run(strategy, bars: dict[str, list[Candle]], *, capital: float, window_start: datetime,
        segment_of: Callable[[str], str], point_value_of: Callable[[str], float] = lambda _i: 1.0,
        sizing: str = "all_in", semantics: str = "engine", long_only: bool = False,
        equity_lot: int = 5, fractional: Callable[[str], bool] = lambda _i: False,
        feed_only: set[str] = frozenset(), window_end: Optional[datetime] = None) -> Account:
    """Simulate one account. ``bars`` is {instrument: candles}; instruments in
    ``feed_only`` (e.g. a regime benchmark) are fed to the strategy first on
    each timestamp but never traded."""
    acct = Account(capital, segment_of, point_value_of)
    events = sorted(((c.timestamp, 0 if inst in feed_only else 1, inst, c)
                     for inst, cs in bars.items() for c in cs), key=lambda e: (e[0], e[1], e[2]))
    last_ts = None
    for ts, _, inst, candle in events:
        if window_end and ts > window_end:
            break
        if last_ts is not None and ts != last_ts and last_ts >= window_start:
            acct.equity.append((last_ts, acct.cash + acct.unrealized()))
        last_ts = ts
        acct.last_px[inst] = candle.close
        sig = strategy.on_candle(candle)
        if sig is None or inst in feed_only:
            continue
        _apply(acct, strategy, sig, candle, ts >= window_start, sizing, semantics, long_only,
               equity_lot, fractional(inst))
    if last_ts is not None:
        acct.equity.append((last_ts, acct.cash + acct.unrealized()))
    # Force-close whatever is still open at the end of the window at the last close.
    for inst in list(acct.positions):
        px = acct.last_px[inst]
        acct.close(inst, acct.positions[inst].qty, px, last_ts, "backtest_end")
    if acct.equity:
        acct.equity[-1] = (acct.equity[-1][0], acct.cash)
    return acct


def _fill_price(sig, candle: Candle, closing_side: int) -> float:
    p = _explicit_price(sig)
    if p is None or p <= 0 or p == candle.close:
        return candle.close
    # Any other strategy-supplied exit price is a stop level: an open beyond it fills at the open.
    return min(p, candle.open) if closing_side > 0 else max(p, candle.open)


def _apply(acct: Account, strategy, sig, candle: Candle, counted: bool, sizing: str, semantics: str,
           long_only: bool, equity_lot: int, fractional: bool) -> None:
    inst = candle.instrument
    action = 1 if sig.action == OrderSide.BUY else -1
    reason = _reason(sig)
    pos = acct.positions.get(inst)

    if semantics == "target":
        target = {"lorentzian_new_long": 1, "lorentzian_new_short": -1}.get(reason, 0)
        if pos is not None and (target == 0 or target != pos.side):
            acct.close(inst, pos.qty, _fill_price(sig, candle, pos.side), candle.timestamp, reason)
            pos = None
        if target != 0 and pos is None:
            _open(acct, strategy, sig, candle, target, counted, sizing, equity_lot, fractional, reason)
        return

    if pos is None:
        if long_only and action < 0:
            return
        _open(acct, strategy, sig, candle, action, counted, sizing, equity_lot, fractional, reason)
    elif action != pos.side:
        qty = float(sig.quantity) if reason == "partial_profit_take" else pos.qty
        acct.close(inst, qty, _fill_price(sig, candle, pos.side), candle.timestamp, reason)


def _open(acct: Account, strategy, sig, candle: Candle, side: int, counted: bool, sizing: str,
          equity_lot: int, fractional: bool, reason: str) -> None:
    inst = candle.instrument
    price = _explicit_price(sig) or candle.close
    pv = acct.point_value_of(inst)
    if sizing == "all_in":
        budget = acct.cash / (1 + 0.003)  # leave room for entry costs
        qty = budget / (price * pv)
        qty = qty if fractional else math.floor(qty / equity_lot) * equity_lot
    else:
        qty = float(sig.quantity)
        if sizing == "signal_cash":  # cash segment: cannot spend more than free cash
            committed = sum(p.price * p.qty for p in acct.positions.values())
            free = acct.cash - committed
            qty = min(qty, math.floor(max(free, 0) / (price * pv)))
    if qty <= 0:
        return
    acct.open(inst, side, qty, price, candle.timestamp, reason, counted)
    hook = getattr(strategy, "on_entry_fill", None)
    if callable(hook) and not fractional:
        hook(inst, int(qty), price)  # keep the strategy's own share/lot bookkeeping in sync with the fill


# ------------------------------------------------------------------ metrics ---
def metrics(acct: Account, window_start: datetime, window_end: datetime) -> dict[str, Any]:
    trades = acct.trades
    nets = [t.net_pnl for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    gross = sum(t.gross_pnl for t in trades)
    comm = sum(t.commission for t in trades)
    spread = sum(t.spread for t in trades)
    slip = sum(t.slippage for t in trades)
    net = sum(nets)
    eq = [v for _, v in acct.equity] or [acct.capital]
    peak, mdd, mdd_abs = acct.capital, 0.0, 0.0
    for v in [acct.capital] + eq:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
            mdd_abs = max(mdd_abs, peak - v)
    years = max((window_end - window_start).days / 365.25, 1e-9)
    final = acct.capital + net
    cagr = (final / acct.capital) ** (1 / years) - 1 if final > 0 else -1.0
    rets = [t.net_pnl / t.notional for t in trades if t.notional]
    gw, gl = sum(wins), -sum(losses)
    long_net = sum(t.net_pnl for t in trades if t.side == "LONG")
    short_net = sum(t.net_pnl for t in trades if t.side == "SHORT")
    gross_wins = sum(t.gross_pnl for t in trades if t.gross_pnl > 0)
    gross_losses = -sum(t.gross_pnl for t in trades if t.gross_pnl <= 0)
    return {
        "capital": acct.capital, "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "gross_pnl": gross, "commission": comm, "spread": spread, "slippage": slip,
        "costs": comm + spread + slip, "net_pnl": net, "return_pct": net / acct.capital,
        "cagr": cagr, "max_dd_pct": mdd, "max_dd_abs": mdd_abs,
        "profit_factor": (gw / gl) if gl > 0 else (math.inf if gw > 0 else 0.0),
        "gross_profit_factor": (gross_wins / gross_losses) if gross_losses > 0 else (math.inf if gross_wins > 0 else 0.0),
        "expectancy": net / len(trades) if trades else 0.0,
        "expectancy_pct": sum(rets) / len(rets) if rets else 0.0,
        "avg_win": gw / len(wins) if wins else 0.0, "avg_loss": -gl / len(losses) if losses else 0.0,
        "avg_hold_days": sum(t.holding_days for t in trades) / len(trades) if trades else 0.0,
        "intraday_share": sum(t.intraday for t in trades) / len(trades) if trades else 0.0,
        "long_net": long_net, "short_net": short_net,
        "long_trades": sum(t.side == "LONG" for t in trades), "short_trades": sum(t.side == "SHORT" for t in trades),
        "net_pnl_2x_friction": net - spread - slip,
    }


def merge(accounts: list[Account]) -> Account:
    """Combine independent sleeves into one book (sum of capital, trades, equity)."""
    total = Account(sum(a.capital for a in accounts), accounts[0].segment_of, accounts[0].point_value_of)
    total.trades = sorted((t for a in accounts for t in a.trades), key=lambda t: t.exit_time)
    series = [dict(a.equity) for a in accounts]
    stamps = sorted({ts for s in series for ts in s})
    last = [a.capital for a in accounts]
    eq = []
    for ts in stamps:
        for i, s in enumerate(series):
            if ts in s:
                last[i] = s[ts]
        eq.append((ts, sum(last)))
    total.equity = eq
    total.cash = total.capital + sum(t.net_pnl for t in total.trades)
    return total


def trades_as_dicts(acct: Account) -> list[dict]:
    return [asdict(t) for t in acct.trades]
