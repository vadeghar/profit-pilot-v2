"""Deterministic importer for the ICICI Breeze SDK (``breeze_connect``).

Why this exists
---------------
``breeze_connect/breeze_connect.py`` starts with::

    dirs = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(1, dirs)
    import config
    ...
    urlopen(config.SECURITY_MASTER_URL)

That is an *absolute* ``import config`` made resolvable only because the SDK
inserts its own directory into ``sys.path`` at index **1**. Anything named
``config`` earlier on ``sys.path`` — the cwd/project root, ``PYTHONPATH``, a
stray ``config.py`` in a script folder — wins and the SDK fails with::

    AttributeError: module 'config' has no attribute 'SECURITY_MASTER_URL'

Importing the SDK through :func:`import_breeze_connect` loads the SDK's own
``config.py`` by file path and seeds ``sys.modules['config']`` with it, so the
SDK's internal import can never be shadowed, regardless of the working
directory. It also points OpenSSL at certifi's CA bundle (the python.org
bundle lacks the newer GlobalSign roots used by api.icicidirect.com).
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

__all__ = ["import_breeze_connect", "ensure_tls_cabundle"]


def ensure_tls_cabundle() -> None:
    """Point OpenSSL/requests at certifi's CA bundle (idempotent, best-effort)."""
    try:
        import certifi
    except ImportError:
        return
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())


def _sdk_dir() -> Path | None:
    """Filesystem path of the installed ``breeze_connect`` package, if any."""
    # Already-imported (possibly stubbed) module: use its spec, never import.
    spec = getattr(sys.modules.get("breeze_connect"), "__spec__", None)
    if spec is None:
        try:
            spec = importlib.util.find_spec("breeze_connect")
        except Exception:  # noqa: BLE001 - SDK missing / broken metadata
            return None
    locations = getattr(spec, "submodule_search_locations", None) or []
    for location in locations:
        candidate = Path(location)
        if candidate.is_dir():
            return candidate
    return None


def _seed_sdk_config_module() -> None:
    """Load ``breeze_connect/config.py`` as top-level ``config`` in sys.modules."""
    current = sys.modules.get("config")
    # A previously-seeded (or genuinely SDK) config module already has the marker.
    if current is not None and hasattr(current, "SECURITY_MASTER_URL"):
        return

    sdk_dir = _sdk_dir()
    if sdk_dir is None:
        return  # not installed → let the real import raise a clear error
    config_path = sdk_dir / "config.py"
    if not config_path.exists():
        return

    spec = importlib.util.spec_from_file_location("config", str(config_path))
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    sys.modules["config"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop("config", None)
        raise


def import_breeze_connect() -> Any:
    """Return the SDK's ``BreezeConnect`` class with ``config`` resolution pinned.

    Raises ImportError/AttributeError with the underlying cause when the SDK is
    not installed or cannot be loaded — callers decide how to surface it.
    """
    ensure_tls_cabundle()
    if "breeze_connect" not in sys.modules:
        _seed_sdk_config_module()
    from breeze_connect import BreezeConnect  # noqa: PLC0415 - lazy by design
    return BreezeConnect
