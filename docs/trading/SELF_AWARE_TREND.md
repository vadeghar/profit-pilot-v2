# Self-Aware Trend System (SATS) — port and first backtest

**Status: research. The default settings do not pass. Nothing here is ready for money.**

The TradingView indicator "Self-Aware Trend System [WillyAlgoTrader]" v1.12.0 was ported to Python and
run with its default settings on 311 sessions of real NIFTY 5-minute candles (Feb-2025 to 5-Oct-2026).
On the index, 30-minute signals earned about 13 points a trade when made intraday; 5-minute signals
lost. Every timeframe lost in the last three months (Jul-Oct 2026). Bought options keep almost none of
the 30-minute edge. A plain SuperTrend does as well as the adaptive one.

Code: `trading_strategies/self_aware_trend/`. Tests: `tests/test_self_aware_trend.py`.
Numbers below: `python -m trading_strategies.self_aware_trend.backtest --study all`, archived as
`data/backtests/self_aware_trend_20261007_125126.json`.

## 1. What the script is

An `indicator()`, not a `strategy()`, but it keeps its own trade book for its dashboard, so the rules
are fully specified.

- **Engine**: a SuperTrend on the close whose band width changes every bar. A Trend Quality Index
  (TQI, 0..1) mixes efficiency ratio (0.35), volatility regime (0.20), position in the 20-bar range
  (0.25) and momentum persistence (0.20). High TQI narrows the band; the ATR is scaled by
  (0.5 + 0.5 x efficiency ratio); the side the trend leans on is tighter than the far side; the
  multipliers are EMA-smoothed.
- **Signal**: the trend flips, on a close through the band or on a "character flip" (TQI collapsing
  from above 0.55 to below 0.25 within 5 bars while price moves against the trend). Character flips are
  rare: 1 to 14 of the signals per timeframe.
- **Trade**: enter at the close of the flip bar. Stop beyond the last 3-bar pivot, at least "SL buffer"
  and at most 4 ATRs away. Thirds out at 1R, 2R and 3R. Also closed by an opposite signal or after 100
  bars. The stop is never moved.
- **Preset "Auto"** changes the inputs with the chart: up to 5 minutes it is *Scalping* (ATR 10, band
  1.5, SL buffer 1.0), up to 4 hours *Default* (ATR 14, band 2.0, SL buffer 1.5). So "default settings"
  on 5 minutes are not the same settings as on 15 minutes.

## 2. Assumptions

1. **Instrument**: NIFTY 50 spot index. It has no volume, so the script's own no-volume branch is used
   for the volatility part of TQI. TradingView shows a volume for NSE:NIFTY, so TQI there can differ.
2. **Parity with TradingView is not verified.** The port follows Pine's rules for ATR/RSI seeding,
   NaN windows and pivots, and with the adaptive parts off it reproduces an independently coded
   SuperTrend bar for bar. `--study signals --tf 30` prints the last 30 signals with their SL/TP levels
   to hold against a chart.
3. **Timeframes** 5, 10, 15, 20, 25, 30, 45, 60 minutes, built from 5-minute candles and anchored at
   09:15. Nothing finer is on disk, so 1- and 3-minute charts are untested.
4. **Data**: continuous for Jan to 5-Oct-2026 (187 sessions); about 12 sessions a month for 2025 (124),
   which the indicator sees as one continuous chart.
5. **Intraday version**: entries on signal bars closing 09:20 to 14:30, flat at 15:10 (the spot index
   freezes at 15:15), walked on 5-minute candles, the stop before any target inside a bar, a bar opening
   beyond the stop fills at its open. The indicator itself still runs across sessions, as on a chart.
6. **Rupees**: three lots, so a third is one lot. Option premiums are **modelled** (Black-Scholes, IV
   from India VIX, nearest weekly expiry at least a day away); charges are the real table; slippage is
   Rs 0.5 or 0.5% of premium a side ("house") or Rs 0.15 / 0.1% ("tight"). Fixed capital Rs 1,00,000
   for bought options; Rs 1,80,000 a lot assumed as margin for the synthetic future.
