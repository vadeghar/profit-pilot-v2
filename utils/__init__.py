"""Utility functions for Trading Platform"""

import hashlib
import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


def get_timestamp() -> str:
    """Get current IST timestamp in ISO-8601 format"""
    from utils.timezone import now_ist
    return now_ist().isoformat()


def get_timestamp_ms() -> int:
    """Get current IST timestamp in milliseconds"""
    from utils.timezone import now_ist
    return int(now_ist().timestamp() * 1000)


def ist_now() -> datetime:
    """Get current IST time (aware, Asia/Kolkata = UTC+05:30)."""
    from utils.timezone import now_ist
    return now_ist()


def format_inr(amount: float) -> str:
    """Format amount as Indian Rupees"""
    return f"₹{amount:,.2f}"


def ensure_dir(path: str) -> Path:
    """Ensure directory exists"""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_data_dir() -> Path:
    """Get platform data directory"""
    import platform_config
    config = platform_config.get_config()
    data_dir = config.get('system.dataDir', 'data')
    return ensure_dir(data_dir)


def compute_hash(data: Any) -> str:
    """Compute SHA256 hash of data"""
    if isinstance(data, dict):
        data = json.dumps(data, sort_keys=True)
    elif not isinstance(data, str):
        data = str(data)
    return hashlib.sha256(data.encode()).hexdigest()


def parse_instrument(instrument: str) -> Dict[str, str]:
    """Parse instrument token (e.g., 'NSE:INE123A01021' or 'NFO:NIFTY2435023400CE')"""
    parts = instrument.split(':')
    if len(parts) != 2:
        raise ValueError(f"Invalid instrument format: {instrument}")
    exchange, token = parts
    return {"exchange": exchange, "token": token}


def format_pnl(pnl: float) -> str:
    """Format P&L with color indicator"""
    if pnl > 0:
        return f"+₹{pnl:,.2f}"
    elif pnl < 0:
        return f"-₹{abs(pnl):,.2f}"
    else:
        return "₹0.00"


@dataclass
class TimeRange:
    """Time range for backtesting"""
    start: datetime
    end: datetime
    
    def __post_init__(self):
        if self.start.tzinfo is None:
            from utils.timezone import ensure_ist as _e; self.start = _e(self.start)
        if self.end.tzinfo is None:
            from utils.timezone import ensure_ist as _e2; self.end = _e2(self.end)


class RateLimiter:
    """Simple rate limiter"""
    
    def __init__(self, max_calls: int, window_seconds: float):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self.calls: List[float] = []
        self._lock = threading.Lock()
    
    def acquire(self) -> bool:
        """Try to acquire a slot. Returns True if allowed."""
        import time
        now = time.time()
        with self._lock:
            # Remove old calls outside window
            self.calls = [t for t in self.calls if now - t < self.window_seconds]
            if len(self.calls) < self.max_calls:
                self.calls.append(now)
                return True
            return False
    
    def wait_if_needed(self) -> None:
        """Wait if rate limit is exceeded"""
        import time
        while not self.acquire():
            time.sleep(0.1)


class Logger:
    """Thread-safe logger with file output"""
    
    def __init__(self, name: str, log_file: Optional[str] = None):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)
        
        # Console handler
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        formatter = logging.Formatter(
            '%(asctime)s | %(name)s | %(levelname)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        ch.setFormatter(formatter)
        self.logger.addHandler(ch)
        
        # File handler
        if log_file:
            ensure_dir(os.path.dirname(log_file))
            fh = logging.FileHandler(log_file)
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(formatter)
            self.logger.addHandler(fh)
    
    def debug(self, msg: str) -> None:
        self.logger.debug(msg)
    
    def info(self, msg: str) -> None:
        self.logger.info(msg)
    
    def warning(self, msg: str) -> None:
        self.logger.warning(msg)
    
    def error(self, msg: str) -> None:
        self.logger.error(msg)
    
    def critical(self, msg: str) -> None:
        self.logger.critical(msg)


# Default logger
log = Logger("trading-platform")
