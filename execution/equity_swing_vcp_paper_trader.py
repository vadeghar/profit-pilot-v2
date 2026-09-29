"""Equity Swing VCP: live daily-bar PAPER-ONLY trading runner.

Design mirrors ``execution/four_indicator_paper_trader.py``: rather than
serializing the strategy's rich internal state (rolling closes/highs/lows,
per-symbol position, breakeven/partial flags), every ``poll()`` call
re-derives the current state by replaying a *fresh*
``strategies.equity_swing_vcp.EquitySwingVCPStrategy`` instance over each
watched symbol's full available daily history (benchmark candles fed first,
then the symbol's own) - exactly like a backtest, using the same code path.
Only signals from candles newer than the last one already processed are
acted on, so a restart or a slow/late poll can never double-enter or
retroactively trade a stale signal.

This is a *daily* strategy (1-3 month holds): ``poll()`` is meant to be
called once per trading day after the close, but is idempotent and safe to
call more often - a day already reflected in ``last_processed_date`` is
never re-acted upon.

Capital sizing mirrors the confirmed Four Indicator System behavior: each
poll's fresh strategy instance is constructed with the *current compounded
balance* as its position-sizing capital, so share counts scale with realized
P&L across the whole poll-to-poll lifetime (not a fixed initial-capital
reference). Between two entries inside the same poll's replay window the
sizing capital is fixed to that poll's starting balance - it does not
re-read balance mid-replay - since polls happen at least daily this tracks
the real account closely enough in practice.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import platform_config
from core.models import OrderSide
from market_data.rate_limiter import get_limiter
from platform_config import get_all_instruments, get_indices
from strategies.equity_swing_vcp import EquitySwingVCPStrategy
from utils import Logger
from utils.timezone import ensure_ist, now_ist

DEFAULT_STATE_PATH = platform_config.FORWARD_TEST_DIR / "equity_swing_vcp_paper.json"
DEFAULT_LOOKBACK_DAYS = 400  # >= 252 (52-week window) + buffer for SMA200 warmup
BENCHMARK_SYMBOL = get_indices()[0]["symbol"]  # "NSE:NIFTY"


def default_equity_universe(limit: int = 15) -> List[str]:
    return [i["symbol"] for i in get_all_instruments() if i.get("type") == "equity"][:limit]


@dataclass
class EquityTrade:
    symbol: str
    shares: int
    entry_date: str
    entry_price: float
    exit_date: str = ""
    exit_price: float = 0.0
    reason: str = ""
    pnl: float = 0.0
    status: str = "CLOSED"


class EquitySwingVCPPaperTrader:
    """One persisted paper-trading account across a watchlist of NSE symbols."""

    def __init__(self, provider: Any, *, symbols: Optional[List[str]] = None,
                 capital: float = 100_000.0, params: Optional[Dict[str, Any]] = None,
                 state_path: Optional[Path] = None, lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                 benchmark_symbol: str = BENCHMARK_SYMBOL,
                 strategy_factory: Callable[[Dict[str, Any]], Any] = None,
                 on_event: Optional[Callable[[str, dict], None]] = None):
        self.provider = provider
        self.symbols = list(symbols or default_equity_universe())
        self.capital = float(capital)
        self.params = dict(params or {})
        self.state_path = Path(state_path or DEFAULT_STATE_PATH)
        self.lookback_days = lookback_days
        self.benchmark_symbol = benchmark_symbol
        self.strategy_factory = strategy_factory or (
            lambda params: EquitySwingVCPStrategy("equity_swing_vcp_paper", "Equity Swing VCP", params))
        self.on_event = on_event
        self.logger = Logger("execution.equity_swing_vcp_paper_trader")
        self._lock = threading.Lock()
        self.state: Dict[str, Any] = self._load_state()

    # -- persistence ------------------------------------------------------------
    def _default_state(self) -> Dict[str, Any]:
        return {
            "strategy_id": "equity_swing_vcp", "mode": "PAPER",
            "capital": self.capital, "balance": self.capital,
            "symbols": self.symbols, "positions": {}, "trades": [],
            "last_processed_date": {}, "started_at": now_ist().isoformat(),
            "status": "RUNNING", "last_poll_at": None, "last_error": None,
        }

    def _load_state(self) -> Dict[str, Any]:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception:
                self.logger.warning(f"Could not parse existing state at {self.state_path}; starting fresh")
        return self._default_state()

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2, default=str), encoding="utf-8")

    def _emit(self, kind: str, payload: dict) -> None:
        if self.on_event:
            try:
                self.on_event(kind, payload)
            except Exception:
                pass

    # -- one evaluation step ------------------------------------------------------
    def poll(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Idempotent: replay each symbol's history, act on anything newer
        than what was already processed, persist, return status."""
        now = ensure_ist(now) if now else now_ist()
        with self._lock:
            try:
                self._poll_locked(now)
                self.state["last_error"] = None
            except Exception as exc:
                self.state["last_error"] = str(exc)
                self.logger.error(f"poll failed: {exc}")
            self.state["last_poll_at"] = now.isoformat()
            self._save_state()
            return self.status()

    def _poll_locked(self, now: datetime) -> None:
        start = now - timedelta(days=self.lookback_days)
        with get_limiter(getattr(self.provider, "name", "unknown")):
            bench_rows = self.provider.get_historical_candles(self.benchmark_symbol, "1d", start, now)
        for symbol in self.symbols:
            try:
                self._poll_symbol(symbol, bench_rows, start, now)
            except Exception as e:
                self._emit("symbol_error", {"symbol": symbol, "error": str(e)})
                self.logger.warning(f"{symbol}: {e}")

    def _is_benchmark(self, instrument: str) -> bool:
        inst = str(instrument).upper()
        return inst == self.benchmark_symbol.upper() or inst == self.benchmark_symbol.split(":", 1)[-1].upper()

    def _poll_symbol(self, symbol: str, bench_rows, start: datetime, now: datetime) -> None:
        with get_limiter(getattr(self.provider, "name", "unknown")):
            rows = [c for c in self.provider.get_historical_candles(symbol, "1d", start, now) if c.timestamp <= now]
        if not rows:
            return
        strat = self.strategy_factory({**self.params, "capital": self.state["balance"]})
        strat.initialize()
        bench_rows = [c for c in bench_rows if c.timestamp <= now]
        merged = sorted(list(bench_rows) + list(rows), key=lambda c: c.timestamp)
        last_date = self.state["last_processed_date"].get(symbol)
        for candle in merged:
            sig = strat.on_candle(candle)
            if self._is_benchmark(candle.instrument):
                continue
            candle_date = candle.timestamp.date().isoformat()
            if sig is not None and (last_date is None or candle_date > last_date):
                self._apply_signal(symbol, sig, candle)
        self.state["last_processed_date"][symbol] = rows[-1].timestamp.date().isoformat()

    def _apply_signal(self, symbol: str, sig: Any, candle: Any) -> None:
        candle_date = candle.timestamp.date().isoformat()
        if sig.action == OrderSide.BUY:
            position = {"symbol": symbol, "shares": int(sig.quantity), "entry_price": float(sig.price),
                       "entry_date": candle_date, "stop_price": float(sig.stop_loss or 0.0)}
            self.state["positions"][symbol] = position
            self._emit("entry", {"position": position, "balance": self.state["balance"]})
            self.logger.info(f"PAPER ENTRY {symbol} {position['shares']} sh @ {position['entry_price']:.2f}")
            return
        position = self.state["positions"].get(symbol)
        if not position:
            return  # a SELL with no tracked position: nothing to reconcile against
        qty = min(int(sig.quantity), position["shares"])
        pnl = (float(sig.price) - position["entry_price"]) * qty
        self.state["balance"] = float(self.state["balance"]) + pnl
        reason = (sig.metadata or {}).get("reason", "exit")
        trade = EquityTrade(symbol=symbol, shares=qty, entry_date=position["entry_date"],
                            entry_price=position["entry_price"], exit_date=candle_date,
                            exit_price=float(sig.price), reason=reason, pnl=pnl)
        self.state["trades"].append(asdict(trade))
        position["shares"] -= qty
        if position["shares"] <= 0:
            self.state["positions"].pop(symbol, None)
        self._emit("exit", {"trade": asdict(trade), "balance": self.state["balance"]})
        self.logger.info(f"PAPER EXIT {symbol} {reason} pnl={pnl:.0f} balance={self.state['balance']:.0f}")

    # -- read-only status ---------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        return dict(self.state)

    def stop(self) -> None:
        with self._lock:
            self.state["status"] = "STOPPED"
            self._save_state()


class EquitySwingVCPPaperSession:
    """Background daily polling loop around :class:`EquitySwingVCPPaperTrader`."""

    def __init__(self, trader: EquitySwingVCPPaperTrader, poll_interval_seconds: int = 3600):
        self.trader = trader
        self.poll_interval_seconds = poll_interval_seconds
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self.trader.state["status"] = "RUNNING"
        self.trader._save_state()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self.trader.poll()
            self._stop_event.wait(self.poll_interval_seconds)

    def stop(self, reason: str = "manual") -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.trader.state["status"] = "STOPPED"
        self.trader.state["stop_reason"] = reason
        self.trader._save_state()

    def status(self) -> Dict[str, Any]:
        return self.trader.status()


__all__ = ["EquityTrade", "EquitySwingVCPPaperTrader", "EquitySwingVCPPaperSession",
          "default_equity_universe", "DEFAULT_STATE_PATH", "BENCHMARK_SYMBOL"]
