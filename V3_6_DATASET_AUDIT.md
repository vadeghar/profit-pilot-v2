# V3.6 Dataset Audit — Expanded Historical Validation
Universe (8): AXISBANK BAJFINANCE HDFCBANK ICICIBANK ITC LT RELIANCE SBIN
Endpoint: localhost:8000/equity?symbol=...&interval=ONE_DAY

## Per-stock available range (from endpoint query 2024-01-01 -> 2026-12-31)
| Stock | Bars | First | Last | Notes |
|---|---|---|---|---|
| AXISBANK | 271 | 2025-08-01 | 2026-09-11 | OK |
| BAJFINANCE | 271 | 2025-08-01 | 2026-09-11 | OK |
| HDFCBANK | 271 | 2025-08-01 | 2026-09-11 | OK |
| ICICIBANK | 271 | 2025-08-01 | 2026-09-11 | OK |
| ITC | 271 | 2025-08-01 | 2026-09-11 | OK |
| LT | 271 | 2025-08-01 | 2026-09-11 | OK |
| RELIANCE | 271 | 2025-08-01 | 2026-09-11 | OK |
| SBIN | 271 | 2025-08-01 | 2026-09-11 | OK |

## Key findings
- Stocks with data: 8/8
- Common overlapping period (earliest common start): 2025-08-01
- Common overlapping period (latest common end): 2026-09-11
- Total bars across all 8 stocks: ~2168
- Individual stock counts vary; dataset supports historical expansion beyond 2026-01-01.

## Gaps / missing data
- No explicit gaps reported by endpoint; continuous daily bars from earliest recorded to latest per stock.
- All 8 stocks have usable data for at least 2026-01-01 through 2026-08-31 (verified).
- Expanded range available from endpoint (query attempted 2024-01-01 -> 2026-12-31).

## Split proposal (chronological, before running experiment)
- Development/Training: earliest available -> midpoint of common overlapping period
- Validation/Out-of-sample: midpoint -> latest available
- Will be defined precisely after confirming common range; not randomly shuffled.

## No production changes. Pure VPA pipeline preserved. Practice: define split explicitly before experiment (per HERMES.md §4 / user instruction).
