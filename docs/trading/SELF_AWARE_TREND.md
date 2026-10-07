# Self-Aware Trend System (SATS) — port and first backtest

**Status: research. The default settings do not pass. Nothing here is ready for money.**

> **Read section 8 first.** Sections 1-7 were run on 311 NIFTY sessions that included closing-auction
> bars after 15:15 from 3-Aug-2026 and, for stocks, on 57 sessions from Yahoo. Section 8 reruns the work on
> 684 continuous sessions from Breeze with those bars removed, and supersedes their numbers.

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

## 7. Round 2: stocks and presets (7-Oct-2026)

Asked: does it hold on the three heaviest NIFTY stocks, and which preset is best. No input was tuned;
only the script's own presets were switched. `--study stocks --base 5|60` and `--study presets`,
archived as `data/backtests/self_aware_trend_stocks_*.json` and `self_aware_trend_presets_20261007.json`.

**Port check.** The user's TradingView dashboard (NIFTY spot, Scalping preset) confirms the TQI formula
(0.26 x 0.35 + 0 x 0.20 + 0.51 x 0.25 + 0.50 x 0.20 = 0.32). It also shows `Vol Z -2.34`: TradingView has a
volume for NIFTY spot and the script uses it, while these candles have none and take the no-volume
branch. That is one TQI component (weight 0.20) computed differently on the index. Stocks have volume.

**Stock data.** The local Breeze session had expired, so stocks come from Yahoo: 5-minute candles for the
last 57 sessions only (16-Jul to 6-Oct-2026), hourly candles for about three years (Oct-2023 on, ~718
sessions). The hourly test trades on hourly bars: entries 10:15-14:15, flat at 15:15. Moves are in basis
points (bps) of the entry price; an intraday equity round trip is assumed to cost 10 bps.

**Presets on NIFTY spot** (311 sessions, intraday, index points a trade; t in brackets for the best cells):

| Preset (ATR, band, SL) | 5 | 10 | 15 | 20 | 25 | 30 | 45 | 60 |
|---|---|---|---|---|---|---|---|---|
| Scalping (10, 1.5, 1.0) | −2.1 | 0.8 | 2.3 | 4.6 | 6.9 | 5.0 | **12.6** (2.0) | 8.9 |
| Default (14, 2.0, 1.5) | −1.5 | 1.5 | 0.7 | 4.2 | 6.3 | **13.4** (2.3) | 10.9 | 4.3 |
| Swing (21, 2.5, 2.0) | 0.1 | 1.5 | 4.2 | 9.2 | **16.3** (2.8) | 13.7 (1.9) | 2.4 | −4.1 |
| Crypto 24/7 (14, 2.8, 2.5) | −1.0 | 4.5 | 4.8 | 10.8 | **16.7** (2.5) | 11.1 | −0.9 | −3.6 |
| Custom (13, 2.0, 1.5) | −1.9 | 1.4 | −0.4 | 3.9 | 6.1 | 11.5 | 9.8 | 4.5 |

The best cell of each preset lies on a diagonal: the wider the band, the shorter the timeframe it wants
(Scalping 45, Default 30, Swing and Crypto 25). The peaks are 12.6 to 16.7 points and cannot be told
apart statistically. Swing is the steadiest: positive on 20, 25 and 30 minutes and in both halves of its
trades. No preset makes 5 minutes work.

**Presets on the three stocks, last 57 sessions** (pooled, bps a trade; 48 to 1,211 trades a cell):

| Preset | 5 | 10 | 15 | 20 | 25 | 30 | 45 | 60 |
|---|---|---|---|---|---|---|---|---|
| Scalping | −0.6 | 0.9 | 2.2 | 3.5 | 3.9 | 0.2 | 12.3 | 12.1 |
| Default | −0.6 | 3.1 | 2.5 | 4.2 | 3.3 | 6.4 | 27.8 | 18.9 |
| Swing | 1.4 | 2.3 | 1.9 | 1.4 | 5.2 | 16.1 | **30.5** | 16.8 |
| Crypto 24/7 | 2.4 | 4.9 | 1.1 | 7.3 | 8.3 | 21.6 | 24.9 | 14.0 |

