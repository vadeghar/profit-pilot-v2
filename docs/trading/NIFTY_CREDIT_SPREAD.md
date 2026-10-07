# NIFTY Trend-Filtered Credit Spread (B1) — backtest report and decision

**Decision: NO GO.** On 349 weekly trades priced on recorded option candles (2020 to Oct-2026) the rules
lose money in six years of seven, and so do all fourteen variants tried. Not worth adding to the application.

Date: 7-Oct-2026. Code: `trading_strategies/nifty_credit_spread/`. Tests: `tests/test_nifty_credit_spread.py`.
Numbers: `python -m trading_strategies.nifty_credit_spread.backtest --study all`, archived as
`data/backtests/nifty_credit_spread_all_20261007.json`.

## 1. The strategy

Each weekly cycle, sell one out-of-the-money spread on the side the trend protects.

1. **Cycle**: enter at 10:00 on the first trading day after a weekly expiry, in the contract that expires at
   the end of that cycle (four or five trading days away).
2. **Trend**: Nifty's previous close at or above its 50-day average: sell a put spread. Below: a call spread.
3. **Strikes**: the short strike is one expected move from spot (spot x India VIX x sqrt(days / 365)),
   rounded away to a 50-point strike; the long strike is 200 points further out.
4. **Exits**: buy back at half the credit (target), or when the spread costs twice the credit (stop), checked
   on every 5-minute close; otherwise at 15:10 on expiry day.

One lot. The rules were written down before the first run and not changed.

## 2. How it was tested

- **Prices**: recorded 5-minute closes of the actual option contracts from Breeze, 2,860 contracts in all.
  No premium model anywhere.
- **Period**: 353 weekly cycles, Jan-2020 to 6-Oct-2026 (6.8 years), including the March 2020 crash.
  349 traded; 4 skipped for a missing quote or no credit.
- **Costs**: Rs 20 an order (four orders a trade), the real STT, exchange, stamp and GST table, and slippage
  on every fill. "Middle" slippage (Rs 0.30 or 0.3% a fill) is used unless stated.
- **Size**: one lot of today's 65 units throughout, Rs 65,000 fixed capital, no reinvestment.
- **Windows**: `early` 2020-2022, `late` 2023-2026.

## 3. Results

| | Full 2020-2026 | Early 2020-2022 | Late 2023-2026 |
|---|---|---|---|
| Net result | **−Rs 54,361** | −Rs 17,625 | −Rs 36,736 |
| Return on Rs 65,000 a year | **−12.4%** | −9.0% | −15.1% |
| Max drawdown | Rs 64,409 (88%) | Rs 26,815 | Rs 38,923 |
| Trades | 349 | 155 | 194 |
| Win rate | 69.3% | 72.3% | 67.0% |
| Average win / loss | Rs 379 / Rs 1,366 | Rs 399 / Rs 1,449 | Rs 363 / Rs 1,310 |
| Profit factor | 0.63 | 0.72 | 0.56 |
| Worst trade | −Rs 9,086 | −Rs 3,847 | −Rs 9,086 |
| Longest losing streak | 5 | 5 | 5 |

**By year (Rs):** 2020 +5,536 · 2021 −13,563 · 2022 −9,599 · 2023 −6,354 · 2024 −8,947 · 2025 −9,120 ·
2026 −12,315.

**By side:** put spreads (228 trades) −46,900; call spreads (121 trades) −7,461.

**By exit:** 244 targets made +91,790; 105 stops lost −146,151. No trade reached expiry: every one hit the
target or the stop first, after 1.5 days on average.

## 4. Why it loses

- **The payoff is too lopsided for the win rate.** A target makes 7.3 points on average; a stop loses 19.8.
  That needs 73% winners to break even and the rules get 69%.
- **Stops overshoot.** The stop is set at a loss equal to the credit (14.5 points on average), but prices
  are checked every five minutes and the market gaps, so the average stop costs 19.8 points. The worst
  week (4-Apr-2025) lost 137 points on a 19-point credit.
