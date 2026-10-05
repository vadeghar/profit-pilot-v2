# NIFTY Afternoon Momentum — research, rules and backtest

**Status: experimental. Not proven. Do not trade it with money yet.**

The brief was an intraday (non-scalping) strategy for Rs 50,000 that only buys index options. Three
published intraday signal families were tested on 297 sessions of real NIFTY 5-minute candles
(Mar-2025 to Oct-2026). None of them makes money buying options once decay and costs are counted.
The one variant that shows a profit — afternoon continuation on high-VIX days — earns all of it in a
single month (March 2026) and on modelled, not recorded, option prices. It is packaged here as the
best-supported candidate for paper trading, with the evidence against it written down.

## 1. What the research says

| Source | Finding | Relevance |
|---|---|---|
| Zarattini, Aziz & Barbon (2024), *Beat the Market: An Effective Intraday Momentum Strategy for SPY* | Trade a break of a time-of-day "noise" band around the open / previous close, trail on the band and VWAP. 19.6% a year, Sharpe 1.33, 2007-2024, on the ETF itself. | The noise-band family below. Tested on the underlying with leverage, not on bought options. |
| Zarattini & Aziz (2023), *Can Day Trading Really Be Profitable?* | 5-minute opening-range breakout on QQQ and on "stocks in play". | The opening-range family below. |
| Gao, Han, Li & Zhou (2018), *Market Intraday Momentum* | The first half-hour return predicts the last half-hour; stronger on volatile days. | The afternoon family, and the reason for a volatility gate. |
| Baltussen, Da, Lammers & Martens (2021), *Hedging Demand and Market Intraday Momentum* | The return up to the last 30 minutes predicts the last 30 minutes across 60+ futures; linked to dealers hedging short gamma. | Same, and why it should be stronger when dealers are short gamma (high-volatility selloffs). |
| SEBI studies (Sep-2024, Jul-2025) | 93% of individual F&O traders lost money FY22-FY24; 91% in FY25, net loss Rs 1.05 lakh crore. | The base rate for what is being attempted. |

All of the profitable results above are on the underlying. A bought option adds time decay, an IV that
falls as the index rises, and a round-trip cost of roughly 1-2% of the premium. That gap is what the
tests below measure.

## 2. What was tested

Data: NIFTY 50 5-minute candles from Breeze (continuous Jan-Sep 2026; about 12 sessions a month for
Feb-Dec 2025) plus Yahoo for the last days; India VIX and daily closes from Yahoo. 297 sessions have
the history the rules need. Option premiums are **modelled** (`pricing.py`): Black-Scholes with IV =
India VIX, decay in session-weighted variance time, IV moving against the index. Charges are the real
table in `backtest/charges.py`. One lot, fixed Rs 50,000.

Two cost settings: **house** (Rs 0.5 or 0.5% of premium per side, the scalper engine's no-quote
assumption) and **tight** (Rs 0.15 or 0.1%).

45 configurations were run in three rounds, each list fixed before running:

**Round 1 — the three families, no filter, house costs**

| Family | Trades | Win % | Profit factor | Net (Rs) | Max DD |
|---|---|---|---|---|---|
| Noise band, as published | 222 | 25.7 | 0.82 | −39,084 | 79% |
| Noise band, 5-minute exits | 243 | 19.8 | 0.81 | −32,666 | 68% |
| Opening range 15m | 272 | 30.9 | 0.59 | −1,77,772 | >100% |
| Opening range 30m | 246 | 35.4 | 0.74 | −97,681 | >100% |
| Afternoon, threshold 1.0 | 166 | 41.0 | 0.94 | −7,089 | 56% |

The noise band does capture index points (about +5 a trade) but that is worth roughly Rs 160 a lot
against Rs 170 of costs. The opening-range breakout loses on the index itself. Calls lose in every
family; what profit exists is on the put side.

**Round 2 — 27 configurations: band width x exit style x VIX gate, afternoon threshold x VIX gate.**
Without a gate everything loses. With VIX >= 15 the afternoon family turns positive at every
threshold (profit factor 1.62 / 1.21 / 1.62 at 1.0 / 1.5 / 2.0); the noise band stays around
break-even. Full table: `backtest.py --study grid`.

**Round 3 — 8 configurations: which kind of volatility gate.** "VIX above its own 20-day average" and
"yesterday's range was large" do not work for the afternoon family; only the absolute level does.
That one of three plausible gates works and two do not is a warning, not a confirmation.

**Longer sample, index only.** On ~3 years of hourly candles (721 sessions), the move from the previous
close to 14:15 continues to 15:15 by +5.8 points on days that moved 0.5% or more (hit rate 56%,
t = 1.56), and +8.8 points with VIX >= 15 (t = 1.25). Positive, small, not statistically significant,
and negative in 2025.

## 3. The rules

All times IST, NIFTY 50 index, 5-minute candles. Code: `trading_strategies/nifty_afternoon_momentum/strategy.py`.

1. **Regime gate** — India VIX previous close >= 15. Otherwise no trade that day.
2. **Signal at 14:25** — `r` = index / previous close − 1 at the close of the 14:20 candle. `sigma` =
   the 14-session average of |index / day open − 1| at that time of day. Trade only if |r| >= 1.0 x sigma.
3. **Entry** — r > 0 buys the ATM call, r < 0 the ATM put; nearest weekly expiry at least one day
   away (never an expiry-day contract). One lot. One trade a day.
4. **Exit** — 15:10, or earlier if the premium is 30% below the entry price.
5. **Capital** — skip the trade if one lot costs more than half the capital. No averaging, no re-entry.

