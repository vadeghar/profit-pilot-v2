# Trend Breakout (A2) — backtest report and decision

**Decision: NO GO.** Buying 52-week highs and trailing a 50-day low made 11.8% a year in the backtest with a
46% drawdown. After the same survivorship haircut as the momentum rotation that is about 7%, less than
holding the Nifty 50 with dividends. It is also the momentum rotation's poorer cousin: it loses in the same
years and does not diversify it.

Date: 7-Oct-2026. Code: `investment_strategies/trend_breakout/`. Tests: `tests/test_trend_breakout.py`.
Numbers: `python -m investment_strategies.trend_breakout.backtest --study all`, archived as
`data/backtests/trend_breakout_all_20261007.json`.

## 1. The strategy

Buy a liquid stock when it closes at a new 52-week high; hold it until its trend breaks. Decided at a close,
traded at the next open.

1. **Universe**: the 200 most-traded NSE stocks over the last six months, refreshed monthly.
2. **Market**: no new buys while the Nifty 50 is below its 200-day average. Holdings stay until their own exit.
3. **Buy**: the stock closes at its highest close of the last 252 days. Six equal slots; with more breakouts
   than free slots, the strongest 6-month return first.
4. **Sell**: a close below the lowest close of the previous 50 days.

Rules written down before the first run and not changed.

## 2. How it was tested

Same data and money rules as the momentum rotation (A1): Yahoo adjusted daily candles for 499 of today's
Nifty 500 members, Jan-2011 to Oct-2026, Rs 65,000, whole shares, idle cash earning nothing, Rs 20 an order,
STT 0.1% each side, stamp duty, exchange fees, GST, depository charge, 0.1% slippage each side. `early` is
2011-2018, `late` 2019-2026, each started with fresh capital.

A data fault was found during this test and fixed for all three stock strategies: Yahoo's history carried a
few empty Saturday rows in 2012 which blanked every 200- and 252-day window for the following year.

## 3. Results

| | Full 2011-2026 | Early 2011-2018 | Late 2019-2026 |
|---|---|---|---|
| CAGR | **11.8%** | 13.6% | 13.4% |
| Max drawdown | **46.1%** | 22.4% | 37.9% |
| Sharpe | 0.68 | 0.80 | 0.72 |
| Losing years | 7 of 16 | 3 | 3 |
| Trades | 202 | 94 | 103 |
| Win rate | 43.6% | 50.0% | 40.8% |
| Average win / loss | +35.4% / −12.6% | +29.3% / −11.5% | +43.4% / −13.5% |
| Profit factor | 1.59 | 2.41 | 1.57 |
| Average hold | 97 days | 106 | 93 |
| Days invested | 92% | 94% | 90% |
| Nifty 50 CAGR / drawdown (price index) | 8.6% / 38.4% | 7.4% / 26.2% | 9.9% / 38.4% |

Costs are not the issue here: with no costs at all it makes 13.4%. Thirteen trades a year, held three months.

**Year by year (%)**

| | 2011 | 2012 | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Breakout | −22.0 | 41.1 | 20.8 | 49.9 | −0.7 | 10.2 | 39.3 | −8.4 | −8.7 | 19.1 | 41.0 | −8.3 | 50.4 | 43.5 | −12.7 | −21.1 |
| Nifty 50 | −24.9 | 27.7 | 6.8 | 31.4 | −4.1 | 3.0 | 28.6 | 3.2 | 12.0 | 14.9 | 24.1 | 4.3 | 20.0 | 8.8 | 10.5 | −13.5 |

The equity peaked on 24-Sep-2024 and is 35.7% below that now. The deepest fall (46.1%) bottomed in March
2020. Rolling windows: 32% of one-year windows lose; the worst three years lost 11% a year; the worst five
years lost 3.2% a year. Only 38% of five-year windows reached 20% a year.

## 4. Why it is not good enough

- **After the survivorship haircut it does not beat the index.** The stock list is today's Nifty 500. For the
  momentum rotation that bias was measured at about 5 points a year (27.4% for a rebuilt NSE momentum index
  against 22% published). Taking the same off 11.8% leaves about 7%, against roughly 10% for the Nifty 50
  with dividends. The haircut was not measured separately for this strategy.
- **A 46% drawdown for that return.** The rule keeps holdings through a falling market and waits for each
  stock's own 50-day low, so it rode March 2020 down.
- **The result depends on which variant is picked** (section 5): from 5.6% to 24.3% a year.
- **Winners are rare and large.** The median trade loses 3.9%. The best tenth of the trades made 186% of the
  total profit; the other nine tenths lost money between them.

## 5. Variants

Fifteen changes, one at a time, list fixed before running:

