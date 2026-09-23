"""Central configuration loader — universe, strategy defaults, paths.

Lives inside ``platform_config`` (not a top-level ``config`` package) on
purpose: ``breeze_connect`` does ``sys.path.insert(1, <sdk dir>); import config``
at import time, so *any* top-level ``config`` module/package on ``sys.path``
(cwd, PYTHONPATH, ...) shadows the SDK's own ``config.py`` and breaks the
Breeze connection with::

    AttributeError: module 'config' has no attribute 'SECURITY_MASTER_URL'
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# ---------------------------------------------------------------------------
# Paths — this file is platform_config/universe.py → parent is platform_config/
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_PATH = PROJECT_ROOT / "platform_config" / "universe.yaml"


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
# Cached helpers — import these directly from platform_config wherever needed
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


def get_instrument(symbol: str) -> Optional[dict]:
    """Return the YAML instrument entry for a canonical symbol."""
    wanted = symbol.upper().strip()
    for entry in get_all_instruments():
        if entry.get("symbol", "").upper() == wanted:
            return entry
    return None


def get_index_lot_size(symbol: str, default: int = 1) -> int:
    """Return the configured derivatives lot size for an index symbol."""
    normalized = symbol.upper().strip().replace(":", "_")
    if normalized.startswith("NSE_") or normalized.startswith("BSE_"):
        normalized = normalized.split("_", 1)[1]
    for entry in get_indices():
        configured = str(entry.get("symbol", "")).upper().replace(":", "_")
        if configured.split("_", 1)[-1] == normalized:
            try:
                lot_size = int(entry.get("lot_size", default))
            except (TypeError, ValueError):
                return default
            return lot_size if lot_size > 0 else default
    return default


def resolve_provider_symbol(symbol: str, provider: str) -> Optional[dict]:
    """Return the YAML mapping for a canonical symbol and provider.

    Non-universe instruments intentionally return None so FNO/MCX callers keep
    their existing provider-specific resolution paths.
    """
    entry = get_instrument(symbol)
    if not entry:
        return None
    field = {
        "angel": "sym_angel",
        "breeze": "sym_breeze",
        "yfinance": "sym_yfinance",
    }.get(provider.lower())
    if not field or not entry.get(field):
        return None
    return {**entry, "provider_symbol": entry[field]}


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
