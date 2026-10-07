# Volume Shock / High-Volume Return Premium (V3) — backtest report and decision

**Decision: NO GO.** The effect the research describes is visible in liquid Indian stocks but tiny: a stock
that has just traded at three times its usual volume beat the average stock by about 0.3% over the next 20
days. A monthly strategy built on it made 6.3% a year with a 47% drawdown, below the Nifty 50.

Date: 7-Oct-2026. Code: `investment_strategies/volume_shock/`. Tests: `tests/test_volume_shock.py`.
Numbers: `python -m investment_strategies.volume_shock.backtest --study all`, archived as
`data/backtests/volume_shock_all_20261007.json`.

## 1. The claim and the strategy

Gervais, Kaniel and Mingelgrin (Journal of Finance, 2001): stocks with unusually high trading volume over a
day or a week tended to rise over the following month, whatever the price did during the burst.

Rules, decided at the close of the last trading day of a month and traded at the next open:

1. **Universe**: the 200 most-traded NSE stocks over the last six months.
2. **Shock**: average daily turnover of the last 5 days divided by the median of the 50 days before. A
   candidate has a shock of 2 or more.
3. **Selection**: the six largest shocks, equal slots, replaced every month. Price direction is ignored.
4. **Regime**: cash while the Nifty 50 is below its 200-day average, as in the momentum rotation.

Rules written down before the first run. Same data, Rs 65,000 account and delivery charges as A1.

## 2. Does the effect exist? Every stock, every week

Each of the 200 stocks was looked at every fifth trading day and grouped by its volume shock. Return from the
next open to the close 20 days later, minus the average of the 200 over the same days. Each date counts once
in the t-statistic.

| Volume shock | Stock-weeks | Next 20 days, full | t | Early | Late |
|---|---|---|---|---|---|
| Below 0.5x | 5,694 | +0.29% | 1.2 | +0.11% | +0.48% |
| 0.5x to 0.8x | 30,456 | −0.11% | −1.2 | −0.05% | −0.17% |
| 0.8x to 1.25x (normal) | 57,499 | −0.01% | −0.2 | −0.06% | +0.05% |
| 1.25x to 2x | 38,897 | +0.01% | 0.2 | +0.04% | −0.02% |
| 2x to 3x | 13,735 | +0.15% | 1.7 | +0.30% | −0.01% |
| 3x and above | 9,360 | **+0.31%** | 2.0 | +0.20% | +0.43% |
| 2x and above, price up during the shock | 15,341 | +0.17% | 1.2 | +0.22% | +0.11% |
| 2x and above, price down during the shock | 7,730 | +0.21% | 1.5 | +0.31% | +0.11% |

- The direction matches the research: the heavier the recent volume, the better the next 20 days, and it does
  not depend on whether the price rose or fell during the burst.
- The size does not. The best group earns 0.31% over 20 days, with a t-statistic of 2.0 across 773 dates. The
  2x-to-3x group is zero in the recent half.
- A monthly round trip on this account costs about 1% (see the stock pullback report). The edge is a third of
  that.

## 3. The strategy

| | Full 2011-2026 | Early 2011-2018 | Late 2019-2026 |
|---|---|---|---|
| CAGR | **6.3%** | 4.5% | 8.7% |
| Max drawdown | **47.4%** | 36.3% | 42.4% |
| Sharpe | 0.41 | 0.33 | 0.50 |
| Losing years | 6 of 16 | 4 | 2 |
| Trades (a year) | 765 (48) | 379 | 374 |
| Win rate | 49.2% | 49.9% | 47.9% |
| Average win / loss | +10.1% / −7.8% | +9.5% / −7.9% | +10.9% / −7.6% |
| Nifty 50 CAGR / drawdown (price index) | 8.6% / 38.4% | 7.4% / 26.2% | 9.9% / 38.4% |

Before any cost it makes 13.9% a year; replacing six stocks every month (48 trades a year) takes more than
half of that. The survivorship bias in the stock list flatters both figures.