Only 30 minutes and slower clears the 10 bps cost, and 45 minutes is best for all three stocks. NIFTY
itself lost on every timeframe in these same 57 sessions, so the stocks trended intraday while the index
did not. 57 sessions and 55-80 trades in the best cells is a small sample.

**Hourly chart, three years** (intraday, bps a trade and t):

| Preset | NIFTY | HDFC Bank | ICICI Bank | Reliance |
|---|---|---|---|---|
| Scalping | 0.3 (0.2) | 2.2 (0.7) | −4.0 (−1.3) | 4.3 (1.4) |
| Default | 1.0 (0.4) | 6.7 (1.9) | −5.8 (−1.5) | 8.3 (2.1) |
| Swing | 0.0 (0.0) | 4.1 (1.0) | −7.4 (−1.7) | 10.9 (2.4) |
| Crypto 24/7 | 2.2 (0.7) | −2.8 (−0.6) | −2.6 (−0.6) | 10.4 (1.9) |

Over three years on the hourly chart nothing clears a 10 bps cost except Reliance, barely. ICICI Bank is
negative under every preset. HDFC Bank earned its result in the first half of the period only. Held
overnight as the script scores itself (Default), NIFTY hourly made 9.0 bps a trade (t 2.3) and was
positive in each of 2023-2026; the intraday cut on hourly bars leaves almost none of that.

```
Strategy: Self-Aware Trend (SATS), NIFTY and top-3 stocks, intraday
Round: 2 of 12 (presets only, no input tuned)
Params this round: presets Scalping / Default / Swing / Crypto 24/7 / Custom x 8 timeframes x 4 symbols
Result: NIFTY best cell Swing 25m, 16.3 points a trade, 225 trades, win rate 55%; stocks best cell Swing 45m, 30.5 bps, 63 trades in 57 sessions
Verdict: NEEDS MORE DATA (index); FAIL on the three-year hourly stock test after costs
Next step: get 5-minute stock history from Breeze, then test Swing on 20-30 minutes for NIFTY with deep ITM options
```

## 8. Round 3: 684 sessions from Breeze (7-Oct-2026)

**Data.** 5-minute candles from Breeze for NIFTY, HDFC Bank, ICICI Bank and Reliance, continuous from
1-Jan-2024 to 7-Oct-2026 (684 sessions; `data.py --download`). 2024 and most of 2025 were never seen in
rounds 1-2.

**A data fault found and fixed.** From 3-Aug-2026 the bars Breeze serves after 15:15 are closing-auction
prints: 5-minute ranges of 60-200 bps on NIFTY against about 10 before. They were in the round-1 data for
August and September and distorted the indicator on exactly the months that lost. They are now dropped
(sessions end with the 15:10 candle from that date), as are Muhurat and Saturday sessions.

**NIFTY volume.** TradingView may be showing the future's volume for NIFTY spot. With the near-month
future's 5-minute volume attached to the spot candles, Swing on 25 minutes makes 11.7 points a trade
against 12.4 without, and Default on 30 minutes 10.4 against 9.4. The volume question does not change the
picture.

**Presets on NIFTY spot** (684 sessions, intraday, points a trade; t in brackets):

| Preset | 5 | 10 | 15 | 20 | 25 | 30 | 45 | 60 |
|---|---|---|---|---|---|---|---|---|
| Scalping | −1.1 | 1.1 | 3.4 | 4.4 | 5.6 | 5.0 | **8.6** (2.1) | 5.0 |
| Default | −1.1 | 1.9 | 2.6 | 6.0 | 7.2 | **9.4** (2.3) | 4.5 | 2.2 |
| Swing | 0.1 | 2.9 | 4.2 | 7.1 | **12.4** (3.1) | 10.1 (2.1) | 2.6 | −1.2 |
| Crypto 24/7 | −0.5 | 4.7 | 3.9 | 5.8 | **10.7** (2.3) | 5.4 | −2.1 | 2.6 |
| Custom | −1.0 | 1.9 | 2.6 | 4.8 | 7.1 | 9.6 (2.4) | 4.0 | 2.0 |

