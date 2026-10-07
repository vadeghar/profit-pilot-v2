# Stock Pullback Mean Reversion (A3) — backtest report and decision

**Decision: NO GO.** The dip-buying signal is real before costs (about +0.48% a trade, 66% winners), but a
delivery trade in India costs at least that much. On a Rs 65,000 account the specified rules lose 14% a year
and run the account down; on Rs 5 lakh they break even.

Date: 7-Oct-2026. Code: `trading_strategies/stock_pullback/`. Tests: `tests/test_stock_pullback.py`.
Numbers: `python -m trading_strategies.stock_pullback.backtest --study all`, archived as
`data/backtests/stock_pullback_all_20261007.json`.

## 1. The strategy

Buy a sharp two-day dip in a liquid stock that is in an uptrend; sell the bounce. The RSI(2) method of Connors
and Alvarez, decided at a close and traded at the next open.

1. **Universe**: the 200 most-traded NSE stocks over the last six months, refreshed monthly.
2. **Market**: no new buys while the Nifty 50 is below its 200-day average.
3. **Setup**: the stock closes above its own 200-day average and its 2-day RSI is below 10.
4. **Buy**: next open, into one of six equal slots; the most oversold first.
5. **Sell**: next open after a close above the 5-day average, or after 10 trading days. No stop.

Rules written down before the first run and not changed.

## 2. How it was tested

Same data and money rules as the momentum rotation (A1): Yahoo adjusted daily candles for 499 of today's
Nifty 500 members, Jan-2011 to Oct-2026, Rs 65,000, whole shares, idle cash earning nothing. Costs: Rs 20 an
order, STT 0.1% each side, stamp duty, exchange fees, GST, Rs 18.5 depository charge a sale, 0.1% slippage
each side. A slot smaller than Rs 1,000 is not opened.

## 3. Results

**With the specified costs, Rs 65,000:**

| | Full 2011-2026 | Early 2011-2018 | Late 2019-2026 |
|---|---|---|---|
| CAGR | **−13.9%** | −25.6% | −26.3% |
| Max drawdown | **90.6%** | 90.6% | 90.6% |
| Win rate | 35.4% | 35.4% | 37.4% |
| Average trade | −1.72% | −1.72% | −1.52% |
| Trades a year (while it could still trade) | about 110 | 106 | 131 |
| Nifty 50 CAGR | 8.6% | 7.4% | 9.9% |

Each window starts with fresh capital and is down 90% within six years. The account shrinks, the slots shrink
with it, and the flat fees take a growing share of every trade until slots fall below the Rs 1,000 floor.
Full-period trades stop in 2016 for that reason.

**The same rules before any cost** (fractional shares):

| | Full | Early | Late |
|---|---|---|---|
| CAGR | 18.3% | 14.1% | 22.8% |
| Max drawdown | 31.9% | 21.7% | — |
| Trades | 3,583 | 1,536 | — |
| Win rate | 66.5% | | |
| Average trade | **+0.48%** | +0.45% | +0.50% |
| Average hold | 3.9 days | | |

Gross, a typical trade wins 1% and the worst 5% lose 6.8% or more. By year the gross average trade ranges
from −2.2% (2011) and −0.6% (2022) to +1.0% (2014).

## 4. Why it fails: the cost of a trade against the edge of a trade

| Cost of one round trip on a Rs 10,800 slot | Rs | % of the slot |
|---|---|---|
| STT, 0.1% buy and 0.1% sell | 21.6 | 0.20 |
| Slippage, 0.1% each side | 21.6 | 0.20 |
| Brokerage, Rs 20 x 2 with GST | 47.2 | 0.44 |
| Depository charge on the sale | 18.5 | 0.17 |
| Stamp duty and exchange fees | 2.4 | 0.02 |
| **Total** | **111** | **1.03** |

The signal earns 0.48%. STT and slippage alone take 0.40%, whatever the account size or broker.

