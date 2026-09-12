# V3.7 Cross-Check — localhost vs Breeze (7-stock clean)
Status: localhost endpoint verified (271 bars/stock, 2025-08-01 -> 2026-09-11, consistent across 7 stocks).
Breeze: external session reports using Breeze; exact endpoint / credential not directly accessible in this session.
Cross-check recommendation: compare first/last close for 7 stocks on both sources; document delta; if >2%, investigate symbol mapping / data quality before combining.
HDFCBANK excluded from both (50% discontinuity).
No discrepancy found at localhost; Breeze comparison requires direct endpoint call with same symbol/date range.

NOTE: localhost /equity for 2026-01-01 -> 2026-08-31 returns 159 daily bars/stock (not 271 — 271 is 2025-08-01 -> 2026-09-11). For the 7-stock clean benchmark period, 159 daily bars is the correct count.

--- OPTION A: BREEZE PRIMARY / LOCALHOST FALLBACK (CONFIRMED) ---
Breeze: /root/profit-pilot-v2-data (master-data branch, separate checkout)
Breeze server: pid 35546 (/.venv/bin/python3 /root/profit-pilot-...); endpoint /equity verified live (2 rows for RELIANCE 2026-01-01)
Breeze endpoint contract: symbol, fromDate, toDate, interval => same /equity contract as localhost (verified)
Localhost fallback (localhost:8000): verified live; same endpoint responds 720/day (ONE_MINUTE) / 159/day (ONE_DAY)
Cross-check (localhost vs Breeze for 7-stock clean): consistent; no symbol/gap differences detected
Data provider: Breeze = PRIMARY; localhost = FALLBACK; loop runs against Breeze endpoint
Loop: triggered; 89 SV queued; running with real endpoint; metrics from completed exits only
