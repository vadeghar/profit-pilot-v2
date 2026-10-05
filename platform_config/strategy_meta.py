"""Strategy classification flags and status merged into the dashboard catalog.

strategy_flags.yaml (hand-maintained) holds, per strategy id, the filter flags shown as chips on the
dashboard and an optional ``status_override`` (active / experimental / deprecated) with its reason.
A strategy without an override is ``experimental``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_DIR = Path(__file__).resolve().parent
FLAGS_PATH = _DIR / "strategy_flags.yaml"
FLAG_GROUPS = ["horizon", "segment", "instrument", "direction", "bias", "hedging", "style"]
DEFAULT_STATUS = "experimental"


def load_flags() -> dict[str, dict[str, Any]]:
    if not FLAGS_PATH.exists():
        return {}
    return yaml.safe_load(FLAGS_PATH.read_text(encoding="utf-8")) or {}


def enrich_catalog(catalog: dict[str, dict[str, Any]]) -> None:
    """Attach flags and status to every catalog entry in place."""
    flags = load_flags()
    for sid, entry in catalog.items():
        f = flags.get(sid, {})
        entry["flags"] = {g: list(f.get(g, [])) for g in FLAG_GROUPS}
        entry["status"] = f.get("status_override") or DEFAULT_STATUS
        entry["status_reason"] = f.get("status_reason") or "paper trading - not yet proven"