The round-2 pattern repeats on more than twice the history: each preset peaks on its own timeframe along
the same diagonal, and Swing on 25 minutes is the best cell (526 trades, 51% wins). It was positive in
each year: +14.1% of index value summed over trades in 2024, +4.8% in 2025, +8.8% in 2026. Default on 30
minutes: +11.4%, +3.2%, +8.0%. 5 minutes loses under every preset. 2025 is the weak year throughout.

**The three stocks** (683 sessions, intraday, bps a trade before the assumed 10 bps cost):

| | Best cell | bps | t | 2024 / 2025 / 2026 (total %) |
|---|---|---|---|---|
| HDFC Bank | Default 45 min | 6.7 | 2.1 | 8.4 / 1.0 / 16.2 |
| ICICI Bank | Scalping 20 min | 2.3 | 1.5 | −0.7 / 4.3 / 23.0 |
| Reliance | Swing 60 min | 10.6 | 2.3 | 12.7 / 1.4 / 12.6 |
| Pooled, best cell | Crypto 25 min | 4.5 | | |

No cell clears the cost over the full history. ICICI Bank is negative in 2024 in 39 of the 40
preset and timeframe cells. The 57-session Yahoo result in section 7 (30 bps on 45 minutes) was the strong 2026 stretch,
not the rule: the same stocks lost in most cells in 2025.

**Vehicle, NIFTY Swing 25 minutes, three lots, script exits** (house costs unless noted):

| Vehicle | Capital | Net (Rs) | Per trade | Profit factor | Max DD | 2024 / 2025 / 2026H1 / 2026H2 |
|---|---|---|---|---|---|---|
| Buy ATM option | 1,00,000 | 5,042 | 10 | 1.00 | 92% | 1,47,414 / −1,41,783 / 61,854 / −62,443 |
| Buy 200 points ITM | 1,00,000 | 2,07,076 | 394 | 1.07 | 78% | 2,44,000 / −99,567 / 1,19,147 / −56,505 |
| Buy 500 points ITM | 1,50,000 | 3,34,211 | 635 | 1.10 | 73% | 3,14,481 / −91,845 / 1,69,828 / −58,254 |
| Buy 500 points ITM, tight costs | 1,50,000 | 7,91,464 | 1,505 | 1.25 | 47% | 4,65,272 / 94,352 / 2,47,190 / −15,351 |
| Synthetic future | 5,40,000 | 8,39,015 | 1,595 | 1.23 | 34% | 4,72,804 / 89,984 / 2,90,863 / −14,636 |

Deeper in the money keeps more of the signal, but slippage is modelled as 0.5% of premium, and a Rs 550
premium pays Rs 2.75 a side. Whether deep ITM works therefore rests on the real bid-ask spread of those
strikes, which this backtest does not have. Rupee figures for 2024 use a 65-unit lot (the real lot was 50,
then 25, then 75 that year). Premiums are still modelled.

```
Strategy: Self-Aware Trend (SATS), NIFTY intraday
Round: 3 of 12 (presets only, 684 sessions, auction bars removed)
Params this round: preset Swing (ATR 21, band 2.5, SL buffer 2.0), 25 minutes, script exits, 3 lots bought 500 points ITM
Result: annualised 81%, max DD 73%, win rate 46.4%, Sharpe 0.51, 526 trades (index: 12.4 points a trade, t 3.1)
Verdict: FAIL on drawdown and profit factor as bought options; index signal PASSES a first out-of-sample look; stocks FAIL
Next step: record or buy real deep-ITM option quotes; then tune exits and position size against the drawdown
```

## 9. Rounds 4-5: tuning NIFTY Swing 25 minutes (7-Oct-2026)

**Protocol.** Tuned on 2025-01 to 2026-10 only (444 sessions); 2024 kept shut as the hold-out. Lists fixed
in code before running (`tune_configs`, `combo_configs`), one change at a time from the baseline (preset
Swing, script exits). Rupees are three lots bought 500 points in the money at house costs.
`--study tune --tf 25`, `--combos`, `--holdout`.

**Round 4, 41 single changes, 2025-2026** (baseline: 344 trades, 9.5 points a trade, t 1.96, Rs 19,730):