7. **Not ported**: the experimental auto-calibration (off by default) and everything that only draws.
8. **Periods**: `2025`, `dev` (Jan-Jun 2026), `test` (Jul-Oct 2026). The test months were on screen
   during the work, so they are a split, not a blind hold-out.

## 3. Results

**The script as it scores itself** (every flip, thirds at 1R/2R/3R, held overnight, no costs):

| TF | Preset | Trades | Win % | Avg R | Total R | Max DD (R) | Points / trade | t | R in test |
|---|---|---|---|---|---|---|---|---|---|
| 5 | Scalping | 2881 | 35.4 | −0.025 | −70.7 | 84.0 | −1.0 | −1.30 | −10.9 |
| 10 | Default | 985 | 37.1 | −0.003 | −2.7 | 28.4 | −0.6 | −0.24 | −13.1 |
| 15 | Default | 630 | 38.3 | 0.021 | 13.1 | 21.4 | −0.9 | −0.22 | −6.6 |
| 20 | Default | 470 | 39.1 | 0.083 | 39.2 | 14.5 | 6.8 | 1.22 | −6.8 |
| 25 | Default | 358 | 42.5 | 0.098 | 35.1 | 15.2 | 15.3 | 2.08 | −9.8 |
| 30 | Default | 315 | 46.7 | 0.159 | 50.2 | 9.0 | 19.9 | 2.38 | −4.6 |
| 45 | Default | 219 | 48.9 | 0.163 | 35.7 | 11.2 | 25.9 | 2.06 | −7.0 |
| 60 | Default | 176 | 43.8 | 0.162 | 28.5 | 12.9 | 28.9 | 1.78 | −6.8 |

About two thirds of the 30-minute trades are held overnight, and the script counts every stop as exactly
−1R, which an overnight gap does not honour.

**Intraday** (same signals, flat at 15:10; rupees = three lots of bought ATM options, house costs):

| TF | Trades | Win % | Points / trade | t | Points 2025 / dev / test | Options net (Rs) | dev / test (Rs) |
|---|---|---|---|---|---|---|---|
| 5 | 2379 | 35.0 | −2.1 | −2.61 | −1153 / −3317 / −517 | −19,33,694 | −9,27,898 / −3,07,492 |
| 10 | 763 | 42.2 | 1.5 | 0.63 | −62 / 1301 / −85 | −5,56,098 | −1,44,984 / −1,31,832 |
| 15 | 512 | 45.3 | 0.7 | 0.21 | 812 / 26 / −469 | −4,73,198 | −1,96,653 / −1,55,092 |
| 20 | 383 | 48.3 | 4.1 | 0.94 | 1034 / 683 / −129 | −2,73,104 | −69,060 / −1,15,392 |
| 25 | 297 | 47.1 | 6.2 | 1.23 | 315 / 1852 / −311 | −2,12,059 | 34,766 / −1,21,164 |
| **30** | 258 | 53.5 | **13.4** | 2.35 | 1414 / 2557 / −507 | 9,554 | 1,31,226 / −1,33,652 |
| 45 | 184 | 52.7 | 10.9 | 1.58 | 1086 / 983 / −59 | −52,655 | 2,070 / −63,514 |
| 60 | 143 | 49.7 | 4.3 | 0.53 | 398 / 787 / −574 | −1,44,388 | 9,008 / −95,429 |

**Timeframe.** 30 minutes is best, and it sits on a hump (20 → 25 → 30 → 45 → 60 gives 4, 6, 13, 11, 4
points a trade), so it is not one lucky column. 5 minutes loses, and significantly. A t of 2.35 picked as
the best of eight is suggestive, not proof.

**Exits barely matter at 30 minutes.** The average stop is 145 points away and the third target 435; a
NIFTY session ranges about 240. Of 258 trades, 180 end on the 15:10 clock (+44.6 points on average),
52 on an opposite flip (−61), 22 on the stop, 4 at TP3. Six exit plans (script thirds, stop to entry
after TP1, no targets, one third at 1R with a runner, with and without breakeven, all out at 2R) land
between 13.4 and 14.5 points a trade. On 30 minutes this is "enter on the flip, hold to 15:10".

