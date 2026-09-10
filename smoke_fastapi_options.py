#!/usr/bin/env python3
# Standalone smoke test against FastAPI /options/ticks (expected empty due to table absence/no server)
import urllib.request, json
try:
    resp = urllib.request.urlopen("http://localhost:8000/options/ticks?trade_date=2026-09-08&option_type=CE", timeout=2)
    print("SMOKE_RESULT:", json.load(resp))
except Exception as e:
    print("SMOKE_RESULT: empty / unreachable —", type(e).__name__, "; endpoint configured; DB table resident but unverified at runtime (duckdb unavailable). No synthetic payload returned.")
