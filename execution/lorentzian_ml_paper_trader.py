"""Lorentzian Classification ML: live daily-bar PAPER-ONLY trading runner.

Same replay-from-scratch design as the other retrofitted paper traders:
every ``poll()`` call rebuilds a fresh
``strategies.lorentzian_ml.LorentzianMLStrategy`` instance and replays each
watched ticker's full available daily history through it, acting only on
signals newer than the last one already processed.

Unlike the other three strategies, ``LorentzianMLStrategy`` already keeps
its own per-instrument growing OHLC buffer and re-runs the KNN pipeline on
every ``on_candle`` call (that's how it works live *or* in a backtest), and
a single instance already partitions state by ``candle.instrument`` - so one
shared instance can watch every ticker in the same poll instead of one
instance per ticker.

No capital-based position sizing: the strategy itself only ever trades a
fixed ``quantity`` (default 1) per signal - there is no risk/capital
reference to compound here, unlike the other three strategies. The trader
still tracks a running ``balance`` (capital + cumulative realized P&L) for
reporting, it just never feeds back into sizing.

Signal shape note: entry/exit price is in ``signal.price`` directly (unlike
MCX Trend Rider). A trend reversal ("lorentzian_new_long" while already
short, or vice versa) arrives as a single new-side signal with no separate
exit signal for the old side - the strategy's own internal position counter
just flips. This trader detects that case (existing position with the
opposite side) and closes it at the reversal bar's price before opening the
new one, so a stale position can never linger in the ledger.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import platform_config
from platform_config import get_indices
from strategies.lorentzian_ml import LorentzianMLStrategy
from utils import Logger
from utils.timezone import ensure_ist, now_ist

DEFAULT_STATE_PATH = platform_config.FORWARD_TEST_DIR / "lorentzian_ml_paper.json"
DEFAULT_LOOKBACK_DAYS = 400


def default_tickers() -> List[str]:
    return [get_indices()[0]["symbol"]]  # "NSE:NIFTY"


@dataclass
class LorentzianTrade:
    ticker: str
    side: str  # "LONG" | "SHORT"
    quantity: int
    entry_date: str
    entry_price: float
    exit_date: str = ""
    exit_price: float = 0.0
    reason: str = ""
    pnl: float = 0.0
    status: str = "CLOSED"


class LorentzianMLPaperTrader:
    """One persisted paper-trading account across a watchlist of tickers."""

    def __init__(self, provider: Any, *, tickers: Optional[List[str]] = None,
                 capital: float = 100_000.0, params: Optional[Dict[str, Any]] = None,
                 state_path: Optional[Path] = None, lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                 strategy_factory: Callable[[Dict[str, Any]], Any] = None,
                 on_event: Optional[Callable[[str, dict], None]] = None):
        self.provider = provider
        self.tickers = list(tickers or default_tickers())
        self.capital = float(capital)
        self.params = dict(params or {})
        self.state_path = Path(state_path or DEFAULT_STATE_PATH)
        self.lookback_days = lookback_days
        self.strategy_factory = strategy_factory or (
            lambda params: LorentzianMLStrategy("lorentzian_ml_paper", "Lorentzian Classification ML", params))
        self.on_event = on_event
        self.logger = Logger("execution.lorentzian_ml_paper_trader")
        self._lock = threading.Lock()
        self.state: Dict[str, Any] = self._load_state()

    # -- persistence ------------------------------------------------------------
    def _default_state(self) -> Dict[str, Any]:
        return {
            "strategy_id": "lorentzian_ml", "mode": "PAPER",
            "capital": self.capital, "balance": self.capital,
            "tickers": self.tickers, "positions": {}, "trades": [],
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
        strat = self.strategy_factory(dict(self.params))
        for ticker in self.tickers:
            try:
                self._poll_ticker(strat, ticker, start, now)
            except Exception as e:
                self._emit("ticker_error", {"ticker": ticker, "error": str(e)})
                self.logger.warning(f"{ticker}: {e}")

    def _poll_ticker(self, strat: Any, ticker: str, start: datetime, now: datetime) -> None:
        rows = [c for c in self.provider.get_historical_candles(ticker, "1d", start, now) if c.timestamp <= now]
        if not rows:
            return
        last_date = self.state["last_processed_date"].get(ticker)
        for candle in rows:
            sig = strat.on_candle(candle)
            candle_date = candle.timestamp.date().isoformat()
            if sig is not None and (last_date is None or candle_date > last_date):
                self._apply_signal(ticker, sig, candle)
        self.state["last_processed_date"][ticker] = rows[-1].timestamp.date().isoformat()

    def _close_position(self, ticker: str, position: dict, exit_price: float,
                        exit_date: str, reason: str) -> None:
        sign = 1 if position["side"] == "LONG" else -1
        pnl = sign * (exit_price - position["entry_price"]) * position["quantity"]
        self.state["balance"] = float(self.state["balance"]) + pnl
        trade = LorentzianTrade(ticker=ticker, side=position["side"], quantity=position["quantity"],
                                entry_date=position["entry_date"], entry_price=position["entry_price"],
                                exit_date=exit_date, exit_price=exit_price, reason=reason, pnl=pnl)
        self.state["trades"].append(asdict(trade))
        self.state["positions"].pop(ticker, None)
        self._emit("exit", {"trade": asdict(trade), "balance": self.state["balance"]})
        self.logger.info(f"PAPER EXIT {ticker} {reason} pnl={pnl:.0f} balance={self.state['balance']:.0f}")

    def _apply_signal(self, ticker: str, sig: Any, candle: Any) -> None:
        reason = (sig.metadata or {}).get("reason", "")
        candle_date = candle.timestamp.date().isoformat()
        price = float(sig.price)
        existing = self.state["positions"].get(ticker)

        if reason.startswith("lorentzian_new"):
            new_side = "LONG" if reason.endswith("long") else "SHORT"
            if existing and existing["side"] != new_side:
                self._close_position(ticker, existing, price, candle_date, "reversed")
                existing = None
            if existing is None:
                position = {"ticker": ticker, "side": new_side, "quantity": int(sig.quantity),
                           "entry_price": price, "entry_date": candle_date}
                self.state["positions"][ticker] = position
                self._emit("entry", {"position": position, "balance": self.state["balance"]})
                self.logger.info(f"PAPER ENTRY {ticker} {new_side} {position['quantity']} @ {price:.2f}")
        elif reason.startswith("lorentzian_exit") and existing:
            self._close_position(ticker, existing, price, candle_date, reason)

    # -- read-only status ---------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        return dict(self.state)

    def stop(self) -> None:
        with self._lock:
            self.state["status"] = "STOPPED"
            self._save_state()


class LorentzianMLPaperSession:
    """Background daily polling loop around :class:`LorentzianMLPaperTrader`."""

    def __init__(self, trader: LorentzianMLPaperTrader, poll_interval_seconds: int = 3600):
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


__all__ = ["LorentzianTrade", "LorentzianMLPaperTrader", "LorentzianMLPaperSession",
          "default_tickers", "DEFAULT_STATE_PATH"]
