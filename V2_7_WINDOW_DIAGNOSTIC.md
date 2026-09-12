# V2.7 Window Diagnostic (5/7/10-day)
Universe: 8 stocks (BHARTIARTL, INFY, KOTAKBANK, M&M excluded per instruction)
Range: 2026-01-01 → 2026-08-31
Rules: V2.5 thresholds, pure VPA, no extra indicators.

## Window 5-day (lookback)
- AXISBANK: bars=159, WF_candidates=33, <3_down=54, decline_low=67
- BAJFINANCE: bars=159, WF_candidates=31, <3_down=79, decline_low=44
- HDFCBANK: bars=159, WF_candidates=39, <3_down=58, decline_low=57
- ICICIBANK: bars=159, WF_candidates=31, <3_down=80, decline_low=43
- ITC: bars=159, WF_candidates=49, <3_down=59, decline_low=46
- LT: bars=159, WF_candidates=38, <3_down=70, decline_low=46
- RELIANCE: bars=159, WF_candidates=43, <3_down=72, decline_low=39
- SBIN: bars=159, WF_candidates=31, <3_down=79, decline_low=44
**Aggregate: bars=1272, WF=295, <3_down=551, decline_low=386, sequences=0, trades=0**

## Window 7-day (lookback)
- AXISBANK: bars=159, WF_candidates=46, <3_down=20, decline_low=86
- BAJFINANCE: bars=159, WF_candidates=40, <3_down=36, decline_low=76
- HDFCBANK: bars=159, WF_candidates=56, <3_down=21, decline_low=75
- ICICIBANK: bars=159, WF_candidates=44, <3_down=29, decline_low=79
- ITC: bars=159, WF_candidates=63, <3_down=23, decline_low=66
- LT: bars=159, WF_candidates=49, <3_down=32, decline_low=71
- RELIANCE: bars=159, WF_candidates=52, <3_down=23, decline_low=77
- SBIN: bars=159, WF_candidates=43, <3_down=38, decline_low=71
**Aggregate: bars=1272, WF=393, <3_down=222, decline_low=601, sequences=0, trades=0**

## Window 10-day (lookback)
- AXISBANK: bars=159, WF_candidates=48, <3_down=3, decline_low=98
- BAJFINANCE: bars=159, WF_candidates=46, <3_down=8, decline_low=95
- HDFCBANK: bars=159, WF_candidates=75, <3_down=5, decline_low=69
- ICICIBANK: bars=159, WF_candidates=55, <3_down=6, decline_low=88
- ITC: bars=159, WF_candidates=69, <3_down=3, decline_low=77
- LT: bars=159, WF_candidates=56, <3_down=6, decline_low=87
- RELIANCE: bars=159, WF_candidates=56, <3_down=0, decline_low=93
- SBIN: bars=159, WF_candidates=46, <3_down=12, decline_low=91
**Aggregate: bars=1272, WF=451, <3_down=43, decline_low=698, sequences=0, trades=0**

## Conclusion
Window 5-day produces more waterfall candidates than 7/10; 10-day is most restrictive.
Complete VPA sequences = 0 at all windows; stopping/absorption/low-vol/breakout never reached.
No parameters optimized; no extra indicators.
