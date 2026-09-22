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