**Year by year (%)**

| | 2011 | 2012 | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Volume shock | −3.3 | 7.1 | −6.5 | 55.0 | 9.3 | −1.4 | 18.9 | −26.1 | −12.2 | 24.7 | 5.9 | −0.7 | 16.9 | 14.2 | 19.0 | 1.4 |
| Nifty 50 | −24.9 | 27.7 | 6.8 | 31.4 | −4.1 | 3.0 | 28.6 | 3.2 | 12.0 | 14.9 | 24.1 | 4.3 | 20.0 | 8.8 | 10.5 | −13.5 |

## 4. Variants

Twelve changes, one at a time, list fixed before running:

| Variant | Full CAGR / DD | Early | Late |
|---|---|---|---|
| Baseline | 6.3 / 47.4 | 4.5 / 36.3 | 8.7 / 42.4 |
| Shock of 1.5x or more | 6.3 / 47.4 | 4.5 / 36.3 | 8.7 / 42.4 |
| Shock of 3x or more | 6.3 / 45.8 | 4.7 / 36.6 | 7.5 / 34.9 |
| 1-day shock | −4.4 / 74.5 | −5.5 / 48.2 | 4.2 / 49.5 |
| 20-day shock | 13.8 / 44.9 | 11.3 / 44.9 | 14.7 / 35.4 |
| Only shocks with the price up | 8.3 / 49.2 | 2.3 / 43.5 | 16.0 / 32.6 |
| Only shocks with the price down | 0.2 / 45.8 | −2.3 / 43.8 | 4.0 / 39.0 |
| Hold 3 months | 11.3 / 44.4 | 15.2 / 30.3 | 6.3 / 44.0 |
| No market filter | 9.6 / 48.4 | 8.5 / 35.0 | 9.6 / 43.9 |
| 4 stocks | 8.9 / 36.3 | 9.4 / 36.3 | 8.0 / 35.8 |
| 10 stocks | 0.4 / 59.9 | −2.8 / 46.5 | 8.8 / 37.9 |
| Universe top 100 | −7.3 / 78.2 | −9.3 / 57.5 | 1.8 / 47.9 |
| Universe top 300 | 14.4 / 37.8 | 12.3 / 37.5 | 15.5 / 28.4 |

The best variants (a 20-day shock, a 300-stock universe, holding three months) reach 11-14% before the
survivorship haircut, with drawdowns of 38-45%. None approaches the momentum rotation (24.8% / 35.9%). The
1.5x threshold gives the same result as 2x because the six largest shocks in any month are always above 2.

## 5. Decision

```
Strategy: Volume Shock (V3)
Round: 1 of 12 (rules as specified, no tuning)
Params this round: top 200 by turnover, 5-day turnover at least 2x the prior 50-day median, 6 largest, monthly, cash below the Nifty 200-day average
Result: CAGR 6.3%, Max DD 47.4%, Win rate 49.2%, Sharpe 0.41, 765 trades (before costs 13.9%)
Verdict: FAIL
Next step: stop and report; do not add to the application
```

**NO GO.** The premium is real in direction and too small in size: 0.3% over 20 days against a trading cost
of about 1%. No variant fixes that.

## 6. Where the volume tests stand

| | Idea | Signal test | Strategy | Decision |
|---|---|---|---|---|
| V1 | Heavy volume confirms a breakout | U-shaped, not confirmed | 11.8% / 39.8% | NO GO |
| V2 | Low-volume winners keep winning | Not confirmed (slightly the reverse) | 24.0% / 30.6%, no better than A1 | NO GO |
| V3 | Heavy volume predicts a rise | Confirmed in direction, 0.3% in size | 6.3% / 47.4% | NO GO |

Across all three, total daily turnover carries very little information about the next month in these 200
liquid stocks. Still untested: delivery percentage (V4), which measures something different, and the
chart-reading forms of volume analysis, which cannot be backtested as written.

This is a backtest report, not investment advice.
