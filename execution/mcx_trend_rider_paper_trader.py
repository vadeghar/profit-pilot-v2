"""MCX Trend Rider: live daily-bar PAPER-ONLY trading runner.

Same replay-from-scratch design as the Four Indicator System and Equity
Swing VCP paper traders: every ``poll()`` call rebuilds a fresh
``strategies.mcx_trend_rider.MCXTrendRiderStrategy`` instance and replays
each watched instrument's full daily history through it, acting only on
signals newer than the last one already processed. A restart or a slow
poll can never double-enter or retroactively trade a stale signal.

Capital sizing compounds: each poll's fresh strategy instance is built with
the current balance as its position-sizing capital.

Signal shape note: unlike the other two retrofitted strategies,
``MCXTrendRiderStrategy`` carries its entry/exit price in
``signal.metadata['entry_price'] / ['exit_price']`` (not ``signal.price``),
and both long and short entries/exits share ``OrderSide.BUY`` /
``OrderSide.SELL`` ambiguously (a SELL both opens a short and closes a
long) - the strategy's own ``reason`` metadata ("long_breakout_20d",
"short_stop_hit", ...) together with whether *this trader* already holds a
position for that instrument disambiguates entry vs. exit, mirroring how
the strategy's own internal state does it during a real backtest.

Known data-availability gap (documented, not fixed here): the only provider
with real MCX historical/live data is Angel One
(``market_data/angel_data_provider.py``), and its MCX_* symbol map is
pinned to specific expiry contracts (e.g. ``CRUDEOIL21SEP26FUT``) that go
stale every futures rollover. Using this paper trader against MCX
instruments requires that map to be kept current - it is not dynamically
resolved.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import platform_config
from strategies.mcx_trend_rider import COMMODITY_SPECS, MCXTrendRiderStrategy
from utils import Logger
from utils.timezone import ensure_ist, now_ist

DEFAULT_STATE_PATH = platform_config.FORWARD_TEST_DIR / "mcx_trend_rider_paper.json"
DEFAULT_LOOKBACK_DAYS = 400  # >= SMA200 warmup + buffer
DEFAULT_INSTRUMENTS = ["MCX_GOLDM", "MCX_SILVERM", "MCX_CRUDEOIL"]


@dataclass
class MCXTrade:
    instrument: str
    side: str  # "LONG" | "SHORT"
    lots: int
    entry_date: str
    entry_price: float
    exit_date: str = ""
    exit_price: float = 0.0
    reason: str = ""
    pnl: float = 0.0
    status: str = "CLOSED"


class MCXTrendRiderPaperTrader:
    """One persisted paper-trading account across a basket of MCX instruments."""

    def __init__(self, provider: Any, *, instruments: Optional[List[str]] = None,
                 capital: float = 100_000.0, params: Optional[Dict[str, Any]] = None,
                 state_path: Optional[Path] = None, lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                 strategy_factory: Callable[[Dict[str, Any]], Any] = None,
                 on_event: Optional[Callable[[str, dict], None]] = None):
        self.provider = provider
        self.instruments = list(instruments or DEFAULT_INSTRUMENTS)
        self.capital = float(capital)
        self.params = dict(params or {})
        self.state_path = Path(state_path or DEFAULT_STATE_PATH)
        self.lookback_days = lookback_days
        self.strategy_factory = strategy_factory or (
            lambda params: MCXTrendRiderStrategy("mcx_trend_rider_paper", "mcx_trend_rider", params))
        self.on_event = on_event
        self.logger = Logger("execution.mcx_trend_rider_paper_trader")
        self._lock = threading.Lock()
        self.state: Dict[str, Any] = self._load_state()

    # -- persistence ------------------------------------------------------------
    def _default_state(self) -> Dict[str, Any]:
        return {
            "strategy_id": "mcx_trend_rider", "mode": "PAPER",
            "capital": self.capital, "balance": self.capital,
            "instruments": self.instruments, "positions": {}, "trades": [],
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
        for instrument in self.instruments:
            try:
                self._poll_instrument(instrument, start, now)
            except Exception as e:
                self._emit("instrument_error", {"instrument": instrument, "error": str(e)})
                self.logger.warning(f"{instrument}: {e}")

    def _poll_instrument(self, instrument: str, start: datetime, now: datetime) -> None:
        rows = [c for c in self.provider.get_historical_candles(instrument, "1d", start, now)
               if c.timestamp <= now]
        if not rows:
            return
        strat = self.strategy_factory({**self.params, "capital": self.state["balance"]})
        strat.initialize()
        last_date = self.state["last_processed_date"].get(instrument)
        for candle in rows:
            sig = strat.on_candle(candle)
            candle_date = candle.timestamp.date().isoformat()
            if sig is not None and (last_date is None or candle_date > last_date):
                self._apply_signal(instrument, sig, candle)
        self.state["last_processed_date"][instrument] = rows[-1].timestamp.date().isoformat()

    def _apply_signal(self, instrument: str, sig: Any, candle: Any) -> None:
        candle_date = candle.timestamp.date().isoformat()
        reason = (sig.metadata or {}).get("reason", "")
        existing = self.state["positions"].get(instrument)
        if existing is None:
            side = "LONG" if reason.startswith("long") else "SHORT"
            entry_price = float(sig.metadata.get("entry_price", sig.price))
            position = {"instrument": instrument, "side": side, "lots": int(sig.quantity),
                       "entry_price": entry_price, "entry_date": candle_date,
                       "stop_loss": float(sig.metadata.get("stop_loss", 0.0))}
            self.state["positions"][instrument] = position
            self._emit("entry", {"position": position, "balance": self.state["balance"]})
            self.logger.info(f"PAPER ENTRY {instrument} {side} {position['lots']} lots @ {entry_price:.2f}")
            return
        exit_price = float(sig.metadata.get("exit_price", sig.price))
        point_value = COMMODITY_SPECS.get(instrument, {}).get("point_value", 1)
        sign = 1 if existing["side"] == "LONG" else -1
        pnl = sign * (exit_price - existing["entry_price"]) * existing["lots"] * point_value
        self.state["balance"] = float(self.state["balance"]) + pnl
        trade = MCXTrade(instrument=instrument, side=existing["side"], lots=existing["lots"],
                         entry_date=existing["entry_date"], entry_price=existing["entry_price"],
                         exit_date=candle_date, exit_price=exit_price, reason=reason, pnl=pnl)
        self.state["trades"].append(asdict(trade))
        self.state["positions"].pop(instrument, None)
        self._emit("exit", {"trade": asdict(trade), "balance": self.state["balance"]})
        self.logger.info(f"PAPER EXIT {instrument} {reason} pnl={pnl:.0f} balance={self.state['balance']:.0f}")

    # -- read-only status ---------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        return dict(self.state)

    def stop(self) -> None:
        with self._lock:
            self.state["status"] = "STOPPED"
            self._save_state()


class MCXTrendRiderPaperSession:
    """Background daily polling loop around :class:`MCXTrendRiderPaperTrader`."""

    def __init__(self, trader: MCXTrendRiderPaperTrader, poll_interval_seconds: int = 3600):
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


__all__ = ["MCXTrade", "MCXTrendRiderPaperTrader", "MCXTrendRiderPaperSession",
          "DEFAULT_INSTRUMENTS", "DEFAULT_STATE_PATH"]
