# V2.8 Near-Miss Analysis (St V thresholds, unchanged rules)
Universe: AXISBANK BAJFINANCE HDFCBANK ICICIBANK ITC LT RELIANCE SBIN
Window: 5-day Waterfall, same V2.5 thresholds

## Per-threshold near-miss counts (aggregate across 8 stocks)
  RVOL>=0.6: 276
  RVOL>=0.8: 218
  RVOL>=1.0: 165
  close_loc>=0.25: 166
  close_loc>=0.35: 123
  close_loc>=0.45: 100
  range_ATR<=1.5: 274
  range_ATR<=2.0: 291
  wick>=0.1: 234
  wick>=0.15: 196
  wick>=0.25: 125

## Per-stock (approximate counts)
  AXISBANK: {'RVOL>=1.0': 21, 'RVOL>=0.8': 26, 'RVOL>=0.6': 32, 'wick>=0.1': 29, 'range_ATR<=1.5': 31, 'range_ATR<=2.0': 33, 'close_loc>=0.45': 13, 'close_loc>=0.35': 17, 'close_loc>=0.25': 21, 'wick>=0.25': 18, 'wick>=0.15': 23}
  BAJFINANCE: {'RVOL>=1.0': 15, 'RVOL>=0.8': 24, 'RVOL>=0.6': 31, 'close_loc>=0.35': 18, 'close_loc>=0.25': 20, 'wick>=0.15': 22, 'wick>=0.1': 25, 'range_ATR<=1.5': 30, 'range_ATR<=2.0': 31, 'close_loc>=0.45': 16, 'wick>=0.25': 14}
  HDFCBANK: {'RVOL>=1.0': 28, 'RVOL>=0.8': 31, 'RVOL>=0.6': 39, 'close_loc>=0.35': 13, 'close_loc>=0.25': 21, 'wick>=0.25': 14, 'wick>=0.15': 22, 'wick>=0.1': 31, 'range_ATR<=1.5': 36, 'range_ATR<=2.0': 38, 'close_loc>=0.45': 9}
  ICICIBANK: {'RVOL>=1.0': 15, 'RVOL>=0.8': 21, 'RVOL>=0.6': 27, 'close_loc>=0.35': 13, 'close_loc>=0.25': 18, 'wick>=0.25': 10, 'wick>=0.15': 23, 'wick>=0.1': 25, 'range_ATR<=1.5': 31, 'range_ATR<=2.0': 31, 'close_loc>=0.45': 11}
  ITC: {'RVOL>=1.0': 28, 'RVOL>=0.8': 40, 'RVOL>=0.6': 45, 'close_loc>=0.45': 13, 'close_loc>=0.35': 14, 'close_loc>=0.25': 21, 'wick>=0.25': 16, 'wick>=0.15': 31, 'wick>=0.1': 38, 'range_ATR<=1.5': 45, 'range_ATR<=2.0': 49}
  LT: {'RVOL>=1.0': 25, 'RVOL>=0.8': 28, 'RVOL>=0.6': 35, 'close_loc>=0.25': 23, 'wick>=0.25': 18, 'wick>=0.15': 28, 'wick>=0.1': 31, 'range_ATR<=1.5': 34, 'range_ATR<=2.0': 37, 'close_loc>=0.45': 13, 'close_loc>=0.35': 18}
  RELIANCE: {'RVOL>=1.0': 23, 'RVOL>=0.8': 30, 'RVOL>=0.6': 41, 'close_loc>=0.25': 25, 'wick>=0.25': 21, 'wick>=0.15': 28, 'wick>=0.1': 31, 'range_ATR<=1.5': 38, 'range_ATR<=2.0': 42, 'close_loc>=0.45': 14, 'close_loc>=0.35': 17}
  SBIN: {'RVOL>=0.6': 26, 'wick>=0.15': 19, 'wick>=0.1': 24, 'range_ATR<=1.5': 29, 'range_ATR<=2.0': 30, 'RVOL>=0.8': 18, 'close_loc>=0.45': 11, 'close_loc>=0.35': 13, 'close_loc>=0.25': 17, 'wick>=0.25': 14, 'RVOL>=1.0': 10}

## Interpretation
- RVOL >= 1.0 is more achievable than >= 1.2 (higher count at 1.0 means more near-misses at stricter threshold)
- close_location >= 0.25 is easiest; >= 0.45 is stricter (fewer near-misses)
- lower_wick >= 0.25 stricter than >= 0.10
- range_ATR <= 1.5 stricter than <= 2.0
Since aggregate counts show many near-misses but 0 complete sequences, the bottleneck remains in sequence continuity (absorption/low-vol/breakout chain), not just SV thresholds.
No thresholds optimized; no extra indicators added; geometry verified; no negative ratios.
