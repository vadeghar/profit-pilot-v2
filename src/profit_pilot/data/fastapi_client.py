from profit_pilot.data.market_data import MarketDataProvider
from profit_pilot.data.models import MarketState
from datetime import date, datetime
from collections.abc import Iterable
import urllib.request
import json

API_URL = "http://localhost:8000"

class HttpMarketDataProvider:
    """HTTP data provider for the ProfitPilot Data Bridge API.

    Contracts:
      - Spot (NIFTY):  GET /spot?symbol=NIFTY&fromDate=...&toDate=...&interval=ONE_DAY
      - Equity:       GET /equity?symbol=RELIANCE&fromDate=...&toDate=...&interval=ONE_DAY
      - VIX:          GET /vix?fromDate=...&toDate=...&interval=ONE_DAY
      - Options:      GET /options?strike=...&expiry=...&optionType=CE&fromDate=...&toDate=...&interval=ONE_DAY

    Each returns rows with: trade_time, open, high, low, close, volume.
    """

    def __init__(self, symbol: str = "NIFTY", interval: str = "ONE_DAY"):
        self.symbol = symbol
        self.interval = interval

    def states(self, start: date, end: date) -> Iterable[MarketState]:
        """Yield MarketState rows for [start, end] from the API."""
        current = start
        while current <= end:
            # Fetch up to 800 buckets per call (api cap); chunk by month to stay sane
            month_end = self._month_end(current)
            url = (f"{API_URL}/equity?symbol={self.symbol}"
                   f"&fromDate={current.isoformat()}"
                   f"&toDate={month_end.isoformat()}"
                   f"&interval={self.interval}")
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    rows = json.loads(resp.read().decode("utf-8"))
            except Exception as exc:
                import sys
                print(f"[HttpMarketDataProvider] Error fetching {url}: {exc}", file=sys.stderr)
                rows = []
            for r in rows:
                ts_str = r.get("trade_time")
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except Exception:
                    continue
                yield MarketState(
                    timestamp=ts,
                    symbol=self.symbol,
                    price=float(r.get("close", r.get("close") or 0)),
                    fields={
                        "open": float(r.get("open", 0)),
                        "high": float(r.get("high", 0)),
                        "low": float(r.get("low", 0)),
                        "volume": float(r.get("volume") or 0),
                    },
                )
            current = month_end + __import__("datetime").timedelta(days=1)

    @staticmethod
    def _month_end(d: date) -> date:
        import datetime
        y, m = d.year, d.month
        next_month = d.replace(day=28) + datetime.timedelta(days=5)
        return next_month - datetime.timedelta(days=next_month.day)
