from profit_pilot.data.market_data import MarketDataProvider
from profit_pilot.data.models import MarketState
from datetime import date, datetime
from collections.abc import Iterable
import urllib.request
import json

API_URL = "http://localhost:8000/spot/candles"

class HttpMarketDataProvider:
    def states(self, start: date, end: date) -> Iterable[MarketState]:
        # For Step 10, we page per trade_date. For simplicity here,
        # fetch each date between start and end inclusive.
        # Note: the API returns per-day candles; this is the data contract.
        current = start
        while current <= end:
            url = f"{API_URL}?trade_date={current.isoformat()}"
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    rows = json.loads(resp.read().decode("utf-8"))
            except Exception as exc:
                # Audit recommendation: don't swallow errors silently.
                # Log the exception message without changing the API contract.
                import sys
                print(f"[HttpMarketDataProvider] Error fetching {url}: {exc}", file=sys.stderr)
                rows = []
            for r in rows:
                # trade_time is an ISO timestamp with possible Z or offset; parse carefully
                ts_str = r.get("trade_time")
                # If it has 'T' and no timezone, assume UTC for simplicity here
                # For production, parse with fromisoformat (Python 3.11 supports Z)
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except Exception:
                    continue
                yield MarketState(
                    timestamp=ts,
                    symbol="NIFTY",
                    price=float(r.get("close", r.get("close") or 0)),
                    fields={
                        "open": float(r.get("open", 0)),
                        "high": float(r.get("high", 0)),
                        "low": float(r.get("low", 0)),
                        "volume": float(r.get("volume") or 0),
                    }
                )
            # Move to next day
            from datetime import timedelta
            current += timedelta(days=1)
