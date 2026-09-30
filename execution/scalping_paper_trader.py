"""Background PAPER session for one scalping strategy (no real orders, ever).

Each session owns one ScalpEngine on its own thread, fed from the shared tick
hub through a queue, so the five scalpers run independently. It keeps running
across days: outside market hours it simply waits; the hub's scheduler starts
recording at 09:12 IST and ticks resume. Balance and the full trade log are
persisted to data/forward_test/scalping/<strategy_id>.json after every trade.
"""
from __future__ import annotations

import json
import queue
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

import platform_config
from market_data.tick_recorder import TickHub, get_hub
from market_data.tick_store import Instrument, Tick
from strategies.scalping import SCALP_STRATEGIES
from utils.timezone import now_ist

STATE_DIR = Path(platform_config.FORWARD_TEST_DIR) / "scalping"


class ScalpPaperSession:
    def __init__(self, strategy_id: str, capital: float = 50_000.0, overrides: Optional[dict] = None,
                 hub: Optional[TickHub] = None, state_dir: Optional[Path] = None):
        self.strategy_id = strategy_id
        self.cls = SCALP_STRATEGIES[strategy_id]
        self.hub = hub or get_hub()
        self.path = Path(state_dir or STATE_DIR) / f"{strategy_id}.json"
        self.state = self._load(capital)
        self.overrides = dict(overrides or {})
        cfg = self.cls.default_config().update(self.overrides)
        self.engine = self.cls({}, capital=self.state["balance"], config=cfg, on_event=self._on_engine_event)
        self.engine.capital = self.state["capital"]
        self._q: queue.Queue = queue.Queue(maxsize=500_000)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._sub: Optional[int] = None
        self.status_text = "STOPPED"
        self.stop_reason: Optional[str] = None
        self.started_at: Optional[str] = None
        self.last_error: Optional[str] = None
        self.dropped = 0

    # ----------------------------------------------------------- persistence
    def _load(self, capital: float) -> dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        return {"strategy_id": self.strategy_id, "capital": float(capital), "balance": float(capital), "trades": []}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, default=str), encoding="utf-8")
        tmp.replace(self.path)

    def _on_engine_event(self, kind: str, payload: dict) -> None:
        if kind == "exit":
            self.state["trades"].append(payload["trade"])
            self.state["balance"] = payload["balance"]
            self._save()

    # --------------------------------------------------------------- control
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._sub = self.hub.subscribe(self._enqueue, self._on_instruments)
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"scalp-{self.strategy_id}")
        self._thread.start()
        self.status_text, self.stop_reason, self.started_at = "RUNNING", None, now_ist().isoformat()
        self.state["running"] = True  # resumed automatically after a service restart
        self.state["overrides"] = self.overrides
        self._save()
        err = self.hub.ensure_recording()
        if err:
            self.last_error = f"tick recorder: {err}"

    def stop(self, reason: str = "manual") -> None:
        self._stop.set()
        if self._sub is not None:
            self.hub.unsubscribe(self._sub)
            self._sub = None
        if self._thread:
            self._thread.join(timeout=5)
        with self._lock:
            if self.engine.pos and self.engine.last_tick_at:
                self.engine.finish(self.engine.last_tick_at)
        self.status_text, self.stop_reason = "STOPPED", reason
        self.state["running"] = reason == "shutdown"  # a service shutdown is not a user stop
        self._save()

    # ------------------------------------------------------------ tick path
    def _enqueue(self, tick: Tick) -> None:
        try:
            self._q.put_nowait(("tick", tick))
        except queue.Full:
            self.dropped += 1

    def _on_instruments(self, insts: dict[str, Instrument]) -> None:
        self._q.put(("instruments", insts))

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                kind, item = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                with self._lock:
                    if kind == "instruments":
                        self.engine.add_instruments(item)
                    else:
                        self.engine.on_tick(item)
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"

    # ---------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        with self._lock:
            snap = self.engine.snapshot()
        return {
            "status": self.status_text, "strategy_id": self.strategy_id, "name": self.cls.name,
            "live_trading": False, "capital": self.state["capital"], "balance": round(self.state["balance"], 2),
            "position": snap["position"], "trades": self.state["trades"], "today": snap["day"],
            "day_trades": snap["day_trades"], "day_losses": snap["day_losses"], "ticks": snap["ticks"],
            "skipped": snap["skipped"], "last_skips": snap["last_skips"],
            "last_tick_at": snap["last_tick_at"], "queue": self._q.qsize(), "dropped": self.dropped,
            "started_at": self.started_at, "stop_reason": self.stop_reason, "last_error": self.last_error,
            "config": snap["config"], "recorder": self.hub.status(),
        }


def sessions_to_resume(state_dir: Optional[Path] = None) -> list[dict]:
    """Persisted sessions that were running when the process last stopped."""
    folder = Path(state_dir or STATE_DIR)
    out = []
    for sid in SCALP_STRATEGIES:
        st = persisted_status(sid, folder)
        if st and st.get("running"):
            out.append({"strategy_id": sid, "capital": st.get("capital", 50_000.0),
                        "overrides": st.get("overrides") or {}})
    return out


def persisted_status(strategy_id: str, state_dir: Optional[Path] = None) -> Optional[dict]:
    path = Path(state_dir or STATE_DIR) / f"{strategy_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
