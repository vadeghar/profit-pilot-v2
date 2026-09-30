"""Shared paths/credentials for the strategy audit tools."""
from __future__ import annotations

from pathlib import Path

import platform_config


def resolve_env_file() -> Path:
    """The platform .env; a git worktree has none, so fall back to the main checkout's."""
    env = Path(platform_config.ENV_FILE)
    if env.exists():
        return env
    root = Path(platform_config.PROJECT_ROOT).resolve()
    for parent in root.parents:
        if (parent / ".git").is_dir() and (parent / ".env").exists():
            return parent / ".env"
    return env


ENV_FILE = resolve_env_file()
platform_config.ENV_FILE = ENV_FILE  # providers read credentials via platform_config.ENV_FILE

AUDIT_DATA_DIR = Path(platform_config.PROJECT_ROOT) / "data" / "strategy_audit"
REPORT_DIR = Path(platform_config.PROJECT_ROOT) / "docs" / "strategy_audit"