- **The credit is small and the costs are fixed.** The average credit is 14.5 points, about Rs 940 on one
  lot. Charges are Rs 99 a trade, more than a tenth of it before slippage on four fills.

**Slippage is not the cause.** With no slippage at all it still loses:

| Slippage a fill | Net (Rs) | A year | Profit factor |
|---|---|---|---|
| None | −26,854 | −6.1% | 0.80 |
| Tight (Rs 0.15 or 0.1%) | −40,523 | −9.2% | 0.71 |
| Middle (Rs 0.30 or 0.3%) | −54,361 | −12.4% | 0.63 |
| House (Rs 0.50 or 0.5%) | −72,697 | −16.5% | 0.52 |

Before charges and slippage the 349 trades make about Rs 7,850 in 6.8 years, which is nothing. More lots
would spread the brokerage thinner but there is no edge underneath to keep.

## 5. Variants

Fourteen changes, one at a time, list fixed before running:

| Variant | Full net (Rs) | Profit factor | Early / Late (Rs) |
|---|---|---|---|
| Baseline | −54,361 | 0.63 | −17,625 / −36,736 |
| Trend: 20-day average | −44,845 | 0.68 | −8,146 / −36,699 |
| Trend: 200-day average | −58,137 | 0.60 | −37,358 / −20,779 |
| No filter: always put spreads | −80,393 | 0.53 | −39,883 / −40,510 |
| No filter: always call spreads | −35,930 | 0.70 | −6,817 / −29,113 |
| Hold to expiry, no target or stop | −32,357 | 0.88 | −2,533 / −29,823 |
| Stop only (2x) | −26,223 | 0.87 | −790 / −25,433 |
| Target only (50%) | −63,804 | 0.66 | −35,873 / −27,931 |
| 50% target, 3x stop | −44,989 | 0.70 | −30,256 / −14,732 |
| 75% target, 2x stop | −44,189 | 0.75 | −10,381 / −33,808 |
| Short strike 0.75 moves away | −39,852 | 0.81 | −3,972 / −35,881 |
| Short strike 1.25 moves away | −60,186 | 0.43 | −21,852 / −38,334 |
| 100 wide | −57,502 | 0.43 | −22,216 / −35,286 |
| 300 wide | −56,109 | 0.69 | −17,727 / −38,382 |
| Skip weeks with VIX below 13 | −36,871 | 0.67 | −16,982 / −19,889 |

Every variant loses in both halves. The trend filter does help against selling puts blindly (−54,361
against −80,393), but always selling calls did better than either, so the filter is not what the result
turns on. The least bad forms are the ones that let winners run to expiry, and they still lose.

## 6. What this says about the other option candidates

- Weekly out-of-the-money premium on NIFTY, sold one lot at a time, paid about nothing before costs over
  these 6.8 years. That weighs against **B2 (weekly iron condor)**, which is two of these spreads at once.
- It does not settle **B3 (broken-wing butterfly)** or **B4 (calendar)**, which earn differently, nor
  spreads held for a month, where the credit is several times larger against the same fixed costs.
- Not tested here: monthly expiries, entries later in the cycle, more than one lot.

## 7. Decision

```
Strategy: NIFTY Trend-Filtered Credit Spread (B1)
Round: 1 of 12 (rules as specified, no tuning)
Params this round: 50-day trend, short strike 1.0 expected move, 200 wide, 50% target, 2x stop, weekly, 1 lot
Result: -12.4% a year on Rs 65,000, Max DD 88%, Win rate 69.3%, Sharpe -1.11, 349 trades
Verdict: FAIL
Next step: stop and report; do not add to the application
```

**NO GO.** The loss is not a matter of one bad year, a cost assumption or a setting: it shows in six of seven
years, with zero slippage, and in all fifteen configurations. The code and the 2,860 cached option contracts
are kept, because the other NIFTY option candidates can reuse both.

This is a backtest report, not investment advice.