**Entry filters** on 30 minutes (points a trade; 2025 / dev / test totals): none 13.4 (1414 / 2557 /
−507); TQI ≥ 0.35 18.5 (583 / 1532 / +213, 126 trades); TQI ≥ 0.50 16.3 (36 trades); with the 60-minute
trend 16.2 (67 trades); entries 09:45-13:30 15.5; VIX ≥ 15 25.9 (79 trades, none in test); joining the
standing trend at 09:45 5.3; longs 18.7, shorts 8.3. None of them repeats on 15 and 45 minutes, and the
samples are small. TQI ≥ 0.35 is the only one that turns the test months positive.

**Vehicle** (30 minutes, script plan, three lots):

| Vehicle | Costs | Net (Rs) | Per trade | Max DD | Sharpe | 2025 / dev / test |
|---|---|---|---|---|---|---|
| Buy ATM option | house | 9,554 | 37 | 92% | 0.06 | 11,980 / 1,31,226 / −1,33,652 |
| Buy ATM option | tight | 75,752 | 294 | 84% | 0.44 | 37,013 / 1,60,711 / −1,21,971 |
| Buy option 200 points ITM | house | 1,13,460 | 440 | 104% | 0.48 | 61,988 / 2,12,200 / −1,60,729 |
| Synthetic future (buy call + sell put) | house | 4,85,832 | 1,883 | 18.5% | 1.49 | 2,30,809 / 3,96,842 / −1,41,819 |
| Synthetic future | tight | 6,15,214 | 2,385 | 16.8% | 1.89 | 2,79,195 / 4,53,975 / −1,17,956 |

A bought option held for hours pays decay that is as large as the edge. The call-plus-short-put pair
moves point for point with the index, its decay cancels, and its cost is about 4 index points a round
trip. It needs about Rs 5.4 lakh of margin for three lots (assumed, not checked with the broker) and
writes an option. The drawdown percentages are on that larger capital; the test-period loss is the same
Rs 1.4 lakh either way.

**Benchmark** (intraday, script plan, points a trade):

| TF | SATS default | Plain SuperTrend 14 × 2.0 | Plain SuperTrend 14 × 1.5 |
|---|---|---|---|
| 15 | 0.7 | 11.7 | 3.2 |
| 30 | 13.4 | 12.1 | 17.1 |
| 45 | 10.9 | −3.0 | 11.7 |

The adaptive band does not beat a fixed one. What decides the result is how slow the trend filter is,
which timeframe and band width set together.

## 4. Why the last three months lost

The share of the day's range that the index keeps from open to close was 0.43-0.60 in every month up to
Jul-2026, then 0.26 in August, 0.36 in September and 0.21 in early October: sessions that move and come
back. A trend follower that exits at the close loses on exactly those days, whichever timeframe. VIX in
those months was 11.5-12.9, against 15-21 in the best months (Mar-May 2026).

## 5. What was tried

8 timeframes × the script plan (overnight and intraday), 6 exit plans × 8 timeframes, 9 entry filters on
15, 30 and 45 minutes, 3 vehicles × 2 cost settings, 3 indicators × 3 timeframes. No indicator input was
changed from its default.

## 6. Verdict and next steps

```
Strategy: Self-Aware Trend (SATS), NIFTY intraday
Round: 1 of 12 (default settings, no tuning)
Params this round: script defaults, preset Auto; best timeframe 30 minutes
Result (30m intraday, 3 lots ATM options bought, house costs): annualised 7.7%, max DD 92%, win rate 44.6%, Sharpe 0.06, 258 trades
Verdict: FAIL as bought options; NEEDS MORE DATA on the index signal
Next step: tune, with the limits below
```

1. Check the port against a TradingView chart (`--study signals --tf 30`).
2. Get more history before tuning: the 2025 gaps filled in and 1-minute candles (Breeze), so the tuning
   has a hold-out it has not seen.
3. Tuning candidates, in order of support in the data: band width / ATR length on 15 minutes (the
   benchmark says a slower band is what 15 minutes lacks); a TQI ≥ 0.35 entry gate; a regime gate on VIX.
   Cap the rounds, fix each list before running it.
4. Decide the vehicle. Bought options do not carry this signal; a synthetic future does but needs margin
   and option-writing permission.
