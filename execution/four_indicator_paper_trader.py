"""Dedicated live paper-trading runner for the Four Indicator System.

Design: rather than maintaining a second, parallel implementation of the
entry/exit/strike-selection rules for live trading, every ``poll()`` call
re-derives the current signal state by replaying
``strategies.four_indicator_system.FourIndicatorSignalEngine`` (the exact
same engine the real backtest uses) over a short rolling window of real
Breeze candles ending "now". This makes live and backtest provably
identical rule-for-rule; only the *data source* differs (a rolling live
fetch instead of a fixed historical range).

``poll()`` is idempotent and safe to call repeatedly (e.g. every 60s during
market hours): it only acts on engine events whose timestamp is newer than
the last one it already processed, and only from the moment the session was
started (it will never retroactively "paper trade" a signal from before
deployment). State - capital, compounded balance, open trade, trade log -
is persisted to disk so the process can be restarted without losing
position or double-entering.

Capital sizing mirrors the backtest exactly: lots are recomputed at every
entry from the current (compounded) ``balance // capital_per_lot`` - not a
fixed lot count - so position size grows and shrinks with the paper
account's realized P&L, per the user's explicit requirement.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import platform_config
from backtest.charges import ChargeConfig, Fill, option_charges
from backtest.four_indicator_backtest import (
    DEFAULT_LOT_SIZE, SQUARE_OFF_TIME, _premium_at, find_strike_for_target_premium,
    resolve_weekly_expiry,
)
from market_data.trading_days import SESSION_CLOSE, TradingCalendar, TradingDayFetcher
from strategies.four_indicator_system import FourIndicatorConfig, FourIndicatorSignalEngine
from utils import Logger
from utils.timezone import EQUITY_CLOSE_MIN, EQUITY_OPEN_MIN, IST, ensure_ist, ist_minutes, now_ist

DEFAULT_STATE_PATH = platform_config.FORWARD_TEST_DIR / "four_indicator_system_paper.json"
DEFAULT_LOOKBACK_DAYS = 5  # enough for SuperTrend/RSI/Bollinger warmup + yesterday's pivot


def is_market_hours_ist(now: Optional[datetime] = None) -> bool:
    now = now or now_ist()
    return EQUITY_OPEN_MIN <= ist_minutes(now) <= EQUITY_CLOSE_MIN


@dataclass
class PaperTrade:
    entry_date: str
    entry_time: str
    expiry: str
    strike: int
    side: str
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
    flags: List[str] = field(default_factory=list)


class FourIndicatorPaperTrader:
    """One persisted paper-trading account for the Four Indicator System."""

    def __init__(self, provider: Any, *, capital: float = 100_000.0, capital_per_lot: float = 50_000.0,
                 target_premium_pct: float = 0.01, lot_size: Optional[int] = None,
                 config: Optional[FourIndicatorConfig] = None, charges: Optional[ChargeConfig] = None,
                 state_path: Optional[Path] = None, calendar: Optional[TradingCalendar] = None,
                 lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                 on_event: Optional[Callable[[str, dict], None]] = None):
        self.provider = provider
        self.capital = float(capital)
        self.capital_per_lot = float(capital_per_lot)
        self.target_premium_pct = float(target_premium_pct)
        self.lot_size = int(lot_size or DEFAULT_LOT_SIZE)
        self.config = config or FourIndicatorConfig()
        self.charge_cfg = charges if charges is not None else ChargeConfig()
        self.state_path = Path(state_path or DEFAULT_STATE_PATH)
        self.calendar = calendar or TradingCalendar()
        self.lookback_days = lookback_days
        self.on_event = on_event
        self.logger = Logger("execution.four_indicator_paper_trader")
        self._lock = threading.Lock()
        self.state: Dict[str, Any] = self._load_state()

    # -- persistence ----------------------------------------------------------
    def _default_state(self) -> Dict[str, Any]:
        return {
            "strategy_id": "four_indicator_system",
            "mode": "PAPER",
            "capital": self.capital,
            "balance": self.capital,
            "capital_per_lot": self.capital_per_lot,
            "target_premium_pct": self.target_premium_pct,
            "lot_size": self.lot_size,
            "started_at": now_ist().isoformat(),
            "last_processed_ts": None,
            "open_trade": None,
            "trades": [],
            "status": "RUNNING",
            "last_poll_at": None,
            "last_error": None,
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

    # -- one evaluation step ----------------------------------------------------
    def poll(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Idempotent: fetch recent candles, replay the signal engine, act on
        anything newer than what was already processed, persist, return status."""
        now = ensure_ist(now or now_ist())
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
        fetcher = TradingDayFetcher(self.provider, self.calendar, throttle_seconds=0.3, retries=1)
        fetch_start = self.calendar.previous_trading_day(now.date() - timedelta(days=1))
        seen = 0
        while seen < self.lookback_days and fetch_start > now.date() - timedelta(days=self.lookback_days * 4 + 10):
            if self.calendar.is_trading_day(fetch_start):
                seen += 1
            if seen >= self.lookback_days:
                break
            fetch_start = self.calendar.previous_trading_day(fetch_start - timedelta(days=1))

        rows = fetcher.fetch("NSE:NIFTY", self.config.timeframe,
                             datetime.combine(fetch_start, time(9, 15), IST), now)
        engine = FourIndicatorSignalEngine(self.config)
        started_at = self.state.get("started_at")
        last_ts = self.state.get("last_processed_ts")
        cutoff = max(t for t in (started_at, last_ts) if t) if (started_at or last_ts) else None

        for candle in rows:
            result = engine.process(candle)
            if not result:
                continue
            ts = candle.timestamp
            if cutoff and ts.isoformat() <= cutoff:
                continue
            if result["action"] == "ENTER":
                self._handle_entry(fetcher, result, ts)
            elif result["action"] == "EXIT" and self.state.get("open_trade"):
                self._handle_exit(fetcher, result, ts, reason="supertrend_flip")
            self.state["last_processed_ts"] = ts.isoformat()
            cutoff = ts.isoformat()

        self._maybe_square_off(fetcher, now)

    def _handle_entry(self, fetcher: TradingDayFetcher, result: dict, ts: datetime) -> None:
        if self.state.get("open_trade"):
            return  # already holding a position: never stack entries
        side = result["side"]
        expiry = resolve_weekly_expiry(ts.date(), self.calendar)
        found = find_strike_for_target_premium(fetcher, expiry, result["price"], ts, side,
                                               self.target_premium_pct)
        if found is None:
            self._emit("entry_skipped", {"time": ts.isoformat(), "side": side, "reason": "no_option_data"})
            return
        strike, premium = found
        balance = float(self.state["balance"])
        lots = max(1, int(balance // self.capital_per_lot))
        trade = PaperTrade(
            entry_date=ts.date().isoformat(), entry_time=ts.isoformat(), expiry=expiry.isoformat(),
            strike=strike, side=side, entry_spot=result["price"], entry_premium=premium,
            lots=lots, quantity=lots * self.lot_size, indicators_at_entry=result["indicators"],
        )
        self.state["open_trade"] = asdict(trade)
        self._emit("entry", {"trade": asdict(trade), "balance": balance})
        self.logger.info(f"PAPER ENTRY {side} {strike} @ {premium:.2f} x{trade.quantity} "
                         f"(lots={lots}, balance={balance:.0f})")

    def _close(self, exit_ts: datetime, exit_premium: Optional[float], reason: str) -> None:
        open_trade = self.state.get("open_trade")
        if not open_trade:
            return
        if exit_premium is None:
            open_trade.setdefault("flags", []).append("NO_EXIT_PRICE_CARRIED_ENTRY_PREMIUM")
            exit_premium = open_trade["entry_premium"]
        gross = (exit_premium - open_trade["entry_premium"]) * open_trade["quantity"]
        fills = [Fill("BUY", open_trade["entry_premium"], open_trade["quantity"]),
                Fill("SELL", exit_premium, open_trade["quantity"])]
        charges = option_charges(fills, exit_ts.date(), self.charge_cfg)
        open_trade.update(
            exit_time=exit_ts.isoformat(), exit_premium=exit_premium, exit_reason=reason,
            gross_pnl=gross, charges_total=charges["total"], pnl=gross - charges["total"],
            status="CLOSED",
        )
        self.state["balance"] = float(self.state["balance"]) + open_trade["pnl"]
        self.state["trades"].append(open_trade)
        self.state["open_trade"] = None
        self._emit("exit", {"trade": open_trade, "balance": self.state["balance"]})
        self.logger.info(f"PAPER EXIT {reason} pnl={open_trade['pnl']:.0f} "
                         f"balance={self.state['balance']:.0f}")

    def _handle_exit(self, fetcher: TradingDayFetcher, result: dict, ts: datetime, reason: str) -> None:
        open_trade = self.state["open_trade"]
        expiry = date.fromisoformat(open_trade["expiry"])
        premium = _premium_at(fetcher, expiry, open_trade["strike"], open_trade["side"], ts)
        self._close(ts, premium, reason)

    def _maybe_square_off(self, fetcher: TradingDayFetcher, now: datetime) -> None:
        open_trade = self.state.get("open_trade")
        if not open_trade:
            return
        entry_day = date.fromisoformat(open_trade["entry_date"])
        if now.date() != entry_day:
            return  # a later poll on a subsequent day still finds it open; leave for the engine/manual review
        if ist_minutes(now) < SQUARE_OFF_TIME.hour * 60 + SQUARE_OFF_TIME.minute:
            return
        expiry = date.fromisoformat(open_trade["expiry"])
        premium = _premium_at(fetcher, expiry, open_trade["strike"], open_trade["side"], now)
        self._close(now, premium, "intraday_square_off")

    # -- read-only status -------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        return dict(self.state)

    def stop(self) -> None:
        with self._lock:
            self.state["status"] = "STOPPED"
            self._save_state()


class FourIndicatorPaperSession:
    """Background polling loop around :class:`FourIndicatorPaperTrader`.

    Polls only during NSE equity market hours; sleeps the rest of the time so
    it can be left running continuously without hammering Breeze off-hours.
    """

    def __init__(self, trader: FourIndicatorPaperTrader, poll_interval_seconds: int = 60):
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
            if is_market_hours_ist():
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


__all__ = ["PaperTrade", "FourIndicatorPaperTrader", "FourIndicatorPaperSession",
          "is_market_hours_ist", "DEFAULT_STATE_PATH"]
