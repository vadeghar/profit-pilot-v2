# Volume rules on the stock strategies (V1 and V2) — backtest report and decisions

**Decisions: V1 NO GO. V2 NO GO.** Neither volume rule improved its strategy in a way that holds up. Heavy
volume on a breakout day did not make breakouts more reliable, and filtering the momentum rotation by volume
traded return for a smaller drawdown without improving the ratio between them.

Date: 7-Oct-2026. Code: `volume_*` parameters and the `volume` / `volume_events` studies in
`investment_strategies/trend_breakout/` (V1) and `investment_strategies/momentum_rotation/` (V2).
Archived as `data/backtests/trend_breakout_volume*_20261007.json` and `momentum_rotation_volume*_20261007.json`.

Same data and money rules as A1 and A2: Yahoo adjusted daily candles for 499 of today's Nifty 500 members,
Jan-2011 to Oct-2026, Rs 65,000, whole shares, all delivery charges. "Volume" is rupee turnover (close x
shares traded). `early` is 2011-2018, `late` 2019-2026.

Each idea was tested twice: on every signal with no account in the way (does the stock do better afterwards?),
and as the strategy itself with the volume rule added.

---

## V1. Volume-confirmed breakout

**The claim** (volume price analysis, CAN SLIM): a breakout on heavy volume is genuine; one on light volume
tends to fail.

**The rule as specified**: the trend breakout (A2), buying a new 52-week closing high only if that day's
turnover is at least 1.5 times its average over the previous 50 days.

### What every breakout did next

4,545 fresh breakouts (a new 252-day closing high in a top-200 stock with none in the previous 20 days).
Return from the next open, minus the average of all 200 stocks over the same days:

| Volume on the breakout day | Breakouts | Next 20 days | Next 60 days | t (60 days) | Beat the average |
|---|---|---|---|---|---|
| Below 0.75x its average | 319 | +1.54% | **+3.27%** | 3.1 | 58% |
| 0.75x to 1.5x | 1,285 | −0.38% | −0.26% | −0.6 | 47% |
| 1.5x to 2.5x | 1,202 | +0.32% | +0.55% | 1.1 | 46% |
| 2.5x and above | 1,739 | +0.76% | **+1.66%** | 3.5 | 48% |
| All breakouts | 4,545 | +0.37% | +0.93% | 3.4 | 48% |

- The pattern is a U, not a staircase. The *lightest*-volume breakouts did best, the heaviest second, and
  ordinary volume worst. "More volume, better breakout" is not what the data shows.
- In every group fewer than six in ten breakouts beat the average stock; the gains come from a minority.
- It fades in the recent half. For 2019-2026 no group is statistically different from zero at 60 days
  (heaviest +1.13%, t 1.7; lightest +1.97%, t 1.0), and the typical (median) breakout lagged the average
  stock by about 2%.

### The strategy with the rule

| Volume rule | Exit below 50-day low: CAGR / DD | Early / Late CAGR | Exit below 100-day low: CAGR / DD | Early / Late CAGR |
|---|---|---|---|---|
| Any volume (A2 as tested) | 11.8 / 46.1 | 13.6 / 13.4 | 19.2 / 38.8 | 17.4 / 21.2 |
| At least 1.0x | 10.2 / 46.1 | 10.8 / 10.2 | 16.5 / 37.4 | 16.9 / 17.8 |
| **At least 1.5x (as specified)** | **11.8 / 39.8** | 19.1 / 7.3 | 17.0 / 40.0 | 20.5 / 12.8 |
| At least 2.0x | 14.6 / 38.1 | 16.0 / 12.1 | 18.4 / 32.0 | 17.9 / 18.9 |
| At least 3.0x | 17.3 / 28.8 | 13.1 / 18.8 | 18.8 / 30.4 | 15.0 / 18.5 |
| Below 1.0x (the opposite) | 14.3 / 33.5 | 15.5 / 13.3 | 17.1 / 36.1 | 11.2 / 21.6 |
| 1.5x and close in the top third of the day | 11.9 / 43.4 | 19.6 / 5.0 | 18.9 / 34.7 | 24.4 / 13.3 |

- The specified rule returns exactly what A2 did (11.8%), better in the early half and worse in the late.
- Requiring *light* volume did as well as requiring heavy volume (14.3% against 14.6%).
- Only the 3x rule looks better (17.3%, drawdown 28.8%), and on the slower exit it adds nothing (18.8%
  against 19.2% with no rule). One cell of fourteen is not a finding.

```
Strategy: Volume-Confirmed Breakout (V1)
Round: 1 of 12 (rule as specified, plus its fixed list of thresholds)
Params this round: A2 rules, breakout-day turnover at least 1.5x its 50-day average
Result: CAGR 11.8% (about 7% after survivorship), Max DD 39.8%, Win rate 42.7%, Sharpe 0.68, 199 trades
Verdict: FAIL
Next step: stop and report
```

**NO GO.** The volume condition does not make the breakout strategy better, and the signal-level test does
not support the claim it rests on.

---

## V2. Momentum rotation with a volume filter

**The claim** (Lee and Swaminathan, 2000): among stocks that have risen strongly, those on low volume keep
rising and those on heavy volume reverse sooner.

**The rule as specified**: the momentum rotation (A1), dropping from the ranked list any stock whose median
daily turnover over the last month is more than 2 times its median over the last year.

