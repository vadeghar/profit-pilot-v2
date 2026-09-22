"""Central configuration loader — universe, strategy defaults, paths."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# ---------------------------------------------------------------------------
# Project root — this file is config/__init__.py → parent is project root
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_PATH = PROJECT_ROOT / "config" / "universe.yaml"


def _load_yaml(path: Path) -> dict:
    """Load and return parsed YAML, raising on missing/invalid file."""
    if not path.exists():
        raise FileNotFoundError(f"Universe config not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping at {path}, got {type(data).__name__}")
    return data


def load_universe() -> dict:
    """Return the full universe config dict (cached on first call)."""
    return _load_yaml(UNIVERSE_PATH)


# ---------------------------------------------------------------------------
# Cached helpers — import these directly from config wherever needed
# ---------------------------------------------------------------------------

_universe_cache: Optional[dict] = None


def _get_universe() -> dict:
    global _universe_cache
    if _universe_cache is None:
        _universe_cache = load_universe()
    return _universe_cache


def get_indices() -> List[dict]:
    """Return list of index entries [{symbol, label, exchange, type, description}, ...]."""
    return _get_universe().get("indices", [])


def get_equities() -> List[dict]:
    """Return list of equity entries [{symbol, label, exchange, type, description}, ...]."""
    return _get_universe().get("equities", [])


def get_all_instruments() -> List[dict]:
    """Return indices + equities combined (the global dropdown universe)."""
    return get_indices() + get_equities()


def get_strategy_defaults(strategy_id: str) -> Dict[str, Any]:
    """Return per-strategy default config dict (instruments, description, ...)."""
    return _get_universe().get("strategy_defaults", {}).get(strategy_id, {})


def get_strategy_instruments(strategy_id: str) -> List[str]:
    """Return the default instrument symbol list for a given strategy id."""
    return get_strategy_defaults(strategy_id).get("instruments", [])


def symbol_to_label(symbol: str) -> str:
    """Human-readable label for a symbol, falling back to the symbol itself."""
    for entry in get_all_instruments():
        if entry.get("symbol", "").upper() == symbol.upper():
            return entry.get("label", symbol)
    return symbol


def label_for_dropdown(symbol: str) -> str:
    """Build a dropdown label like 'Reliance Industries (NSE:RELIANCE)'."""
    entry = None
    for e in get_all_instruments():
        if e.get("symbol", "").upper() == symbol.upper():
            entry = e
            break
    if entry:
        return f"{entry.get('label', symbol)} ({symbol})"
    return f"{symbol_to_label(symbol)} ({symbol})"


def build_dropdown_list(instrument_list: Optional[List[str]] = None) -> List[Dict[str, str]]:
    """
    Build the [{'label': ..., 'value': ...}, ...] dropdown structure from the
    YAML universe.

    If instrument_list is provided, only those symbols are included (preserving
    the order from the YAML definition). If None, returns ALL indices + equities.
    """
    all_items = get_all_instruments()
    if instrument_list:
        wanted = {s.upper() for s in instrument_list}
        all_items = [i for i in all_items if i.get("symbol", "").upper() in wanted]
    return [
        {"label": item["label"], "value": item["symbol"]}
        for item in all_items
    ]


def reset_cache() -> None:
    """Clear the YAML cache — useful in tests."""
    global _universe_cache
    _universe_cache = None