| List | What happened |
|---|---|
| Exits (5) | 9.0 to 9.9 points. No effect, as on 30 minutes. |
| Stop distance (5) | Every tighter stop is worse (1.7 to 8.5 points). The wide stop stays. |
| Risk controls (6) | One trade a day 14.3 (t 2.45); stop for the day after a loss 12.7; entries until 13:30 11.8. |
| Entry filters (8) | Entries 09:45-13:30 14.1; higher-timeframe trend 17.8 on 80 trades; VIX >= 15 18.4 on 101; TQI >= 0.35 7.9; longs 14.7, shorts 4.2. |
| Band x ATR length (11) | Band 3.0 gives 14.2 to 16.4 at every ATR length; band 2.0 gives 7.0 to 9.3; band 3.5 falls to 3.5 to 8.8. A peak with a cliff beside it. |
| Adaptive parts off (6) | Removing the trend quality engine (14.6), the asymmetric bands (14.4), the legacy adaptation (14.4) or the smoothing (12.0) each *improves* the result. Removing the efficiency-weighted ATR (2.0) hurts, because it widens the band by about a third. |

The adaptive machinery is not what earns the points; the effective band width is.

**Round 5, six combinations.** Choice written down before 2024 was opened: one trade a day + band 3.0.

| Configuration | 2025-2026: trades / points / t / Rs | 2024 hold-out: trades / points / t / Rs |
|---|---|---|
| Baseline (Swing, script exits) | 344 / 9.5 / 1.96 / 19,730 | 182 / **17.8** / 2.35 / 3,14,481 |
| One trade a day | 259 / 14.3 / 2.45 / 2,50,575 | 138 / 17.8 / 2.04 / 2,17,136 |
| Band 3.0 | 277 / 14.2 / 2.47 / 2,54,693 | 158 / 15.1 / 1.72 / 1,85,814 |
| **One trade a day + band 3.0 (chosen)** | 228 / 16.5 / 2.53 / 3,00,293 | 123 / 12.7 / 1.25 / 86,910 |
| One trade a day + entries until 13:30 | 238 / 15.0 / 2.42 / 2,47,844 | 126 / 15.4 / 1.66 / 1,40,618 |
| One trade a day + band 3.0 + until 13:30 | 210 / 17.9 / 2.57 / 3,21,070 | 118 / 12.4 / 1.19 / 78,769 |

**The tuning did not carry over.** On the hold-out the chosen configuration makes 12.7 points a trade against
17.8 for the untouched baseline, and every tuned row is at or below the baseline. What the tuning found in
2025-2026 was mostly the shape of those two years. All six rows are still positive in 2024, so the signal
itself held; the improvements did not. The one change that cost nothing per trade out of sample is "one
trade a day" (17.8 in both), which trades less often for the same edge.

**Drawdown.** No list touched it. On 2025-2026 the option drawdown is Rs 2.2 to 3.9 lakh on three lots in
every configuration with a usable number of trades, against average wins of about Rs 15,000 and losses of
Rs 12,500 at 52% wins. That is the trade-to-trade swing of a three-lot position around an edge of a few
hundred rupees; exits and stops do not shrink it without also shrinking the edge.

```
Strategy: Self-Aware Trend (SATS), NIFTY intraday
Round: 5 of 12
Params this round: Swing 25 minutes; tuned = one trade a day + band 3.0; 3 lots bought 500 points ITM, house costs
Result (2024 hold-out): tuned 12.7 points a trade, Rs 86,910, profit factor 1.10, Sharpe 0.40, 123 trades; baseline 17.8 points, Rs 3,14,481, profit factor 1.28, Sharpe 1.41, 182 trades
Verdict: FAIL for the tuned version (worse than the baseline out of sample); baseline signal holds but the option drawdown is unsolved
Next step: stop tuning inputs; price the baseline on real deep-ITM option candles from Breeze
```

## 10. Round 6: the baseline on recorded option prices (7-Oct-2026)

**What changed.** The premium model is replaced by Breeze's own 5-minute candles of the option actually
traded: for each of the 526 baseline trades (NIFTY, Swing, 25 minutes, script exits) the contract-day was
downloaded at three strikes (1,578 requests, cached under `research/self_aware_trend/breeze/options/`).
Entries and exits on a candle close take the option's close for that candle; a stop or target inside a
candle takes that close moved by 0.9 x the index distance to the fill level. All 526 trades were priced
at every strike; one needed a stale price. `--study real --preset Swing --tf 25`.

