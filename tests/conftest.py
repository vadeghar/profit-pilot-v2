from __future__ import annotations

import sys
from pathlib import Path

import pytest

from profit_pilot.data.models import MarketState

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datetime import date, datetime, timedelta
import urllib.request
import json


@pytest.fixture(scope="session")
def frozen_nifty(budget: int = 5000) -> list[MarketState]:
    # Use a single representative date for fast tests (Step 10 uses API, not lake)
    # The full range can be restored when running larger backtests.
    start_date = date(2026, 1, 1)
    end_date = date(2026, 1, 1)
    results: list[MarketState] = []
    cur = start_date
    while cur <= end_date:
        url = f"http://localhost:8000/spot/candles?trade_date={cur.isoformat()}"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                rows = json.loads(resp.read().decode("utf-8"))
        except Exception:
            rows = []
        for r in rows:
            ts_str = r.get("trade_time")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except Exception:
                continue
            results.append(MarketState(
                timestamp=ts,
                symbol="NIFTY",
                price=float(r.get("close", 0)),
                fields={
                    "open": float(r.get("open", 0)),
                    "high": float(r.get("high", 0)),
                    "low": float(r.get("low", 0)),
                    "volume": float(r.get("volume") or 0),
                }
            ))
        cur += timedelta(days=1)
    return results[:budget]