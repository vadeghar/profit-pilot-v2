"""Configuration module for Trading Platform"""

import os
from pathlib import Path
from typing import Any, Dict, Optional
import yaml


# ---------------------------------------------------------------------------
# Project paths (single source of truth used across the platform)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ENV_FILE = PROJECT_ROOT / ".env"
# Worktrees commonly keep one shared credential file in the parent workspace
# rather than copying secrets into every worktree.
if not ENV_FILE.exists():
    shared_workspace = PROJECT_ROOT.parent.parent / PROJECT_ROOT.parent.name.replace(".worktrees", "")
    for shared_env_file in (
        PROJECT_ROOT.parent / ".env",
        PROJECT_ROOT.parent.parent / ".env",
        shared_workspace / ".env",
    ):
        if shared_env_file.exists():
            ENV_FILE = shared_env_file
            break
CACHE_DIR = DATA_DIR / "cache"
FORWARD_TEST_DIR = DATA_DIR / "forward_test"
HISTORICAL_DATA_DIR = DATA_DIR / "historical"


class Config:
    """Main configuration class for the trading platform"""
    
    DEFAULT_CONFIG = {
        "system": {
            "heartbeat": {
                "intervalSeconds": 30,
                "stallThresholdSeconds": 120
            },
            "marketData": {
                "staleThresholdSeconds": 10,
                "reconnectAttempts": 5,
                "reconnectBackoffSeconds": 2
            },
            "dataDir": "data",
            "logLevel": "INFO"
        },
        "execution": {
            "orderRetryAttempts": 3,
            "orderRetryDelaySeconds": 2,
            "partialFillTimeoutSeconds": 30,
            "partialFillBehavior": "ROLLBACK"
        },
        "persistence": {
            "journalEnabled": True,
            "snapshotIntervalSeconds": 300,
            "maxJournalFiles": 100
        },
        "notifications": {
            "telegram": {
                "enabled": False,
                "rateLimitPerMinute": 20,
                "coalesceWindowSeconds": 10
            }
        },
        "monitoring": {
            "mode": "TICK",
            "candleTimeframe": "1m"
        },
        "backtest": {
            "execution": {
                "fillModel": "TICK",
                "slippagePercent": 0.05,
                "assumeVolume": "INFINITE"
            }
        }
    }
    
    def __init__(self, config_path: Optional[str] = None):
        self.config: Dict[str, Any] = self.DEFAULT_CONFIG.copy()
        if config_path and os.path.exists(config_path):
            self.load(config_path)
    
    def load(self, path: str) -> None:
        """Load configuration from YAML file"""
        with open(path, 'r') as f:
            user_config = yaml.safe_load(f)
            if user_config:
                self._deep_merge(self.config, user_config)
    
    def _deep_merge(self, base: dict, updates: dict) -> None:
        """Deep merge updates into base"""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get config value using dot notation"""
        keys = key.split('.')
        value = self.config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
            if value is None:
                return default
        return value
    
    def set(self, key: str, value: Any) -> None:
        """Set config value using dot notation"""
        keys = key.split('.')
        config = self.config
        for k in keys[:-1]:
            config = config.setdefault(k, {})
        config[keys[-1]] = value
    
    def save(self, path: str) -> None:
        """Save configuration to YAML file"""
        with open(path, 'w') as f:
            yaml.dump(self.config, f, default_flow_style=False)


# Global config instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get global config instance"""
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(config: Config) -> None:
    """Set global config instance"""
    global _config
    _config = config


# ---------------------------------------------------------------------------
# Universe helpers (platform_config/universe.py) — re-exported so callers use
# `from platform_config import get_all_instruments` etc.
#
# NOTE: these used to live in a top-level `config/` package, which shadowed the
# `config` module imported internally by the breeze_connect SDK and broke every
# Breeze connection ("module 'config' has no attribute 'SECURITY_MASTER_URL'").
# ---------------------------------------------------------------------------
from .universe import (  # noqa: E402,F401  (re-export, kept at the bottom)
    UNIVERSE_PATH,
    build_dropdown_list,
    get_all_instruments,
    get_equities,
    get_indices,
    get_instrument,
    get_index_lot_size,
    get_strategy_defaults,
    get_strategy_instruments,
    label_for_dropdown,
    load_universe,
    reset_cache,
    resolve_provider_symbol,
    symbol_to_label,
)