## 4. Backtest of these rules

297 sessions, 2025-03-03 to 2026-10-05; 89 of them with VIX >= 15.

| Costs | Trades | Win % | Avg win / loss (Rs) | Profit factor | Net (Rs) | Return | Annualised | Max DD | Sharpe |
|---|---|---|---|---|---|---|---|---|---|
| House | 55 | 54.5 | 2,030 / 1,503 | 1.62 | +23,304 | 46.6% | 39.2% | 10.7% | 1.27 |
| Tight | 55 | 58.2 | 2,011 / 1,535 | 1.82 | +29,060 | 58.1% | 48.9% | 9.5% | 1.57 |

Those headline numbers are not the result. This is:

| Month | Trades | Net (Rs, house) |
|---|---|---|
| 2025-04 | 4 | −3,010 |
| 2025-05 | 3 | −1,213 |
| 2025-06 | 3 | −83 |
| **2026-03** | **18** | **+26,526** |
| 2026-04 | 14 | −214 |
| 2026-05 | 9 | +1,032 |
| 2026-06 | 4 | +265 |

- **One month is the whole profit.** Outside March 2026 the 37 other trades net −Rs 3,223. The five
  best trades add up to more than the total.
- **Puts carry it.** Calls −Rs 3,336, puts +Rs 26,639.
- **No trade since June 2026.** VIX has been under 15, so there is no recent or out-of-sample evidence.
- **Best of 45.** This is the best cell of everything tried; some of its edge is selection.
- **The premium model flatters it.** Against the only real option candles on disk (monthly calls, 219
  observations over the same 14:25-15:10 window) the model tracks well (correlation 0.95) but real
  premiums moved 0.85x as much as modelled, and the model overstated call gains by about Rs 1.7 a
  unit. Cutting winning trades by 15% takes the house result from +23,304 to +13,815. Puts and
  weekly contracts could not be checked at all.
- Average outlay Rs 13,100 a trade (largest Rs 21,700); worst trade −Rs 3,291 (6.6% of capital);
  longest losing run 4.

## 5. Verdict

```
Strategy: NIFTY Afternoon Momentum
Round: 3 of 3 (45 configurations; stopped - no further tuning justified on this sample)
Params this round: VIX >= 15, threshold 1.0 sigma, 14:25 -> 15:10, ATM weekly >= 1 DTE, 30% premium stop, 1 lot
Result: annualised 39.2% (house costs), Max DD 10.7%, Win rate 54.5%, Sharpe 1.27, 55 trades
Verdict: NEEDS MORE DATA - the profit is one month, on modelled premiums
Next step: paper trade only; re-run on recorded option prices; see section 7
```

A strategy that buys naked options is long volatility. It is paid when the market moves more than the
premium priced in, which in this sample was one selloff. Nothing found here supports calling it — or
any other intraday option-buying rule on NIFTY — a dependable earner on Rs 50,000.

## 6. Assumptions

1. Option premiums are modelled, not recorded. Weekly ATM IV is taken as India VIX (previous close).
2. Fills at the 5-minute close plus slippage; the premium stop is tested against each candle's extreme.
3. The index has no volume, so "VWAP" in the noise-band family is the session average of typical price.
4. Lot size 75 in 2025 and 65 from 2026; weekly expiry Thursday before Sep-2025, Tuesday after.
5. Brokerage Rs 20 an order; STT 0.10% then 0.15% from 2026-04-01 (as in `backtest/charges.py`).
6. The candle history predates the closing-auction session; the 15:10 exit already respects it.
7. 2025 has gaps (about 12 sessions a month); the 14-session noise average spans them.
8. Previous close is the official daily close in the backtest; the candle strategy uses the last
   5-minute close it was fed.

## 7. What would change the verdict

- **Recorded prices.** Download NIFTY weekly CE/PE 1- or 5-minute candles from Breeze for the 55
  trade days (needs a live Breeze session) and re-run the same trades on real premiums.
- **A forward test.** Paper trade it on the tick recorder. Judge it on at least 40 trades that include
  two separate high-VIX stretches; stop it if it is below −Rs 7,500 (15% of capital) at any point.
- **More history.** Two more years of 5-minute NIFTY candles would show whether 2024's high-VIX
  periods behaved like March 2026 or like 2025.

## 8. Running it

```bash
python -m trading_strategies.nifty_afternoon_momentum.data --refresh         # rebuild the candle / VIX cache
python -m trading_strategies.nifty_afternoon_momentum.backtest               # the rules above
python -m trading_strategies.nifty_afternoon_momentum.backtest --study all   # every table in this document
python -m pytest tests/test_nifty_afternoon_momentum.py
```

Results are written to `data/backtests/nifty_afternoon_momentum_<timestamp>.json` with every trade.
The strategy is registered as `nifty_afternoon_momentum` in `StrategyRegistry`; it has no dashboard
card or paper trader yet.

## Sources

- https://concretumgroup.com/beat-the-market-an-effective-intraday-momentum-strategy-for-sp500-etf-spy/
- https://papers.ssrn.com/abstract=4416622
- https://profiles.wustl.edu/en/publications/market-intraday-momentum/
- https://pure.eur.nl/en/publications/hedging-demand-and-market-intraday-momentum/
- https://www.business-standard.com/markets/capital-market-news/sebi-study-exposes-massive-losses-for-individual-f-o-traders-in-india-124092400948_1.html
- https://www.businesstoday.in/amp/markets/story/despite-reforms-91-of-retail-traders-still-lose-money-in-indias-booming-derivatives-market-sebi-study-483461-2025-07-07