### What the strongest stocks did next, by volume

Each month-end the 30 highest-ranked stocks were split into thirds by that volume ratio. Return over the next
month, minus the average of the 30:

| Group | Median volume ratio | Next month, full | t | Early | Late |
|---|---|---|---|---|---|
| Lowest third (quiet) | 1.06 | −0.21% | −0.9 | −0.34% | −0.07% |
| Middle third | 1.52 | −0.16% | −0.8 | +0.03% | −0.35% |
| Highest third (heavy) | 2.64 | +0.37% | 1.4 | +0.31% | +0.42% |

No significant difference, and the direction is the opposite of the claim: the heavily traded winners did
slightly better, not worse. Note that strong stocks are nearly all trading above their norm; even the
quietest third is at 1.06.

### The strategy with the rule

| Volume rule | Full CAGR / DD / Sharpe | Early | Late |
|---|---|---|---|
| No rule (A1 as tested) | 24.8 / 35.9 / 1.11 | 26.1 / 28.5 / 1.17 | 22.9 / 36.5 / 1.03 |
| **Skip above 2x (as specified)** | **24.0 / 30.6 / 1.29** | 19.0 / 23.2 / 1.14 | 28.4 / 18.9 / 1.41 |
| Skip above 1.5x | 19.7 / 31.5 / 1.13 | 18.9 / 24.0 / 1.15 | 20.0 / 19.0 / 1.10 |
| Skip above 3x | 25.8 / 37.7 / 1.28 | 19.7 / 27.4 / 1.06 | 31.2 / 25.6 / 1.45 |
| Only at or below normal volume (1.0x) | 23.4 / 25.8 / 1.30 | 23.5 / 17.7 / 1.42 | 22.0 / 18.9 / 1.16 |
| Only at 1.2x or more (the opposite) | 27.5 / 28.7 / 1.20 | 21.3 / 28.7 / 1.01 | 32.1 / 28.8 / 1.33 |
| Only at 2x or more | 23.8 / 34.7 / 1.05 | 22.9 / 30.0 / 1.07 | 22.9 / 34.8 / 0.99 |

At first sight two rows look like improvements: the specified rule (same return, smaller drawdown) and "only
at or below normal volume" (Sharpe higher in both halves). Two checks say otherwise.

**A rule and its opposite both "work".** Keeping only heavily traded stocks (1.2x or more) raised the return
to 27.5% and cut the drawdown too. When opposite filters both look good, the differences are noise from
holding six stocks.

**The best-looking row does not survive its neighbours.** The 1.0x threshold with thresholds either side, and
with more holdings:

| Stocks held | No rule | At or below 0.8x | 1.0x | 1.2x | 1.5x |
|---|---|---|---|---|---|
| 6 | 24.8 / 35.9 | 8.2 / 43.5 | **23.4 / 25.8** | 20.3 / 41.6 | 19.7 / 31.5 |
| 8 | 22.6 / 32.5 | 7.8 / 42.4 | 17.1 / 25.8 | 18.2 / 36.7 | 18.0 / 32.8 |
| 10 | 22.0 / 34.6 | 5.8 / 44.6 | 15.7 / 26.7 | 16.8 / 33.1 | 17.7 / 29.0 |

(Full-period CAGR / drawdown.) The 6-stock, 1.0x cell is the only one of twelve that keeps the return.
Everywhere else a quiet-volume rule costs 4 to 16 points a year.

**What is consistent** is smaller: rules that remove heavily traded stocks make the portfolio calmer. Daily
volatility falls from 22.8% to about 18%, and the late-window drawdown from 36.5% to about 19%. But the return
usually falls with it and the Sharpe ratio stays in the same 1.0-1.3 range. That is a dial for risk, the same
kind as holding 10 stocks instead of 6, not an edge.

```
Strategy: Momentum Rotation with a Volume Filter (V2)
Round: 1 of 12 (rule as specified, its fixed list, and one neighbour check)
Params this round: A1 rules, skip stocks whose 1-month median turnover exceeds 2x their 12-month median
Result: CAGR 24.0% (A1: 24.8%), Max DD 30.6% (A1: 35.9%), Sharpe 1.29 (A1: 1.11); early window CAGR 19.0% against 26.1%
Verdict: FAIL as an improvement
Next step: keep A1 as it is; do not add a volume filter
```

**NO GO.** The claim is not confirmed on these stocks, the specified rule is worse in the early window, and
the one variant that looked better is a single lucky cell. The momentum rotation stays as tested.

---

## What this does and does not say about volume

- It tests two specific, mechanical uses of daily volume on liquid Indian stocks. It does not test reading
  volume bar by bar on a chart, which is how volume price analysis is taught and which cannot be backtested
  as written.
- Volume here is total turnover. **Delivery percentage** (V4), which separates shares actually bought to
  hold from intraday churn, is a different measurement and is untested; it needs NSE's delivery files.
- The **high-volume return premium** (V3: unusually heavy volume predicts a rise over the next month) is also
  untested as a strategy. The V1 signal test is a partial look at it: the heaviest-volume breakouts did beat
  the average stock by 1.7% over 60 days across the full period, but not significantly in 2019-2026.
- The survivorship bias in the stock list applies to every number here, as in A1 and A2.

This is a backtest report, not investment advice.
