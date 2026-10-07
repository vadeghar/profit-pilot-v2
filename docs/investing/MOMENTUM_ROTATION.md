# Momentum Rotation (A1) — backtest report and decision

**Decision: GO, as a paper-traded investment strategy. Expect about 17-19% a year with a 35-40% drawdown,
not a steady 20%.** It is not ready for the dashboard as a live strategy until it has been forward-tested.

> Figures corrected on 7-Oct-2026 after a data fault was fixed: Yahoo's history carried a few empty
> Saturday rows in 2012 that blanked the 200- and 252-day windows for the following year. The decision is unchanged.

Date: 7-Oct-2026. Code: `investment_strategies/momentum_rotation/`. Tests: `tests/test_momentum_rotation.py`.
Numbers: `python -m investment_strategies.momentum_rotation.backtest --study all`, archived as
`data/backtests/momentum_rotation_all_20261007b.json`.

## 1. The strategy

Once a month, hold the six strongest of the 200 most-traded NSE stocks; hold cash when the market is falling.
Decided at the close of the last trading day of the month, traded at the next open.

1. **Universe**: the 200 stocks with the highest median daily turnover over the last six months, with at
   least 13 months of prices. Measured as of that day.
2. **Score**: NSE's Nifty200 Momentum 30 method. 6-month and 12-month price return, each divided by the
   stock's one-year volatility, turned into z-scores across the universe and averaged.
3. **Regime**: if the Nifty 50 closes the month below its 200-day average, sell everything and wait in cash.
4. **Selection**: six equal rupee slots. A holding is kept while it ranks in the top 12; freed slots go to
   the highest-ranked stocks not held. A stock whose single share costs more than a slot is skipped.

No rule was tuned. These are the rules written down before the first run.

## 2. How it was tested

- **Data**: Yahoo daily prices adjusted for splits and dividends, 499 of today's Nifty 500 members,
  Jan-2009 to 7-Oct-2026. The backtest runs Jan-2011 to Oct-2026 (15.8 years).
- **Account**: Rs 65,000, whole shares only, idle cash earns nothing.
- **Costs**: Rs 20 brokerage an order, STT 0.1% each side, stamp duty, exchange and SEBI fees, GST, Rs 18.5
  depository charge a sale, and 0.1% slippage each side.
- **Windows**: `early` 2011-2018 and `late` 2019-2026, each started with fresh capital.
- **Benchmark**: Nifty 50 price index (add about 1.3% a year for dividends).

## 3. Results

| | Full 2011-2026 | Early 2011-2018 | Late 2019-2026 |
|---|---|---|---|
| CAGR | **24.8%** | 26.1% | 22.9% |
| Max drawdown | **35.9%** | 28.5% | 36.5% |
| Sharpe | 1.11 | 1.17 | 1.03 |
| Losing years | 5 of 16 | 1 | 4 |
| Trades | 253 | 122 | 124 |
| Win rate | 47.0% | 49.2% | 43.5% |
| Average win / loss | +38.6% / −10.1% | +35.3% / −9.8% | +44.9% / −10.2% |
| Profit factor | 1.95 | 2.90 | 1.86 |
| Trades a year | 16 | 15 | 16 |
| Months invested | 71% | 71% | 71% |
| Nifty 50 CAGR / drawdown | 8.6% / 38.4% | 7.4% / 26.2% | 9.9% / 38.4% |

Rs 65,000 became Rs 21.4 lakh in the backtest. Fees were Rs 92,277 in total; costs and whole-share rounding together take about 2 points a year (26.8% without them).

**Year by year (%)**

| | 2011 | 2012 | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Strategy | 8.7 | 29.7 | 7.0 | 85.1 | 0.5 | −0.9 | 117.7 | 5.6 | −11.3 | 82.8 | 91.7 | −0.4 | 75.3 | 27.1 | −14.1 | −13.3 |
| Nifty 50 | −24.9 | 27.7 | 6.8 | 31.4 | −4.1 | 3.0 | 28.6 | 3.2 | 12.0 | 14.9 | 24.1 | 4.3 | 20.0 | 8.8 | 10.5 | −13.5 |

The return comes in bursts. Five years (2014, 2017, 2020, 2021, 2023) made 75% or more; the other eleven
average 3.5%. Rolling windows: over any one year the median is +18.1%, and 26% of one-year windows lose
money; over any three years the worst was +7.0% a year and 90% of windows made 20% or more.

**It is in its worst drawdown now.** The backtest equity peaked on 11-Jul-2024 and is 35.9% below that
today. 2025 lost 14.1% while the Nifty gained 10.5%. The rules have been in cash since February 2026.

## 4. How much of this is real

**Survivorship bias: about 5 points a year.** The stock list is today's Nifty 500, so companies that were
dropped or delisted are missing. To measure the effect, NSE's own Nifty200 Momentum 30 index was rebuilt from
this data (30 stocks, half-yearly, no costs): it returns **27.4%** a year for the 15 years to June 2026,
against the **22%** the fund houses publish for the real index. Taking 5 points off:

| | Backtest | After the survivorship haircut |
|---|---|---|
| Full period | 24.8% | about 19% |
| Late period | 22.9% | about 18% |

The gap may be larger recently: since June 2024 the rebuilt index made +2.7% a year while a real momentum ETF
lost 7.7%. That is two years and one ETF, so it is a warning rather than a measurement. Only 296 of the 499
stocks have prices in 2011, which is where the bias comes from.

**Robustness: good.** Fourteen variants, one rule changed at a time, list fixed before running:

