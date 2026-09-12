# V2.6 Final Report (Pure VPA, 12-stock universe, 2026-01-01 → 08-31)

## V2.5 Backtest (threshold relaxation)
{'TRADES': 0, 'RET_PCT': 0.0, 'PNL': 0, 'WIN_RATE': 0.0, 'MAX_DD': 0.0, 'LOSING_TRADES': ['None (no executions)'], 'FAILURE_REASON': 'Waterfall bottleneck — decline < 1xATR dominates; stopping volume requires 20 prior bars not met; absorption contradicts short windows.'}
## V2.6 Per-stock diagnostics (8 of 12 have data)
# V2.6 Per-Stock Phase/Frequency

## HDFCBANK
{'bars': 159, 'first': '2026-01-01T00:00:00', 'last': '2026-08-31T00:00:00'}

## ICICIBANK
{'bars': 159, 'first': '2026-01-01T00:00:00', 'last': '2026-08-31T00:00:00'}

## RELIANCE
{'bars': 159, 'first': '2026-01-01T00:00:00', 'last': '2026-08-31T00:00:00'}

## BHARTIARTL
{'error': 'HTTP Error 400: Bad Request', 'bars': 0}

## LT
{'bars': 159, 'first': '2026-01-01T00:00:00', 'last': '2026-08-31T00:00:00'}

## SBIN
{'bars': 159, 'first': '2026-01-01T00:00:00', 'last': '2026-08-31T00:00:00'}

## INFY
{'error': 'HTTP Error 400: Bad Request', 'bars': 0}

## AXISBANK
{'bars': 159, 'first': '2026-01-01T00:00:00', 'last': '2026-08-31T00:00:00'}

## KOTAKBANK
{'error': 'HTTP Error 400: Bad Request', 'bars': 0}

## M&M
{'error': 'HTTP Error 422: Unprocessable Entity', 'bars': 0}

## BAJFINANCE
{'bars': 159, 'first': '2026-01-01T00:00:00', 'last': '2026-08-31T00:00:00'}

## ITC
{'bars': 159, 'first': '2026-01-01T00:00:00', 'last': '2026-08-31T00:00:00'}

## Aggregate
Total stocks: 12
Total bars (sum): 1272
Symbols with data: 8


---

# V2.6 Per-Stock Phase & Failure Reasons (approximate; VPA rules, data only)

## Per-stock

### HDFCBANK
- Bars: 159
- Waterfall candidates: 75
- Failed: fewer than 3 down bars: 5
- Failed: decline < 1xATR: 69

### ICICIBANK
- Bars: 159
- Waterfall candidates: 55
- Failed: fewer than 3 down bars: 6
- Failed: decline < 1xATR: 88

### RELIANCE
- Bars: 159
- Waterfall candidates: 56
- Failed: fewer than 3 down bars: 0
- Failed: decline < 1xATR: 93

### LT
- Bars: 159
- Waterfall candidates: 56
- Failed: fewer than 3 down bars: 6
- Failed: decline < 1xATR: 87

### SBIN
- Bars: 159
- Waterfall candidates: 46
- Failed: fewer than 3 down bars: 12
- Failed: decline < 1xATR: 91

### AXISBANK
- Bars: 159
- Waterfall candidates: 48
- Failed: fewer than 3 down bars: 3
- Failed: decline < 1xATR: 98

### BAJFINANCE
- Bars: 159
- Waterfall candidates: 46
- Failed: fewer than 3 down bars: 8
- Failed: decline < 1xATR: 95

### ITC
- Bars: 159
- Waterfall candidates: 69
- Failed: fewer than 3 down bars: 3
- Failed: decline < 1xATR: 77

## Aggregate (8 symbols with data, 4 missing)
- Total daily bars: 1272
- Total WF candidates across 8 symbols: 451
- Note: BHARTIARTL, INFY, KOTAKBANK, M&M returned 0 bars (symbol/code mismatch in API?)

## Key finding
Zero complete sequences expected; Waterfall is bottleneck across all available symbols.
No extra indicators used.

## Aggregate
- Symbols with data: 8/12 (4 missing: BHARTIARTL, INFY, KOTAKBANK, M&M — likely symbol/code mapping issue in master-data)
- Waterfall is primary bottleneck (most candidates fail on decline < 1xATR, not down-bar count)
- No RSI/MACD/VWAP/news/AI added
- Recommendation: V2.7 should either (a) shorten waterfall lookback or (b) use multi-stock combined scan so sequences from different stocks aggregate, without adding filters.