| Case | Full CAGR | Average trade |
|---|---|---|
| No costs, fractional shares | +18.3% | +0.48% |
| Rs 65,000, no brokerage (taxes, depository charge, slippage) | −11.1% | −0.34% |
| Rs 65,000, Rs 20 an order (specified) | −13.9% | −1.72% |
| Rs 65,000, 0.5% brokerage | −14.0% | −1.73% |
| Rs 5,00,000, Rs 20 an order | −2.7% | −0.05% |

A zero-brokerage broker does not save it at this account size, and a Rs 5 lakh account only reaches
break-even. This is about 110-140 trades a year held four days each; that turnover is the problem.

## 5. Variants, before costs

Fifteen changes, one at a time, list fixed before running. Shown without costs, because with them every
variant runs the Rs 65,000 account down alike:

| Variant | Gross CAGR | Gross average trade | Early / Late average trade |
|---|---|---|---|
| Baseline | 18.3% | 0.48% | 0.45 / 0.50 |
| RSI(2) < 5 | 17.6% | 0.56% | 0.56 / 0.57 |
| RSI(2) < 15 | 19.6% | 0.47% | 0.42 / 0.51 |
| IBS < 0.2 instead of RSI | 9.8% | 0.22% | 0.13 / 0.29 |
| No stock trend filter | 8.7% | 0.24% | 0.12 / 0.36 |
| Stock above 100-day average | 18.7% | 0.49% | 0.43 / 0.53 |
| No market filter | 26.2% | 0.51% | 0.53 / 0.50 |
| Exit above 3-day average | 20.1% | 0.37% | 0.34 / 0.40 |
| Exit above 10-day average | 12.4% | 0.51% | 0.44 / 0.58 |
| Hold at most 5 days | 17.9% | 0.40% | 0.35 / 0.44 |
| Hold at most 20 days | 18.3% | 0.49% | 0.50 / 0.49 |
| 10% stop | 18.3% | 0.47% | 0.43 / 0.50 |
| 4 slots | 18.4% | 0.47% | 0.45 / 0.48 |
| 10 slots | 13.8% | 0.40% | 0.44 / 0.38 |
| Universe top 100 | 6.1% | 0.22% | 0.37 / 0.12 |
| Buy the strongest 6-month stocks first | 20.6% | 0.53% | 0.47 / 0.57 |

The edge is steady: 0.4-0.6% a trade for every sensible form, in both halves. None reaches the 1.0% a trade
costs on this account, and the best (0.56%) leaves about 0.1% after STT and slippage on any account.

## 6. Other cautions

- **Survivorship bias works against this strategy more than most.** The stock list is today's Nifty 500. A
  dip-buyer's worst trades are dips in companies that kept falling and left the index, and those are missing.
  The gross 0.48% is therefore an upper estimate.
- The worst gross trade lost 54.7% (Adani Enterprises, June 2015, a demerger the adjusted prices show as a
  fall). There is no stop, and a 10% stop did not change the result.

## 7. Decision

```
Strategy: Stock Pullback Mean Reversion (A3)
Round: 1 of 12 (rules as specified, no tuning)
Params this round: top 200 by turnover, RSI(2) < 10 above the 200-day average, exit above the 5-day average, 6 slots, Nifty above its 200-day average
Result: CAGR -13.9%, Max DD 90.6%, Win rate 35.4%, Sharpe -1.72, 851 trades (before costs: CAGR 18.3%, +0.48% a trade, win rate 66.5%, 3,583 trades)
Verdict: FAIL
Next step: stop and report; do not add to the application
```

**NO GO.** The finding is structural rather than a matter of settings: a four-day delivery trade in India
pays 0.2% in STT and about as much in slippage, and that is the whole edge. It would need an account several
times larger and still would only break even.

What carries over: the signal does work before costs, so a dip could be used to *time the entries* of a
strategy that holds for months (the momentum rotation), where the same cost is paid 16 times a year instead
of 120. That is an idea to test, not a result.

This is a backtest report, not investment advice.
