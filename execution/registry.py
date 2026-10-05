"""Single registry of every live/paper strategy runner, across all strategies.

Complements the per-strategy session dicts in ``web_app.py``
(``OI_PAPER_SESSIONS``, ``SCALP_PAPER_SESSIONS``) with one place to list what's running, check its health, and
stop everything - the foundation for running multiple strategies
concurrently and for one systemd unit per strategy later: whichever process
owns a session, this registry is where "what's running right now" and
"stop everything" live.

Every session object registered here must expose ``.status() -> dict`` and
``.stop(reason: str = ...) -> None``; that's the entire contract - the
registry doesn't otherwise care what kind of session it is, and doesn't own
the per-strategy dicts either (web_app.py keeps those for its own
strategy-specific endpoints; it just also registers/unregisters here).
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from utils.timezone import now_ist


def _is_running(status: Dict[str, Any]) -> bool:
    """Session status shapes vary (OIPaperSession uses "running": bool; the
    scalper sessions use "status": "RUNNING"/"STOPPED") - normalize both."""
    if isinstance(status.get("running"), bool):
        return status["running"]
    return status.get("status") == "RUNNING"


class RunnerRegistry:
    _lock = threading.Lock()
    _sessions: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def _slot(cls, strategy_id: str, key: str) -> str:
        return f"{strategy_id}:{key}"

    @classmethod
    def register(cls, strategy_id: str, key: str, session: Any) -> None:
        with cls._lock:
            cls._sessions[cls._slot(strategy_id, key)] = {
                "strategy_id": strategy_id, "key": key, "session": session,
                "registered_at": now_ist().isoformat(),
            }

    @classmethod
    def unregister(cls, strategy_id: str, key: str) -> None:
        with cls._lock:
            cls._sessions.pop(cls._slot(strategy_id, key), None)

    @classmethod
    def get(cls, strategy_id: str, key: str) -> Optional[Any]:
        with cls._lock:
            entry = cls._sessions.get(cls._slot(strategy_id, key))
        return entry["session"] if entry else None

    @classmethod
    def list_all(cls) -> List[Dict[str, Any]]:
        with cls._lock:
            entries = list(cls._sessions.values())
        out = []
        for entry in entries:
            try:
                status = entry["session"].status()
            except Exception as e:
                status = {"error": f"status() failed: {e}"}
            out.append({"strategy_id": entry["strategy_id"], "key": entry["key"],
                       "registered_at": entry["registered_at"], "running": _is_running(status),
                       "detail": status})
        return out

    @classmethod
    def running_count(cls) -> int:
        return sum(1 for s in cls.list_all() if s["running"])

    @classmethod
    def stop_all(cls, reason: str = "stop_all") -> List[Dict[str, Any]]:
        with cls._lock:
            entries = list(cls._sessions.values())
        results = []
        for entry in entries:
            session = entry["session"]
            try:
                try:
                    session.stop(reason)
                except TypeError:
                    session.stop()
                cls.unregister(entry["strategy_id"], entry["key"])
                results.append({"strategy_id": entry["strategy_id"], "key": entry["key"], "stopped": True})
            except Exception as e:
                results.append({"strategy_id": entry["strategy_id"], "key": entry["key"],
                               "stopped": False, "error": str(e)})
        return results


__all__ = ["RunnerRegistry"]