| Variant | Full CAGR / DD | Early | Late |
|---|---|---|---|
| Baseline | 11.8 / 46.1 | 13.6 / 22.4 | 13.4 / 37.9 |
| Entry: 6-month high | 12.1 / 41.7 | 12.0 / 23.7 | 13.2 / 32.5 |
| Entry: 55-day high | 11.4 / 42.5 | 9.0 / 24.3 | 16.8 / 29.8 |
| Exit: below 20-day low | 14.5 / 39.1 | 10.0 / 23.7 | 18.4 / 40.4 |
| Exit: below 100-day low | 19.2 / 38.8 | 17.4 / 29.2 | 21.2 / 34.8 |
| Exit: below 50-day average | 15.6 / 37.9 | 13.9 / 23.9 | 15.7 / 40.1 |
| Exit: below 200-day average | 17.9 / 44.0 | 17.4 / 24.3 | 21.0 / 43.7 |
| Exit: chandelier, 3 ATR | 10.1 / 42.8 | 3.7 / 30.0 | 16.8 / 28.3 |
| Market filter ignored | 13.2 / 50.2 | 9.7 / 43.1 | 16.0 / 38.7 |
| Market: sell everything below the 200-day | 12.8 / 44.5 | 8.1 / 38.2 | 17.5 / 32.0 |
| 4 slots | 13.1 / 45.6 | 12.7 / 25.8 | 15.3 / 30.8 |
| 8 slots | 12.0 / 43.3 | 13.3 / 23.4 | 11.3 / 33.1 |
| 10 slots | 13.7 / 36.8 | 14.7 / 22.2 | 10.9 / 27.6 |
| Universe top 100 | 5.6 / 52.1 | 10.4 / 30.6 | 1.6 / 40.8 |
| Universe top 300 | 24.3 / 54.7 | 20.9 / 36.5 | 31.4 / 28.4 |
| Buy the most traded first | 8.5 / 42.1 | 13.8 / 22.1 | 5.4 / 26.6 |

Slower exits help (100-day low: 19.2%; 200-day average: 17.9%), and so does reaching into less liquid
stocks (top 300: 24.3%, with a 55% drawdown and the largest exposure to the survivorship bias). The spread
between neighbours is the finding: the momentum rotation's fourteen variants all landed between 15% and 35%
in both halves; these land between 1.6% and 31%. The best exit variant, taken on the early window as the
protocol allows, would give 19.2% before the haircut with a 39% drawdown, still below the rotation on both.

Account size barely matters (11.3% to 12.3% from Rs 50,000 to Rs 5 lakh); 0.5% brokerage gives 9.8%.

## 6. Is it a second strategy or the same one?

| | Momentum rotation (A1) | Trend breakout (A2) | Half in each |
|---|---|---|---|
| CAGR | 24.8% | 11.8% | 20.7% |
| Max drawdown | 35.9% | 46.1% | 35.7% |
| Sharpe | 1.11 | 0.68 | 1.05 |

Monthly returns of the two are 0.56 correlated, both fall in 19% of months, and both lost in 2019, 2022,
2025 and 2026. Splitting the account lowers the return and leaves the drawdown where it was. They are the
same bet on strong stocks, and the rotation makes it better: it ranks every month and replaces laggards
instead of waiting for a 50-day low.

## 7. What it holds

- Sectors by time held: Healthcare 13%, Autos 11%, FMCG 11%, Consumer Durables 10%, Financial Services 10%,
  IT 9%, Metals 8%. 132 different stocks in 202 trades.
- Median holding 76 days; a quarter of trades last more than four months.
- Best trades: Laurus Labs +361% (2020), Dixon +218% (2020), Aurobindo +190% (2013), REC +161% (2023).
- Worst: Vedanta −61% (Jan-2026; a demerger that the adjusted prices show as a fall, so partly a data
  artefact), Escorts −37% and Jubilant FoodWorks −32% (both bought in February 2020).

## 8. Decision

```
Strategy: Trend Breakout (A2)
Round: 1 of 12 (rules as specified, no tuning)
Params this round: top 200 by turnover, new 252-day closing high, exit below the 50-day low, 6 slots, no new buys below the Nifty 200-day average
Result: CAGR 11.8% (about 7% after survivorship), Max DD 46.1%, Win rate 43.6%, Sharpe 0.68, 202 trades
Verdict: FAIL
Next step: stop and report; do not add to the application
```

**NO GO.** It is below the 20% target before any haircut, below the index after it, carries a deeper drawdown
than the rotation, swings widely with its settings, and adds nothing alongside the rotation. If a trend
strategy in stocks is wanted, the momentum rotation is the one to carry forward.

This is a backtest report, not investment advice.
