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