A candle close is a last traded price, not the side of the spread one would get, so slippage is still an
assumption added on top: none, tight (Rs 0.15 or 0.1%) or house (Rs 0.5 or 0.5%). Three lots.

| Strike | Prices | Slippage | Net (Rs) | Per trade | Profit factor | Max DD (Rs) | Sharpe | 2024 / 2025 / 2026H1 / 2026H2 |
|---|---|---|---|---|---|---|---|---|
| ATM | recorded | none | 2,44,322 | 464 | 1.12 | 2,27,246 | 0.61 | 1,83,559 / −30,488 / 1,35,246 / −43,995 |
| ATM | recorded | house | 78,037 | 148 | 1.04 | 2,59,057 | 0.20 | 1,27,992 / −94,065 / 1,01,806 / −57,696 |
| ATM | model | house | 5,042 | 10 | 1.00 | 2,50,677 | 0.01 | |
| 200 ITM | recorded | none | 4,95,686 | 942 | 1.18 | 2,98,581 | 0.91 | 3,04,629 / 32,182 / 1,96,266 / −37,391 |
| 200 ITM | recorded | tight | 4,35,905 | 829 | 1.15 | 3,06,603 | 0.80 | 2,84,780 / 8,577 / 1,85,235 / −42,687 |
| 200 ITM | recorded | house | 1,97,192 | 375 | 1.07 | 3,40,106 | 0.36 | 2,05,546 / −85,669 / 1,41,143 / −63,828 |
| 200 ITM | model | house | 2,07,076 | 394 | 1.07 | 2,93,027 | 0.40 | |
| 500 ITM | recorded | none | 7,50,119 | 1,426 | 1.22 | 3,53,148 | 1.14 | 4,58,322 / 74,694 / 2,58,533 / −41,430 |
| 500 ITM | recorded | tight | 6,35,030 | 1,207 | 1.19 | 3,68,777 | 0.96 | 4,20,430 / 28,039 / 2,38,777 / −52,216 |
| 500 ITM | recorded | house | 1,74,675 | 332 | 1.05 | 4,31,295 | 0.26 | 2,68,864 / −1,58,583 / 1,59,754 / −95,360 |
| 500 ITM | model | house | 3,34,211 | 635 | 1.10 | 3,67,943 | 0.51 | |

- **The model was close.** At 200 ITM the recorded and modelled results at house slippage are Rs 1.97 and
  2.07 lakh. The model was too harsh on ATM and a little kind at 500 ITM.
- **Liquidity decides the strike.** The median volume in the entry candle is 27 lakh units at the money,
  2.2 lakh at 200 ITM and 3,525 at 500 ITM (about 54 lots in five minutes). Three lots is a visible share of
  the 500 ITM candle, so its true slippage is nearer the house row or worse. 200 ITM trades freely and is the
  practical strike; its true cost lies between its tight and house rows.
- **Slippage is still the swing factor**: at 200 ITM, Rs 4.36 lakh at tight against Rs 1.97 lakh at house.
- **Drawdown is confirmed, not removed.** Rs 3.0 to 3.4 lakh on three lots at 200 ITM, about Rs 1 lakh a
  lot, against a net of Rs 0.7 to 1.6 lakh a lot over 2.8 years. 2025 is flat to negative and 2026H2 is
  negative in every row.

Rupee figures for 2024 use a 65-unit lot. Bid-ask spreads are still not observed; only live quotes or tick
data can settle the slippage question.

```
Strategy: Self-Aware Trend (SATS), NIFTY intraday
Round: 6 of 12 (no parameter changed; modelled premiums replaced by recorded option candles)
Params this round: preset Swing, 25 minutes, script exits, 3 lots bought 200 points ITM
Result: tight slippage Rs 4,35,905 net, max DD Rs 3,06,603, win rate 45.1%, Sharpe 0.80; house slippage Rs 1,97,192, max DD Rs 3,40,106, Sharpe 0.36; 526 trades
Verdict: FAIL on drawdown (as large as two to four years of profit per lot); the index signal and the premium model both stand
Next step: escalate to a human: paper-trade the baseline at 200 ITM to measure real slippage, or stop here
```