| Variant | Full CAGR / DD | Early | Late |
|---|---|---|---|
| Baseline | 24.8 / 35.9 | 26.1 / 28.5 | 22.9 / 36.5 |
| 4 stocks | 19.7 / 43.7 | 22.3 / 33.4 | 17.1 / 38.9 |
| 8 stocks | 22.6 / 32.5 | 23.3 / 29.3 | 22.3 / 33.0 |
| 10 stocks | 22.0 / 34.6 | 22.5 / 26.9 | 21.1 / 27.8 |
| Score: 12-month skipping the last | 23.2 / 48.5 | 26.3 / 30.2 | 19.4 / 48.4 |
| Score: 6-month | 22.4 / 45.0 | 26.4 / 28.4 | 18.5 / 44.9 |
| Regime ignored | 33.4 / 55.7 | 29.9 / 27.5 | 34.8 / 56.1 |
| Regime: no new buys | 28.4 / 50.8 | 26.7 / 29.1 | 31.1 / 51.3 |
| Regime: 100-day average | 30.6 / 32.1 | 28.4 / 28.0 | 31.3 / 33.0 |
| No holding buffer | 20.1 / 43.6 | 22.7 / 32.1 | 17.3 / 37.4 |
| Holding buffer 3x | 23.6 / 32.6 | 26.4 / 29.2 | 20.9 / 33.9 |
| Universe top 100 | 19.8 / 31.7 | 19.5 / 22.6 | 20.3 / 21.2 |
| Universe top 300 | 22.4 / 39.5 | 27.9 / 27.9 | 15.4 / 40.3 |
| Rebalance every 3 months | 24.4 / 41.4 | 23.0 / 28.1 | 25.0 / 41.4 |

Every variant is between 15% and 35% in both halves. The baseline is in the middle, so it is not a lucky
setting. The cash rule costs return (33.4% without it) and buys a much smaller drawdown (35.9% against 55.7%).
The baseline is kept as specified; no variant was adopted.

**Account size: not a constraint.** Rs 50,000, 65,000, 80,000 and 5,00,000 give 25.0%, 24.8%, 24.8% and
25.4%. What matters is the broker's delivery fee: 0.5% brokerage instead of Rs 20 cuts the result to 21.5%.
Slippage of 0.3% a side instead of 0.1% gives 23.5%.

## 5. What it holds

- **Type of stock**: liquid mid and large caps in a strong, steady uptrend. Median liquidity rank 111 of 200;
  only 23% of holdings are from the 50 most-traded names. Median share price Rs 357.
- **Sectors over 16 years**: Financial Services 18%, Capital Goods 13%, Healthcare 12%, IT 8%, Chemicals 7%,
  FMCG 7%, Autos 7%, Power 6%, Consumer Durables 6%. No sector dominates; it follows whatever is leading.
- **Turnover**: 167 different stocks held in 134 invested months. Median holding 63 days; half of all trades
  last between one and four months.
- **Winners carry it**: 47% of trades win, but the average win is +38% against an average loss of −10%.
  Best trades: REC +324% (2023), PCBL +268% (2017), HEG +240% (2017), Adani Total Gas +208% (2021).
  Worst: Wockhardt −31% (2015), Reliance Power −29% (2025), RVNL −27% (2023).
- **Today** (30-Sep-2026): the Nifty is below its 200-day average, so the rules hold nothing. The top-ranked
  names it would look at first when the market turns are STL Tech, Cupid, Welspun Corp, HFCL, Laurus Labs
  and MTAR Technologies. That is a ranking on one date, not a recommendation.

**What to consider if this is run:**

1. Stocks from the 200 most-traded on NSE only. No small or illiquid names, no stocks listed less than 13
   months.
2. Rank by 6- and 12-month return adjusted for volatility; a stock that has risen steadily ranks above one
   that has jumped.
3. Six to ten holdings in equal slots. Fewer than six was clearly worse (4 stocks: 19.7%, drawdown 43.7%).
4. Skip any stock whose single share costs more than one slot (about Rs 11,000 on a Rs 65,000 account).
5. Sell and stay in cash when the Nifty 50 ends a month below its 200-day average.
6. A flat-fee or zero-brokerage delivery plan. Percentage brokerage takes 3 points a year.

## 6. Decision

```
Strategy: Momentum Rotation (A1)
Round: 1 of 12 (rules as specified, no tuning)
Params this round: top 200 by turnover, NSE momentum score, 6 stocks, 2x holding buffer, cash below the Nifty 200-day average, monthly
Result: CAGR 24.8% (about 19% after survivorship), Max DD 35.9%, Win rate 47.0%, Sharpe 1.11, 253 trades
Verdict: GO for paper trading; NEEDS forward results before live
Next step: add as a paper-traded investment strategy; review after 6 months or at the first re-entry
```

**Why GO**

- Clears the Nifty 50 by more than 10 points a year in both halves of the history, after costs.
- Fourteen rule changes all stay profitable in both halves; nothing depends on one setting.
- Fits Rs 50,000-80,000 with no leverage and about 16 trades a year.
- Rests on a published, investable index with the same rules, not on a pattern found in this data.

**Why not an unqualified GO**

- The honest expectation is 17-19% a year, a little under the 20% target, and it arrives unevenly.
- A 36% drawdown has happened and is happening now; more than two years below the previous high.
- The survivorship estimate is a single comparison; the true haircut could be larger.
- Not validated on point-in-time index membership, which is the one test that would settle the bias.

**Conditions for going live**

1. Paper-trade from now. The rules are in cash, so the first live decision is the re-entry.
2. Decide beforehand whether a 35-40% fall in the account is acceptable. If not, this is a NO GO for you
   whatever the average return, and 8-10 stocks (drawdown 28-33% in the late window) is the milder form.
3. Check the broker's delivery brokerage; the result assumes Rs 20 an order.

This is a backtest report, not investment advice.
