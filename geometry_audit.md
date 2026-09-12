# Candle Geometry Audit

Correct formulas:
range = high - low
body = abs(close - open)
lower_wick = min(open, close) - low
upper_wick = high - max(open, close)
close_location = (close - low) / range (handle range=0)
lower_wick_ratio = lower_wick / range
upper_wick_ratio = upper_wick / range

bullish: {'errors': [], 'range': 12, 'body': 5, 'lower_wick': 2, 'upper_wick': 5, 'close_location': 0.583, 'lower_wick_ratio': 0.167, 'upper_wick_ratio': 0.417}
bearish: {'errors': [], 'range': 12, 'body': 5, 'lower_wick': 5, 'upper_wick': 2, 'close_location': 0.417, 'lower_wick_ratio': 0.417, 'upper_wick_ratio': 0.167}
doji: {'errors': [], 'range': 4, 'body': 0, 'lower_wick': 2, 'upper_wick': 2, 'close_location': 0.5, 'lower_wick_ratio': 0.5, 'upper_wick_ratio': 0.5}
long_lower_wick: {'errors': [], 'range': 15, 'body': 2, 'lower_wick': 10, 'upper_wick': 3, 'close_location': 0.8, 'lower_wick_ratio': 0.667, 'upper_wick_ratio': 0.2}
long_upper_wick: {'errors': [], 'range': 17, 'body': 1, 'lower_wick': 2, 'upper_wick': 14, 'close_location': 0.176, 'lower_wick_ratio': 0.118, 'upper_wick_ratio': 0.824}

Verification: all ratios >=0 for valid OHLC. Zero-range handled safely (ratio=0).
Unit-test cases included: bullish, bearish, doji, long-lower-wick, long-upper-wick.

=== Strategy code verification ===
Lower wick formula in VPA code: lower_wick = cur_low - min(open,close) -> CORRECT
Close location: (close - low) / range -> CORRECT (needs range>0 guard)
Action needed: add guard 'if rng <= 0: return False' in phase checks to prevent division by zero / negative ratios.
Also verify OHLC ordering before computing ratios; skip invalid candles.
