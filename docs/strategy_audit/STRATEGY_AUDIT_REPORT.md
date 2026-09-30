# Strategy Audit - Real Backtests, Costs and Verdicts

Generated 2026-09-30 by `python -m tools.strategy_audit.report` (raw runs under `data/strategy_audit/`). Every number below is net of commission, statutory levies, bid/ask spread and slippage unless labelled gross.

## 1. Executive summary

| Strategy | Horizon | Verdict | Run shown | Trades | Win rate | Net PF | Expectancy / trade | Net PnL | Max DD | Why |
|---|---|---|---|---|---|---|---|---|---|---|
| Four Indicator System | Intraday | **DEPRECATE** | 5m | 346 | 39.3% | 0.93 | -Rs 140 | -Rs 48,487 | 81.2% | loses money after costs (net PF 0.93, net -Rs 48,487) |
| NIFTY No Brainer | Short term | **DEPRECATE** | monthly | 15 | 53.3% | 0.66 | -Rs 282 | -Rs 4,235 | 9.1% | loses money after costs (net PF 0.66, net -Rs 4,235) |
| Index Options OI Momentum | Intraday | **EXPERIMENTAL** | - | - | - | - | - | - | - | not backtestable on real data (no historical tick OI); keep in paper trading only until it has a live track record |
| MCX Trend Rider | Short term | **DEPRECATE** | 1d | 48 | 29.2% | 1.08 | Rs 1,664 | Rs 79,866 | 139.5% | as configured (Rs 1 lakh, forced 1 lot) the account is wiped out: max drawdown 139.5% of capital; thin edge (net PF 1.08 < 1.3); deep drawdown (139.5%) |
| Equity Swing VCP | Short term | **DEPRECATE** | 1d | 23 | 43.5% | 0.42 | -Rs 326 | -Rs 7,505 | 13.3% | loses money after costs (net PF 0.42, net -Rs 7,505) |
| Lorentzian Classification ML | Short term | **DEPRECATE** | 1w | 102 | 51.0% | 1.32 | Rs 646 | Rs 65,845 | 2.1% | profitable but earns only 1.2% a year net - below the 6% risk-free hurdle (bank FD) |
| EMA Crossover Momentum | Short term | **DEPRECATE** | 1d | 567 | 28.7% | 0.96 | -Rs 100 | -Rs 56,728 | 18.6% | loses money after costs (net PF 0.96, net -Rs 56,728) |
| RSI Mean Reversion | Short term | **DEPRECATE** | 1w | 61 | 60.7% | 1.40 | Rs 2,320 | Rs 1,41,495 | 7.4% | profitable but earns only 2.6% a year net - below the 6% risk-free hurdle (bank FD); all of the 1w profit comes from shorts (Rs 1,46,024 over 27 trades, avg hold 213 d), which cannot be carried overnight in cash equities; the long side alone nets -Rs 4,528 |
| Donchian Breakout 20 | Short term | **DEPRECATE** | 1d | 278 | 39.6% | 0.91 | -Rs 290 | -Rs 80,630 | 16.6% | loses money after costs (net PF 0.91, net -Rs 80,630) |

**Verdict rules** (applied to net-of-cost results): **KEEP** = net profit factor >= 1.3 (>= 1.5 when the best of several timeframes is picked, to discount selection bias), >= 30 trades (20 for the monthly No Brainer), max drawdown <= 25% and still profitable with doubled spread + slippage. **DEPRECATE** = loses money after costs on its defined timeframe (or on its best timeframe), earns less than a 6% a year risk-free hurdle (bank FD), or cannot survive at its configured capital. **WATCH** = profitable but fails one KEEP test. **EXPERIMENTAL** = cannot be backtested on real data yet.

## 2. Methodology

**Test windows.** OHLC strategies: 1-Oct-2023 to 29-Sep-2026 (3 years), with extra history before the window fed only to warm up indicators (positions opened during warm-up are tracked but excluded). Option strategies: 1-Jan-2025 to 29-Sep-2026 - the span covered by the repo's confirmed NIFTY lot-size and expiry metadata (`nifty_expiries.json`).

**Data (all real exchange prices).**

| Strategy group | Timeframes | Source |
|---|---|---|
| EMA / RSI / Donchian / Lorentzian, VCP | 1d, 1w | yfinance daily NSE bars (split-adjusted), weekly = Mon-Fri resample |
| EMA / RSI / Donchian / Lorentzian | 15m, 1h, 4h | Angel One SmartAPI 15-minute and 1-hour history (4h resampled from 1h, session-anchored at 09:15), split-adjusted with yfinance's split history |
| Four Indicator System, NIFTY No Brainer | 5m / 1m | ICICI Breeze - NIFTY spot and real expired NIFTY option contracts, via the platform's own runners (`backtest/four_indicator_backtest.py`, `backtest/nifty_no_brainer_runner.py`) |
| MCX Trend Rider | 1d | **Proxy**: COMEX gold/silver and NYMEX WTI continuous futures x USD/INR x Indian import duty (15% until 23-Jul-2024, 6% after) in MCX price units - see limitations |

Tata Motors is excluded: its Oct-2025 demerger breaks price continuity. Broker history is unadjusted, so every intraday series is corrected for splits/bonuses (e.g. Reliance 1:1 bonus on 28-Oct-2024, HDFC Bank 1:1 on 26-Aug-2025) - without this a bonus shows up as a fake 50% crash.

**Fills and sizing.** Signals are acted on at the close of the signal bar (the platform engine's rule). Strategy-supplied stop prices are gap-adjusted: a long stopped at S fills at min(S, open). Generic strategies (EMA, RSI, Donchian, Lorentzian) run one independent Rs 1,00,000 sleeve per instrument and put all sleeve cash into each entry, like the platform engine; index sleeves (NIFTY, BANKNIFTY, SENSEX) are priced as unleveraged index-futures exposure. VCP and MCX Trend Rider use their own risk-based sizing in one shared account (as their paper traders do). Option strategies use their runners' compounding lot sizing.

### 2.1 Hidden execution costs - assumptions

Charged on every fill and reported separately per strategy:

| Segment | Commission + statutory levies | Half spread per side | Slippage per side |
|---|---|---|---|
| NSE cash equity | Rs 20/order; STT 0.1% both sides (delivery) or 0.025% sell (intraday); exch 0.00297%; SEBI 0.0001%; stamp 0.015% buy (delivery) / 0.003% (intraday); GST 18%; DP Rs 15.93 per delivery sell | 0.01% | 0.03% |
| NSE index futures | Rs 20/order; STT sell 0.0125% (<Oct-24) / 0.02% (<Apr-26) / 0.05%; exch 0.00173%; stamp 0.002% buy; GST 18% | 0.002% | 0.01% |
| MCX futures | Rs 20/order; CTT 0.01% sell; exch 0.0021%; stamp 0.002% buy; GST 18% | 0.01% | 0.03% |
| NSE index options | Rs 20/order; STT 0.1% sell (0.15% from Apr-26); exch 0.03503%; stamp 0.003% buy; GST 18% (backtest/charges.py) | 0.1% of premium | 0.25% of premium |
| NIFTY No Brainer legs | as NSE index options | 0.5 pt per leg | 0.25 pt per leg |

*Commission* = broker + exchange + government charges at the rates in force on each trade date. *Spread* = crossing half the bid/ask on entry and exit. *Slippage* = adverse move between the bar close that generated the signal and a market-order fill. Every cost table also shows net PnL with spread and slippage doubled, as a robustness check.

### 2.2 Metric definitions

- **Net PnL**: sum of trade PnL after all costs. **Return / CAGR** are on the capital shown for that run.
- **Win rate**: share of trades with net PnL > 0.
- **Expectancy**: average net PnL per trade (in rupees, and as % of the trade's entry notional).
- **Profit factor (net)**: gross winning trades / gross losing trades, both after costs.
- **Max drawdown**: largest peak-to-trough fall of the mark-to-market equity curve, as % of the peak.

### 2.3 Limitations

- **MCX data is a proxy.** Trend signals track MCX closely (same underlying, INR-converted), but MCX-specific basis, contract rolls and exchange hours differ. Treat MCX results as indicative.
- **Index OI Momentum cannot be backtested** - it needs tick-level historical open interest, which no configured provider serves.
- **Cash-equity shorts** (EMA, RSI, Donchian, Lorentzian) cannot be carried overnight in India; each strategy's long/short split is shown so the long-only result can be read directly.
- **Option margins** (No Brainer) are a proxy; broker margins for expired contracts are not recoverable.

## 3. Classification and flags

**Built for** is the design horizon (`platform_config/strategy_flags.yaml`): **Intraday** = opened and closed in the same session; **Short term** = swing / positional, days to ~2 months; **Long term** = held for months. **Observed** is what the backtest actually did (average holding period of the run shown).

| Strategy | Built for | Observed | Segment | Instrument | Direction | Bias | Hedging | Style | Status |
|---|---|---|---|---|---|---|---|---|---|
| Four Indicator System | Intraday | Intraday (0.1 d avg on 5m) | Derivative | Options | Directional | Long/Short | Unhedged | Momentum, Option buying | deprecated |
| NIFTY No Brainer | Short term | Short term (10.0 d avg on monthly) | Derivative | Options | Non-directional | Neutral | Hedged | Option selling, Ratio spread | deprecated |
| Index Options OI Momentum | Intraday | - | Derivative | Options | Directional | Long/Short | Unhedged | Momentum, Option buying, Order flow | experimental |
| MCX Trend Rider | Short term | Short term (14.8 d avg on 1d) | Commodity, Derivative | Futures | Directional | Long/Short | Unhedged | Trend following, Breakout | deprecated |
| Equity Swing VCP | Short term | Short term (33.3 d avg on 1d) | Equity | Cash | Directional | Long only | Unhedged | Breakout, Momentum | deprecated |
| Lorentzian Classification ML | Short term | Short term (27.7 d avg on 1w) | Equity | Cash, Futures | Directional | Long/Short | Unhedged | Machine learning | deprecated |
| EMA Crossover Momentum | Short term | Short term (32.3 d avg on 1d) | Equity | Cash, Futures | Directional | Long/Short | Unhedged | Trend following | deprecated |
| RSI Mean Reversion | Short term | Long term (212.7 d avg on 1w) | Equity | Cash, Futures | Directional | Long/Short | Unhedged | Mean reversion | deprecated |
| Donchian Breakout 20 | Short term | Short term (56.9 d avg on 1d) | Equity | Cash, Futures | Directional | Long/Short | Unhedged | Breakout, Trend following | deprecated |

- **Intraday:** Four Indicator System, Index Options OI Momentum
- **Short term:** NIFTY No Brainer, MCX Trend Rider, Equity Swing VCP, Lorentzian Classification ML, EMA Crossover Momentum, RSI Mean Reversion, Donchian Breakout 20
- **Long term:** none

## 4. Strategy detail

### 4.1 Four Indicator System - DEPRECATE

*Code:* `strategies/four_indicator_system.py + backtest/four_indicator_backtest.py`  
*Timeframe:* 5-minute NIFTY bars (defined by the strategy)  
*Universe:* NIFTY weekly options (CE and PE)

**Strategy rules (as implemented)**

- Call entry, all on the same 5-minute close: SuperTrend(10,3) up; RSI(14) > 70; close above the prior day's pivot R1; close above the upper Bollinger band (20, 2).
- Put entry (exact mirror): SuperTrend down; RSI(14) < 30; close below prior-day S1; close below the lower band.
- Strike: nearest weekly expiry; the strike whose real premium is closest to 1% of spot (probed on Breeze).
- Exit: SuperTrend flip; intraday square-off at 15:20 if still open.
- Size: lots = floor(balance / Rs 50,000) (compounding), min 1; NIFTY lot 75 in 2025, 65 from Jan-2026.

**Implementation findings**

- The runner books statutory charges but no slippage or spread; the audit adds both. Lot sizing compounds on the runner's balance (before spread/slippage), so sizing is slightly optimistic.
- It is profitable only before costs: execution friction on ~350 short-hold option trades a year is larger than the edge.

**Backtest results**

| Run | Trades | Win rate | Profit factor (net) | Expectancy / trade | Net PnL | Return | CAGR | Max drawdown | Avg hold |
|---|---|---|---|---|---|---|---|---|---|
| 5m | 346 | 39.3% | 0.93 | -Rs 140 (0.17%) | -Rs 48,487 | -48.5% | -31.7% | 81.2% | 0.1 d |

**Hidden execution costs**

| Run | Gross PnL | Commission + levies | Spread | Slippage | Total costs | Net PnL | Costs / gross | Net if spread+slippage 2x |
|---|---|---|---|---|---|---|---|---|
| 5m | Rs 23,675 | Rs 28,619 | Rs 12,441 | Rs 31,102 | Rs 72,162 | -Rs 48,487 | 305% | -Rs 92,030 |

**Backtest trade report**

Full trade logs (CSV, every trade with its cost breakdown): [5m](trades/four_indicator_system_5m.csv).

Exit reasons: supertrend_flip 206, intraday_square_off 140.
Sides: LONG CE 166, LONG PE 180.
Longest losing streak: 8 trades.

Best 5 and worst 5 trades:

| # | Instrument | Side | Entry | Exit | Entry px | Exit px | Exit reason | Net PnL |
|---|---|---|---|---|---|---|---|---|
| 1 | NIFTY 24400 CE 2025-05-15 | LONG CE | 2025-05-12 09:15 | 2025-05-12 15:20 | 241.40 | 618.75 | intraday_square_off | Rs 27,955 |
| 2 | NIFTY 23300 CE 2025-04-17 | LONG CE | 2025-04-17 11:25 | 2025-04-17 15:20 | 232.20 | 549.60 | intraday_square_off | Rs 23,487 |
| 3 | NIFTY 23950 PE 2026-05-12 | LONG PE | 2026-05-12 09:15 | 2026-05-12 15:20 | 225.40 | 571.00 | intraday_square_off | Rs 22,158 |
| 4 | NIFTY 24450 PE 2026-07-14 | LONG PE | 2026-07-08 09:15 | 2026-07-08 15:20 | 257.00 | 591.15 | intraday_square_off | Rs 21,399 |
| 5 | NIFTY 25700 PE 2026-01-20 | LONG PE | 2026-01-20 09:25 | 2026-01-20 15:20 | 215.60 | 473.40 | intraday_square_off | Rs 16,503 |
| 6 | NIFTY 21400 PE 2025-04-09 | LONG PE | 2025-04-07 09:15 | 2025-04-07 13:40 | 219.95 | 139.70 | supertrend_flip | -Rs 12,318 |
| 7 | NIFTY 23500 PE 2025-01-16 | LONG PE | 2025-01-10 09:55 | 2025-01-10 11:00 | 243.50 | 157.15 | supertrend_flip | -Rs 13,260 |
| 8 | NIFTY 22800 PE 2026-09-29 | LONG PE | 2026-09-29 09:35 | 2026-09-29 11:10 | 222.70 | 121.80 | supertrend_flip | -Rs 13,364 |
| 9 | NIFTY 22650 CE 2026-03-24 | LONG CE | 2026-03-24 09:20 | 2026-03-24 10:35 | 212.15 | 78.65 | supertrend_flip | -Rs 17,561 |
| 10 | NIFTY 24600 CE 2025-05-15 | LONG CE | 2025-05-15 10:55 | 2025-05-15 11:10 | 220.85 | 84.50 | supertrend_flip | -Rs 20,693 |

**Verdict: DEPRECATE** - loses money after costs (net PF 0.93, net -Rs 48,487).

### 4.2 NIFTY No Brainer - DEPRECATE

*Code:* `strategies/nifty_no_brainer*.py + backtest/nifty_no_brainer_runner.py`  
*Timeframe:* Monthly cycle; 1-minute bars for the 15:16 entry, 5-minute bars for the lifecycle  
*Universe:* NIFTY monthly call options

**Strategy rules (as implemented)**

- Entry 15:16 IST on the last Friday of the month (previous trading day on a holiday), next month's monthly expiry.
- Structure (1:-2:1 CE): buy 1 x (ATM + 300), sell 2 x (near buy + 300), buy 1 far hedge on the 500-point grid ~1,000 points above the sold strike.
- Net premium rule: skip if the net debit exceeds 1% of margin; if the net credit exceeds 1% shift all strikes up 100 points until it doesn't.
- Exits: target +2.5% of margin; stop -3% of margin (booked at no worse than -3.3%); max hold 19 days; expiry.
- Margin: proxy of (700 points + debit) x lot size per set (historical broker margin is not recoverable); lots = floor(balance / margin per set), compounding.

**Implementation findings**

- The margin used for sizing and for the target/stop is a proxy, so returns on capital are estimates.

**Backtest results**

| Run | Trades | Win rate | Profit factor (net) | Expectancy / trade | Net PnL | Return | CAGR | Max drawdown | Avg hold |
|---|---|---|---|---|---|---|---|---|---|
| monthly | 15 | 53.3% | 0.66 | -Rs 282 (-0.49%) | -Rs 4,235 | -4.2% | -2.5% | 9.1% | 10.0 d |

**Hidden execution costs**

| Run | Gross PnL | Commission + levies | Spread | Slippage | Total costs | Net PnL | Costs / gross | Net if spread+slippage 2x |
|---|---|---|---|---|---|---|---|---|
| monthly | Rs 7,633 | Rs 3,138 | Rs 5,820 | Rs 2,910 | Rs 11,868 | -Rs 4,235 | 155% | -Rs 12,965 |

**Backtest trade report**

Full trade logs (CSV, every trade with its cost breakdown): [monthly](trades/nifty_no_brainer_monthly.csv).

Exit reasons: TARGET 8, STOP_LOSS 4, MAX_HOLD 3.
Sides: 1:-2:1 CE 15.
Longest losing streak: 2 trades.

Best 5 and worst 5 trades:

| # | Instrument | Side | Entry | Exit | Entry px | Exit px | Exit reason | Net PnL |
|---|---|---|---|---|---|---|---|---|
| 1 | NIFTY 24900/25200x2/26000 CE exp 2026-04-28 | 1:-2:1 CE | 2026-03-27 15:16 | 2026-04-15 09:15 | net Rs 533 | n/a | TARGET | Rs 1,773 |
| 2 | NIFTY 24400/24700x2/25500 CE exp 2026-06-30 | 1:-2:1 CE | 2026-05-29 15:16 | 2026-06-15 09:15 | net Rs 338 | n/a | TARGET | Rs 1,516 |
| 3 | NIFTY 24600/24900x2/26000 CE exp 2026-07-28 | 1:-2:1 CE | 2026-06-25 15:16 | 2026-07-03 09:40 | net Rs 247 | n/a | TARGET | Rs 1,464 |
| 4 | NIFTY 25700/26000x2/27000 CE exp 2025-06-26 | 1:-2:1 CE | 2025-05-30 15:16 | 2025-06-10 09:20 | net Rs 337 | n/a | TARGET | Rs 756 |
| 5 | NIFTY 24200/24500x2/25500 CE exp 2025-02-27 | 1:-2:1 CE | 2025-01-31 15:16 | 2025-02-03 13:00 | net Rs 319 | n/a | TARGET | Rs 741 |
| 6 | NIFTY 25700/26000x2/27000 CE exp 2026-03-30 | 1:-2:1 CE | 2026-02-27 15:16 | 2026-03-18 15:15 | net Rs 136 | n/a | MAX_HOLD | -Rs 921 |
| 7 | NIFTY 24900/25200x2/26000 CE exp 2025-05-29 | 1:-2:1 CE | 2025-04-25 15:16 | 2025-05-02 09:50 | net Rs 188 | n/a | STOP_LOSS | -Rs 2,252 |
| 8 | NIFTY 22400/22700x2/23500 CE exp 2025-03-27 | 1:-2:1 CE | 2025-02-28 15:16 | 2025-03-06 13:05 | net -Rs 161 | n/a | STOP_LOSS | -Rs 2,282 |
| 9 | NIFTY 24800/25100x2/26000 CE exp 2025-09-30 | 1:-2:1 CE | 2025-08-29 15:16 | 2025-09-04 09:15 | net -Rs 139 | n/a | STOP_LOSS | -Rs 2,412 |
| 10 | NIFTY 25800/26100x2/27000 CE exp 2026-02-24 | 1:-2:1 CE | 2026-01-30 15:16 | 2026-02-03 09:15 | net Rs 228 | n/a | STOP_LOSS | -Rs 4,088 |

Months not traded: 2025-03 (SKIP_DEBIT), 2025-06 (NO_ENTRY_PRICE), 2025-12 (SKIP_DEBIT), 2026-07 (SKIP_DEBIT), 2026-08 (SKIP_DEBIT), 2026-09 (SKIP_DEBIT).

**Verdict: DEPRECATE** - loses money after costs (net PF 0.66, net -Rs 4,235).

### 4.3 Index Options OI Momentum - EXPERIMENTAL

*Code:* `strategies/index_oi_momentum.py`  
*Timeframe:* Tick-by-tick (Angel WebSocket SNAP_QUOTE)  
*Universe:* NIFTY / BANKNIFTY / SENSEX ATM options

**Strategy rules (as implemented)**

- OI velocity of the index future over a 60-90 s rolling window (k = 4) combined with a price/OI direction matrix.
- Confirmations: strike-level OI, option premium breakout + velocity, bid/ask spread <= 1.2% and depth.
- Modes: base vs expiry-day (auto from the expiry calendar); expiry mode checks the max-pain wall.
- Risk: 0.75% of capital per trade, hard stop 22% of premium, partial exits + trailing, quick exits, max 7 trades/day per index, daily loss limit -3%.

**Implementation findings**

- No provider serves historical tick-level open interest, so no real backtest is possible. The repo's `backtest/oi_momentum_backtest.py` runs on a synthetic tape and is not evidence of edge.
- Live paper trading started today; zero trades so far.

**Backtest:** not backtestable on real data (no historical tick OI); keep in paper trading only until it has a live track record.

### 4.4 MCX Trend Rider - DEPRECATE

*Code:* `strategies/mcx_trend_rider.py`  
*Timeframe:* Daily bars (defined by the strategy: Turtle-style Donchian 20/55)  
*Universe:* MCX GOLDM, SILVERM, CRUDEOIL futures in one account (proxy data, see Methodology)

**Strategy rules (as implemented)**

- Entry: close above the 20-day high (below the 20-day low for shorts), both excluding today, with ADX(14) >= 20.
- Turtle loser-skip: after a losing trade the next entry needs a 55-day breakout.
- Optional regime filter: longs only above SMA(200), shorts only below (the code defaults it ON, the dashboard catalog turns it OFF).
- Size: lots = floor(1% of capital / (2 x ATR(20) x point value)), minimum 1 lot.
- Initial stop entry -/+ 2 x ATR(20); to breakeven at +1 ATR; Chandelier trail (highest high - 3 x ATR(14)) from +2 ATR.
- Other exits: close through the 10-day channel; 90 days held with < 0.5 ATR profit.

**Implementation findings**

- At Rs 1,00,000 the 1% risk rule always floors to 0 lots and the code forces 1 lot - one CRUDEOIL lot is ~Rs 7 lakh notional, so a single stop costs up to ~40% of capital. The configured capital is far below what the sizing rule assumes.
- The catalog sets `use_sma_filter: False`, overriding the strategy's own default regime filter.
- No provider serves multi-year continuous MCX futures (Breeze has no MCX segment, Angel only live contracts).

**Proxy check against real MCX bars (Angel One, live contracts)**

| Proxy | Real contract | Overlap | Daily-return correlation | Real move | Proxy move | Price ratio real/proxy |
|---|---|---|---|---|---|---|
| MCX_GOLDM | GOLDM05OCT26FUT | 166 d (2026-02-02 to 2026-09-29) | 0.87 | 3.7% | -6.0% | 1.036 +/- 0.037 |
| MCX_SILVERM | SILVERM30NOV26FUT | 166 d (2026-02-02 to 2026-09-29) | 0.90 | -6.3% | -17.3% | 1.090 +/- 0.061 |
| MCX_CRUDEOIL | CRUDEOIL21SEP26FUT | 0 d | insufficient overlap | | | |

**Backtest results**

| Run | Trades | Win rate | Profit factor (net) | Expectancy / trade | Net PnL | Return | CAGR | Max drawdown | Avg hold |
|---|---|---|---|---|---|---|---|---|---|
| 1d (shown) | 48 | 29.2% | 1.08 | Rs 1,664 (-0.06%) | Rs 79,866 | 79.9% | 21.7% | 139.5% | 14.8 d |
| 1d_cap10L | 48 | 29.2% | 1.08 | Rs 1,664 (-0.06%) | Rs 79,866 | 8.0% | 2.6% | 42.0% | 14.8 d |
| 1d_cap10L_sma200 | 45 | 28.9% | 1.39 | Rs 6,531 (0.08%) | Rs 2,93,893 | 29.4% | 9.0% | 29.2% | 14.4 d |

**Hidden execution costs**

| Run | Gross PnL | Commission + levies | Spread | Slippage | Total costs | Net PnL | Costs / gross | Net if spread+slippage 2x |
|---|---|---|---|---|---|---|---|---|
| 1d | Rs 1,19,472 | Rs 8,875 | Rs 7,683 | Rs 23,049 | Rs 39,606 | Rs 79,866 | 33% | Rs 49,134 |
| 1d_cap10L | Rs 1,19,472 | Rs 8,875 | Rs 7,683 | Rs 23,049 | Rs 39,606 | Rs 79,866 | 33% | Rs 49,134 |
| 1d_cap10L_sma200 | Rs 3,30,705 | Rs 8,271 | Rs 7,135 | Rs 21,406 | Rs 36,813 | Rs 2,93,893 | 11% | Rs 2,65,351 |

Long vs short (net): 1d: long Rs 5,53,644 (35 trades), short -Rs 4,73,778 (13); 1d_cap10L: long Rs 5,53,644 (35 trades), short -Rs 4,73,778 (13); 1d_cap10L_sma200: long Rs 6,54,852 (34 trades), short -Rs 3,60,960 (11).

**Per instrument (1d)**

| Instrument | Trades | Win rate | Net PnL | Avg net / trade |
|---|---|---|---|---|
| MCX_CRUDEOIL | 14 | 14.3% | -Rs 2,94,444 | -Rs 21,032 |
| MCX_GOLDM | 18 | 33.3% | Rs 94,839 | Rs 5,269 |
| MCX_SILVERM | 16 | 37.5% | Rs 2,79,471 | Rs 17,467 |

**Backtest trade report**

Full trade logs (CSV, every trade with its cost breakdown): [1d](trades/mcx_trend_rider_1d.csv), [1d_cap10L](trades/mcx_trend_rider_1d_cap10L.csv), [1d_cap10L_sma200](trades/mcx_trend_rider_1d_cap10L_sma200.csv).

Exit reasons: long_stop_hit 33, short_stop_hit 9, short_10day_channel_exit 4, long_10day_channel_exit 2.
Sides: SHORT 13, LONG 35.
Longest losing streak: 11 trades.

Best 5 and worst 5 trades:

| # | Instrument | Side | Entry | Exit | Entry px | Exit px | Exit reason | Net PnL |
|---|---|---|---|---|---|---|---|---|
| 1 | MCX_SILVERM | LONG | 2026-01-12 23:30 | 2026-01-30 23:30 | 260,194.71 | 317,307.31 | long_stop_hit | Rs 2,84,101 |
| 2 | MCX_GOLDM | LONG | 2026-01-12 23:30 | 2026-01-29 23:30 | 141,912.37 | 161,072.01 | long_stop_hit | Rs 1,90,069 |
| 3 | MCX_GOLDM | LONG | 2025-09-02 23:30 | 2025-10-21 23:30 | 107,732.57 | 124,057.65 | long_stop_hit | Rs 1,62,071 |
| 4 | MCX_SILVERM | LONG | 2025-12-11 23:30 | 2025-12-29 23:30 | 195,630.84 | 220,154.34 | long_stop_hit | Rs 1,21,555 |
| 5 | MCX_SILVERM | LONG | 2025-09-12 23:30 | 2025-10-21 23:30 | 127,506.09 | 145,896.44 | long_stop_hit | Rs 91,237 |
| 6 | MCX_GOLDM | LONG | 2026-08-21 23:30 | 2026-08-31 23:30 | 152,769.95 | 145,728.25 | long_stop_hit | -Rs 71,912 |
| 7 | MCX_SILVERM | SHORT | 2026-07-16 23:30 | 2026-08-05 23:30 | 183,829.07 | 202,393.75 | short_stop_hit | -Rs 93,805 |
| 8 | MCX_CRUDEOIL | LONG | 2026-03-09 23:30 | 2026-03-10 23:30 | 8,712.79 | 7,745.15 | long_stop_hit | -Rs 97,607 |
| 9 | MCX_SILVERM | LONG | 2026-01-06 23:30 | 2026-01-08 23:30 | 247,639.56 | 227,433.03 | long_stop_hit | -Rs 1,02,230 |
| 10 | MCX_GOLDM | SHORT | 2026-03-23 23:30 | 2026-04-01 23:30 | 141,030.61 | 152,663.29 | short_stop_hit | -Rs 1,17,797 |

**Verdict: DEPRECATE** - as configured (Rs 1 lakh, forced 1 lot) the account is wiped out: max drawdown 139.5% of capital; thin edge (net PF 1.08 < 1.3); deep drawdown (139.5%).

### 4.5 Equity Swing VCP - DEPRECATE

*Code:* `strategies/equity_swing_vcp.py`  
*Timeframe:* Daily bars (defined by the strategy: Minervini trend template + VCP)  
*Universe:* 15 NSE large caps in one shared Rs 1,00,000 account; NIFTY 50 as the regime benchmark

**Strategy rules (as implemented)**

- Trend template: close > SMA150 and SMA200; SMA150 > SMA200; SMA200 rising vs 20 bars ago; SMA50 > SMA150 > SMA200; close > SMA50; close >= 1.25 x 52-week low and >= 0.75 x 52-week high; 6-month return >= 8%.
- Market regime: NIFTY 50 close above its 50-day SMA.
- VCP: 35-bar base depth 4-35%; ATR(10) now <= 95% of ATR(10) 25 bars ago; 5-day average volume <= 1.25 x 50-day average; pivot = highest high of the last 15 bars before today.
- Entry: close crosses above the pivot, volume >= 1.3 x 50-day average, close in the top 40% of the day's range.
- Stop: max(4%, distance to the 10-day low) capped at 7%; size = 1.25% risk of Rs 1,00,000, max 20% of capital per position.
- Management: stop to breakeven at +1R; sell 33% at +2R; exit the rest on a close below EMA(21) (only while in profit).
- Long only.

**Implementation findings**

- Stop exits fill exactly at the stop price even when the stock gaps below it (optimistic); the audit fills gap-downs at the open.
- 'Relative strength rating' is a flat 6-month +8% momentum check, not a percentile RS rank vs the market.
- The platform BacktestEngine replaces the strategy's risk sizing with all-cash sizing; the audit uses the strategy's own sizing (as the paper trader does).
- The universe (15 mega caps) is structurally ill-suited to VCP, which targets growth leaders - hence very few setups.

**Backtest results**

| Run | Trades | Win rate | Profit factor (net) | Expectancy / trade | Net PnL | Return | CAGR | Max drawdown | Avg hold |
|---|---|---|---|---|---|---|---|---|---|
| 1d | 23 | 43.5% | 0.42 | -Rs 326 (-1.05%) | -Rs 7,505 | -7.5% | -2.6% | 13.3% | 33.3 d |

**Hidden execution costs**

| Run | Gross PnL | Commission + levies | Spread | Slippage | Total costs | Net PnL | Costs / gross | Net if spread+slippage 2x |
|---|---|---|---|---|---|---|---|---|
| 1d | -Rs 4,925 | Rs 2,282 | Rs 75 | Rs 224 | Rs 2,580 | -Rs 7,505 | 52% | -Rs 7,804 |

**Per instrument (1d)**

| Instrument | Trades | Win rate | Net PnL | Avg net / trade |
|---|---|---|---|---|
| NSE:ADANIGREEN | 1 | 0.0% | -Rs 1,258 | -Rs 1,258 |
| NSE:BHARTIARTL | 3 | 33.3% | -Rs 945 | -Rs 315 |
| NSE:HCLTECH | 3 | 100.0% | Rs 1,743 | Rs 581 |
| NSE:HDFCBANK | 1 | 0.0% | -Rs 1,153 | -Rs 1,153 |
| NSE:ICICIBANK | 3 | 33.3% | -Rs 2,057 | -Rs 686 |
| NSE:LT | 3 | 66.7% | -Rs 441 | -Rs 147 |
| NSE:RELIANCE | 2 | 0.0% | -Rs 1,404 | -Rs 702 |
| NSE:SBIN | 1 | 0.0% | -Rs 115 | -Rs 115 |
| NSE:TATACONSUM | 1 | 100.0% | Rs 423 | Rs 423 |
| NSE:TATASTEEL | 3 | 66.7% | Rs 705 | Rs 235 |
| NSE:TCS | 1 | 0.0% | -Rs 1,270 | -Rs 1,270 |
| NSE:WIPRO | 1 | 0.0% | -Rs 1,733 | -Rs 1,733 |

**Backtest trade report**

Full trade logs (CSV, every trade with its cost breakdown): [1d](trades/equity_swing_vcp_1d.csv).

Exit reasons: SELL 21, partial_profit_take 2.
Sides: LONG 23.
Longest losing streak: 6 trades.

Best 5 and worst 5 trades:

| # | Instrument | Side | Entry | Exit | Entry px | Exit px | Exit reason | Net PnL |
|---|---|---|---|---|---|---|---|---|
| 1 | NSE:TATASTEEL | LONG | 2025-12-30 15:30 | 2026-03-04 15:30 | 175.80 | 196.73 | SELL | Rs 1,485 |
| 2 | NSE:HCLTECH | LONG | 2023-11-16 15:30 | 2024-01-04 15:30 | 1,311.05 | 1,419.95 | SELL | Rs 1,089 |
| 3 | NSE:LT | LONG | 2023-12-04 15:30 | 2024-01-31 15:30 | 3,313.55 | 3,479.75 | SELL | Rs 717 |
| 4 | NSE:HCLTECH | LONG | 2023-11-16 15:30 | 2023-12-15 15:30 | 1,311.05 | 1,491.30 | partial_profit_take | Rs 641 |
| 5 | NSE:TATASTEEL | LONG | 2025-12-30 15:30 | 2026-01-28 15:30 | 175.80 | 193.85 | partial_profit_take | Rs 584 |
| 6 | NSE:TCS | LONG | 2024-02-06 15:30 | 2024-03-27 15:30 | 4,136.00 | 3,846.48 | SELL | -Rs 1,270 |
| 7 | NSE:RELIANCE | LONG | 2024-06-03 15:30 | 2024-06-04 15:30 | 1,510.32 | 1,413.25 | SELL | -Rs 1,281 |
| 8 | NSE:TATASTEEL | LONG | 2024-03-01 15:30 | 2024-03-14 15:30 | 149.95 | 139.45 | SELL | -Rs 1,364 |
| 9 | NSE:ICICIBANK | LONG | 2024-12-05 15:30 | 2025-01-13 15:30 | 1,336.50 | 1,234.90 | SELL | -Rs 1,435 |
| 10 | NSE:WIPRO | LONG | 2024-07-18 15:30 | 2024-07-22 15:30 | 286.60 | 260.50 | SELL | -Rs 1,733 |

**Verdict: DEPRECATE** - loses money after costs (net PF 0.42, net -Rs 7,505).

### 4.6 Lorentzian Classification ML - DEPRECATE

*Code:* `strategies/lorentzian_ml.py + lorentzian_strategy/`  
*Timeframe:* Timeframe is a user parameter (default 1d) -> tested on 15m, 1h, 4h, 1d, 1w  
*Universe:* 18 NSE instruments, 1 sleeve of Rs 1,00,000 each

**Strategy rules (as implemented)**

- Features (Pine defaults): RSI(14), WaveTrend(10,11), CCI(20), ADX(20), RSI(9), each normalised.
- Approximate nearest neighbours: k = 8 neighbours by Lorentzian distance, every 4th bar sampled, training label = direction of the past 4-bar move (Pine-exact port).
- Filters: volatility filter, regime filter (threshold -0.1), Nadaraya-Watson kernel trend filter (h=8, r=8, x=25, lag=2).
- Entry: new long when the prediction turns positive with filters + bullish kernel; new short mirrored. An opposite entry reverses the position.
- Exit: strict 4-bar holding exit (dynamic kernel exits off by default).
- Platform adapter re-runs the pipeline on the last ~750 bars at every candle (max_bars_back capped to 400).
- Sizing: all available sleeve cash (the strategy itself trades a fixed quantity of 1).

**Implementation findings**

- The platform BacktestEngine desyncs on reversals: a BUY while short only closes the short (it never opens the long the strategy believes it holds), so later exits open unintended shorts. The audit uses reversal semantics, like the Lorentzian paper trader does.
- The Pine anchor `maxBarsBackIndex` is tied to the last bar of whatever data is passed in, so a single full-history run and the per-candle replay give different signals - only the per-candle replay matches live.

**Backtest results**

| Run | Trades | Win rate | Profit factor (net) | Expectancy / trade | Net PnL | Return | CAGR | Max drawdown | Avg hold |
|---|---|---|---|---|---|---|---|---|---|
| 15m | 8997 | 29.0% | 0.40 | -Rs 135 (-0.26%) | -Rs 12,19,089 | -67.7% | -31.4% | 67.7% | 0.5 d |
| 1h | 2594 | 39.1% | 0.58 | -Rs 214 (-0.26%) | -Rs 5,55,627 | -30.9% | -11.6% | 31.7% | 1.4 d |
| 4h | 914 | 42.2% | 0.58 | -Rs 426 (-0.47%) | -Rs 3,88,997 | -21.6% | -7.8% | 22.1% | 4.3 d |
| 1d | 458 | 44.3% | 0.77 | -Rs 289 (-0.29%) | -Rs 1,32,498 | -7.4% | -2.5% | 10.9% | 10.0 d |
| 1w (shown) | 102 | 51.0% | 1.32 | Rs 646 (0.60%) | Rs 65,845 | 3.7% | 1.2% | 2.1% | 27.7 d |

**Hidden execution costs**

| Run | Gross PnL | Commission + levies | Spread | Slippage | Total costs | Net PnL | Costs / gross | Net if spread+slippage 2x |
|---|---|---|---|---|---|---|---|---|
| 15m | -Rs 42,302 | Rs 8,21,420 | Rs 86,528 | Rs 2,68,839 | Rs 11,76,787 | -Rs 12,19,089 | 2782% | -Rs 15,74,456 |
| 1h | -Rs 73,698 | Rs 3,33,938 | Rs 36,121 | Rs 1,11,869 | Rs 4,81,928 | -Rs 5,55,627 | 654% | -Rs 7,03,617 |
| 4h | -Rs 1,34,853 | Rs 1,99,723 | Rs 13,274 | Rs 41,147 | Rs 2,54,144 | -Rs 3,88,997 | 188% | -Rs 4,43,418 |
| 1d | Rs 2,945 | Rs 1,06,114 | Rs 7,171 | Rs 22,158 | Rs 1,35,443 | -Rs 1,32,498 | 4599% | -Rs 1,61,827 |
| 1w | Rs 97,446 | Rs 24,617 | Rs 1,709 | Rs 5,275 | Rs 31,601 | Rs 65,845 | 32% | Rs 58,861 |

Long vs short (net): 15m: long -Rs 4,85,346 (4513 trades), short -Rs 7,33,742 (4484); 1h: long -Rs 2,19,609 (1291 trades), short -Rs 3,36,017 (1303); 4h: long -Rs 1,19,288 (457 trades), short -Rs 2,69,709 (457); 1d: long -Rs 387 (226 trades), short -Rs 1,32,111 (232); 1w: long Rs 36,214 (50 trades), short Rs 29,631 (52).

**Per instrument (1w)**

| Instrument | Trades | Win rate | Net PF | Net PnL | Return | Max DD |
|---|---|---|---|---|---|---|
| NSE:ADANIGREEN | 2 | 100.0% | inf | Rs 48,928 | 48.9% | 12.3% |
| NSE:BANKNIFTY | 11 | 36.4% | 0.30 | -Rs 10,868 | -10.9% | 12.3% |
| NSE:BHARTIARTL | 4 | 100.0% | inf | Rs 7,976 | 8.0% | 6.3% |
| NSE:HCLTECH | 5 | 60.0% | 2.65 | Rs 6,997 | 7.0% | 4.3% |
| NSE:HDFCBANK | 8 | 75.0% | 1.33 | Rs 5,362 | 5.4% | 14.5% |
| NSE:HINDUNILVR | 8 | 25.0% | 0.21 | -Rs 13,022 | -13.0% | 13.0% |
| NSE:ICICIBANK | 6 | 33.3% | 1.27 | Rs 1,952 | 2.0% | 9.2% |
| NSE:INFY | 7 | 71.4% | 4.77 | Rs 35,516 | 35.5% | 9.4% |
| NSE:ITC | 3 | 100.0% | inf | Rs 6,831 | 6.8% | 2.6% |
| NSE:LT | 7 | 28.6% | 0.25 | -Rs 39,380 | -39.4% | 44.5% |
| NSE:NIFTY | 3 | 33.3% | 0.30 | -Rs 2,451 | -2.5% | 5.4% |
| NSE:RELIANCE | 4 | 50.0% | 1.00 | -Rs 1 | -0.0% | 10.8% |
| NSE:SBIN | 8 | 25.0% | 0.97 | -Rs 774 | -0.8% | 22.1% |
| NSE:SENSEX | 5 | 40.0% | 1.02 | Rs 150 | 0.1% | 5.9% |
| NSE:TATACONSUM | 4 | 50.0% | 1.90 | Rs 7,589 | 7.6% | 9.7% |
| NSE:TATASTEEL | 4 | 50.0% | 0.36 | -Rs 9,362 | -9.4% | 13.0% |
| NSE:TCS | 6 | 50.0% | 1.13 | Rs 999 | 1.0% | 6.3% |
| NSE:WIPRO | 7 | 71.4% | 4.14 | Rs 19,403 | 19.4% | 9.0% |

Benchmark: equal-weight buy-and-hold of the same 18 instruments over the window returned 10.5% before costs.

**Backtest trade report**

Full trade logs (CSV, every trade with its cost breakdown): [15m](trades/lorentzian_ml_15m.csv), [1h](trades/lorentzian_ml_1h.csv), [4h](trades/lorentzian_ml_4h.csv), [1d](trades/lorentzian_ml_1d.csv), [1w](trades/lorentzian_ml_1w.csv).

Exit reasons: lorentzian_exit_short 48, lorentzian_exit_long 48, backtest_end 3, lorentzian_new_short 2, lorentzian_new_long 1.
Sides: SHORT 52, LONG 50.
Longest losing streak: 8 trades.

Best 5 and worst 5 trades:

| # | Instrument | Side | Entry | Exit | Entry px | Exit px | Exit reason | Net PnL |
|---|---|---|---|---|---|---|---|---|
| 1 | NSE:SBIN | LONG | 2023-12-08 15:30 | 2024-03-07 15:30 | 614.15 | 788.05 | lorentzian_exit_long | Rs 27,424 |
| 2 | NSE:ADANIGREEN | LONG | 2026-04-17 15:30 | 2026-05-15 15:30 | 1,127.30 | 1,379.00 | lorentzian_exit_long | Rs 25,970 |
| 3 | NSE:ADANIGREEN | SHORT | 2024-11-08 15:30 | 2024-12-06 15:30 | 1,598.55 | 1,210.65 | lorentzian_exit_short | Rs 22,958 |
| 4 | NSE:INFY | LONG | 2024-06-28 15:30 | 2024-07-26 15:30 | 1,566.75 | 1,878.90 | lorentzian_exit_long | Rs 19,890 |
| 5 | NSE:TATACONSUM | SHORT | 2024-10-18 15:30 | 2024-11-14 15:30 | 1,093.25 | 925.00 | lorentzian_exit_short | Rs 14,806 |
| 6 | NSE:LT | SHORT | 2026-03-13 15:30 | 2026-04-10 15:30 | 3,439.00 | 3,959.90 | lorentzian_exit_short | -Rs 10,706 |
| 7 | NSE:TATASTEEL | SHORT | 2023-11-03 15:30 | 2023-12-01 15:30 | 117.30 | 130.00 | lorentzian_exit_short | -Rs 11,111 |
| 8 | NSE:HDFCBANK | LONG | 2026-07-10 15:30 | 2026-08-07 15:30 | 824.95 | 731.00 | lorentzian_exit_long | -Rs 14,028 |
| 9 | NSE:LT | SHORT | 2026-01-23 15:30 | 2026-02-20 15:30 | 3,743.80 | 4,380.60 | lorentzian_new_long | -Rs 16,291 |
| 10 | NSE:LT | LONG | 2026-02-20 15:30 | 2026-03-13 15:30 | 4,380.60 | 3,439.00 | lorentzian_new_short | -Rs 19,133 |

**Verdict: DEPRECATE** - profitable but earns only 1.2% a year net - below the 6% risk-free hurdle (bank FD).

### 4.7 EMA Crossover Momentum - DEPRECATE

*Code:* `strategies/__init__.py::EMACrossover`  
*Timeframe:* No timeframe in the rules -> tested on 15m, 1h, 4h, 1d, 1w  
*Universe:* 18 NSE instruments (NIFTY, BANKNIFTY, SENSEX + 15 large caps); 1 sleeve of Rs 1,00,000 each

**Strategy rules (as implemented)**

- EMA(9) and EMA(21) of the close.
- BUY signal on every bar where EMA9 > EMA21; SELL signal on every bar where EMA9 < EMA21.
- Engine semantics: BUY while flat opens a long; SELL while long closes it; the next SELL opens a short (stop-and-reverse with a one-bar gap). Same-side signals are ignored.
- Sizing: every entry uses all available sleeve cash (platform engine rule), equities rounded down to 5 shares.
- No stop-loss, no ATR stop, no trailing stop.

**Implementation findings**

- **Crossover check is broken**: `prev_fast` and `prev_slow` are both `_price_history[-2]` (the previous close), so the 'crossover' condition is always true - the strategy fires on every bar of a regime, not on the cross. It behaves as an always-in-market EMA regime follower.
- The catalog description promises an 'ATR stop and trailing risk management' - none exists in the code.
- Short entries in cash equities cannot be carried overnight in India (only intraday MIS or via stock futures).

**Backtest results**

| Run | Trades | Win rate | Profit factor (net) | Expectancy / trade | Net PnL | Return | CAGR | Max drawdown | Avg hold |
|---|---|---|---|---|---|---|---|---|---|
| 15m | 11047 | 20.9% | 0.78 | -Rs 86 (-0.35%) | -Rs 9,53,023 | -52.9% | -22.3% | 56.3% | 1.4 d |
| 1h | 3570 | 25.7% | 0.71 | -Rs 245 (-0.34%) | -Rs 8,75,900 | -48.7% | -20.0% | 52.9% | 5.0 d |
| 4h | 1089 | 27.5% | 0.83 | -Rs 274 (-0.35%) | -Rs 2,98,580 | -16.6% | -5.9% | 31.4% | 16.8 d |
| 1d (shown) | 567 | 28.7% | 0.96 | -Rs 100 (-0.07%) | -Rs 56,728 | -3.2% | -1.1% | 18.6% | 32.3 d |
| 1w | 117 | 29.9% | 0.73 | -Rs 1,141 (-0.97%) | -Rs 1,33,500 | -7.4% | -2.5% | 11.9% | 127.1 d |

**Hidden execution costs**

| Run | Gross PnL | Commission + levies | Spread | Slippage | Total costs | Net PnL | Costs / gross | Net if spread+slippage 2x |
|---|---|---|---|---|---|---|---|---|
| 15m | Rs 10,27,787 | Rs 14,98,570 | Rs 1,16,745 | Rs 3,65,495 | Rs 19,80,810 | -Rs 9,53,023 | 193% | -Rs 14,35,263 |
| 1h | -Rs 26,277 | Rs 6,64,133 | Rs 45,176 | Rs 1,40,313 | Rs 8,49,623 | -Rs 8,75,900 | 3233% | -Rs 10,61,389 |
| 4h | Rs 5,507 | Rs 2,38,194 | Rs 16,109 | Rs 49,785 | Rs 3,04,087 | -Rs 2,98,580 | 5522% | -Rs 3,64,473 |
| 1d | Rs 1,16,710 | Rs 1,35,571 | Rs 9,268 | Rs 28,599 | Rs 1,73,438 | -Rs 56,728 | 149% | -Rs 94,595 |
| 1w | -Rs 99,286 | Rs 26,910 | Rs 1,795 | Rs 5,509 | Rs 34,214 | -Rs 1,33,500 | 34% | -Rs 1,40,804 |

Long vs short (net): 15m: long -Rs 1,90,061 (5512 trades), short -Rs 7,62,962 (5535); 1h: long -Rs 1,85,424 (1791 trades), short -Rs 6,90,476 (1779); 4h: long Rs 69,623 (545 trades), short -Rs 3,68,203 (544); 1d: long Rs 50,728 (282 trades), short -Rs 1,07,456 (285); 1w: long -Rs 90,610 (51 trades), short -Rs 42,890 (66).

**Per instrument (1d)**

| Instrument | Trades | Win rate | Net PF | Net PnL | Return | Max DD |
|---|---|---|---|---|---|---|
| NSE:ADANIGREEN | 29 | 24.1% | 0.96 | -Rs 7,241 | -7.2% | 63.8% |
| NSE:BANKNIFTY | 36 | 27.8% | 0.76 | -Rs 11,472 | -11.5% | 23.6% |
| NSE:BHARTIARTL | 27 | 25.9% | 1.32 | Rs 27,129 | 27.1% | 35.1% |
| NSE:HCLTECH | 19 | 57.9% | 3.05 | Rs 1,17,066 | 117.1% | 18.0% |
| NSE:HDFCBANK | 28 | 35.7% | 0.99 | -Rs 366 | -0.4% | 32.5% |
| NSE:HINDUNILVR | 27 | 37.0% | 1.48 | Rs 23,807 | 23.8% | 20.7% |
| NSE:ICICIBANK | 40 | 22.5% | 0.33 | -Rs 46,609 | -46.6% | 50.8% |
| NSE:INFY | 41 | 22.0% | 0.69 | -Rs 37,287 | -37.3% | 54.7% |
| NSE:ITC | 32 | 25.0% | 0.78 | -Rs 11,572 | -11.6% | 28.1% |
| NSE:LT | 37 | 21.6% | 0.32 | -Rs 50,087 | -50.1% | 58.9% |
| NSE:NIFTY | 37 | 24.3% | 0.67 | -Rs 16,787 | -16.8% | 28.8% |
| NSE:RELIANCE | 36 | 27.8% | 0.47 | -Rs 42,288 | -42.3% | 57.2% |
| NSE:SBIN | 32 | 18.8% | 0.79 | -Rs 15,651 | -15.7% | 30.8% |
| NSE:SENSEX | 34 | 29.4% | 0.68 | -Rs 15,426 | -15.4% | 28.1% |
| NSE:TATACONSUM | 22 | 31.8% | 1.05 | Rs 1,985 | 2.0% | 23.8% |
| NSE:TATASTEEL | 29 | 37.9% | 0.99 | -Rs 1,067 | -1.1% | 41.2% |
| NSE:TCS | 30 | 36.7% | 1.52 | Rs 31,919 | 31.9% | 17.3% |
| NSE:WIPRO | 31 | 32.3% | 0.97 | -Rs 2,781 | -2.8% | 43.8% |

Benchmark: equal-weight buy-and-hold of the same 18 instruments over the window returned 10.5% before costs.

**Backtest trade report**

Full trade logs (CSV, every trade with its cost breakdown): [15m](trades/ema_crossover_15m.csv), [1h](trades/ema_crossover_1h.csv), [4h](trades/ema_crossover_4h.csv), [1d](trades/ema_crossover_1d.csv), [1w](trades/ema_crossover_1w.csv).

Exit reasons: death_cross 280, golden_cross 269, backtest_end 18.
Sides: SHORT 285, LONG 282.
Longest losing streak: 19 trades.

Best 5 and worst 5 trades:

| # | Instrument | Side | Entry | Exit | Entry px | Exit px | Exit reason | Net PnL |
|---|---|---|---|---|---|---|---|---|
| 1 | NSE:BHARTIARTL | LONG | 2023-11-07 15:30 | 2024-10-29 15:30 | 937.15 | 1,637.10 | death_cross | Rs 69,548 |
| 2 | NSE:ADANIGREEN | LONG | 2023-11-28 15:30 | 2024-03-19 15:30 | 1,052.80 | 1,821.55 | death_cross | Rs 68,739 |
| 3 | NSE:ADANIGREEN | SHORT | 2024-10-07 15:30 | 2025-03-20 15:30 | 1,752.45 | 923.40 | golden_cross | Rs 53,566 |
| 4 | NSE:HCLTECH | LONG | 2024-06-10 15:30 | 2024-11-01 15:30 | 1,418.75 | 1,757.40 | death_cross | Rs 29,986 |
| 5 | NSE:TATASTEEL | LONG | 2024-01-30 15:30 | 2024-07-08 15:30 | 134.70 | 172.28 | death_cross | Rs 27,962 |
| 6 | NSE:ADANIGREEN | SHORT | 2025-05-09 15:30 | 2025-05-13 15:30 | 879.45 | 958.05 | golden_cross | -Rs 12,271 |
| 7 | NSE:ADANIGREEN | SHORT | 2026-03-04 15:30 | 2026-04-08 15:30 | 871.95 | 1,029.65 | golden_cross | -Rs 14,516 |
| 8 | NSE:TATASTEEL | SHORT | 2025-04-08 15:30 | 2025-05-07 15:30 | 130.28 | 146.00 | golden_cross | -Rs 15,960 |
| 9 | NSE:ADANIGREEN | LONG | 2025-03-21 15:30 | 2025-04-09 15:30 | 954.25 | 860.75 | death_cross | -Rs 16,907 |
| 10 | NSE:HCLTECH | LONG | 2026-04-08 15:30 | 2026-04-22 15:30 | 1,461.00 | 1,285.30 | death_cross | -Rs 23,445 |

**Verdict: DEPRECATE** - loses money after costs (net PF 0.96, net -Rs 56,728).

### 4.8 RSI Mean Reversion - DEPRECATE

*Code:* `strategies/__init__.py::RSIStrategy`  
*Timeframe:* No timeframe in the rules -> tested on 15m, 1h, 4h, 1d, 1w  
*Universe:* 18 NSE instruments, 1 sleeve of Rs 1,00,000 each

**Strategy rules (as implemented)**

- RSI(14) from simple averages of gains/losses (Cutler RSI, not Wilder's smoothing).
- BUY signal while RSI < 30; SELL signal while RSI > 70.
- Engine semantics: long on the first oversold bar, exit on the first overbought bar, short on the next overbought bar, cover when RSI < 30 again.
- Sizing: all available sleeve cash.
- No stop-loss.

**Implementation findings**

- The catalog promises 'trailing breakeven protection' - not implemented; positions have no stop at all, which is why average holding periods run to months.
- Shorts in cash equities are not executable overnight.

**Backtest results**

| Run | Trades | Win rate | Profit factor (net) | Expectancy / trade | Net PnL | Return | CAGR | Max drawdown | Avg hold |
|---|---|---|---|---|---|---|---|---|---|
| 15m | 6593 | 47.8% | 0.40 | -Rs 229 (-0.60%) | -Rs 15,10,250 | -83.9% | -45.7% | 83.9% | 2.4 d |
| 1h | 2075 | 56.8% | 0.74 | -Rs 297 (-0.31%) | -Rs 6,15,735 | -34.2% | -13.0% | 34.6% | 8.4 d |
| 4h | 586 | 59.9% | 0.80 | -Rs 482 (-0.47%) | -Rs 2,82,602 | -15.7% | -5.5% | 18.8% | 29.6 d |
| 1d | 281 | 55.9% | 0.81 | -Rs 618 (-0.69%) | -Rs 1,73,740 | -9.7% | -3.3% | 21.6% | 62.2 d |
| 1w (shown) | 61 | 60.7% | 1.40 | Rs 2,320 (2.53%) | Rs 1,41,495 | 7.9% | 2.6% | 7.4% | 212.7 d |

**Hidden execution costs**

| Run | Gross PnL | Commission + levies | Spread | Slippage | Total costs | Net PnL | Costs / gross | Net if spread+slippage 2x |
|---|---|---|---|---|---|---|---|---|
| 15m | -Rs 4,47,127 | Rs 8,57,061 | Rs 50,056 | Rs 1,56,007 | Rs 10,63,123 | -Rs 15,10,250 | 238% | -Rs 17,16,313 |
| 1h | -Rs 69,500 | Rs 4,31,374 | Rs 28,072 | Rs 86,790 | Rs 5,46,235 | -Rs 6,15,735 | 786% | -Rs 7,30,596 |
| 4h | -Rs 1,10,128 | Rs 1,35,094 | Rs 9,134 | Rs 28,246 | Rs 1,72,474 | -Rs 2,82,602 | 157% | -Rs 3,19,982 |
| 1d | -Rs 90,019 | Rs 65,715 | Rs 4,421 | Rs 13,586 | Rs 83,722 | -Rs 1,73,740 | 93% | -Rs 1,91,747 |
| 1w | Rs 1,63,748 | Rs 17,291 | Rs 1,224 | Rs 3,738 | Rs 22,252 | Rs 1,41,495 | 14% | Rs 1,36,534 |

Long vs short (net): 15m: long -Rs 5,85,886 (3290 trades), short -Rs 9,24,364 (3303); 1h: long -Rs 88,950 (1031 trades), short -Rs 5,26,785 (1044); 4h: long Rs 95,405 (287 trades), short -Rs 3,78,007 (299); 1d: long Rs 41,291 (146 trades), short -Rs 2,15,031 (135); 1w: long -Rs 4,528 (34 trades), short Rs 1,46,024 (27).

**Per instrument (1w)**

| Instrument | Trades | Win rate | Net PF | Net PnL | Return | Max DD |
|---|---|---|---|---|---|---|
| NSE:ADANIGREEN | 5 | 60.0% | 4.35 | Rs 76,257 | 76.3% | 34.3% |
| NSE:BANKNIFTY | 4 | 100.0% | inf | Rs 23,868 | 23.9% | 18.1% |
| NSE:BHARTIARTL | 1 | 0.0% | 0.00 | -Rs 5,319 | -5.3% | 10.7% |
| NSE:HCLTECH | 5 | 80.0% | 3.59 | Rs 41,422 | 41.4% | 24.0% |
| NSE:HDFCBANK | 4 | 50.0% | 0.92 | -Rs 2,548 | -2.5% | 30.1% |
| NSE:HINDUNILVR | 5 | 80.0% | 1.73 | Rs 17,603 | 17.6% | 22.5% |
| NSE:ICICIBANK | 1 | 0.0% | 0.00 | -Rs 25,877 | -25.9% | 43.8% |
| NSE:INFY | 5 | 60.0% | 2.22 | Rs 41,605 | 41.6% | 23.5% |
| NSE:ITC | 3 | 66.7% | 0.19 | -Rs 30,702 | -30.7% | 40.2% |
| NSE:LT | 1 | 100.0% | inf | Rs 3,110 | 3.1% | 17.5% |
| NSE:NIFTY | 2 | 50.0% | 1.03 | Rs 66 | 0.1% | 7.6% |
| NSE:RELIANCE | 6 | 66.7% | 1.71 | Rs 16,987 | 17.0% | 22.0% |
| NSE:SBIN | 4 | 25.0% | 0.25 | -Rs 25,264 | -25.3% | 52.7% |
| NSE:SENSEX | 2 | 50.0% | 1.31 | Rs 915 | 0.9% | 7.7% |
| NSE:TATACONSUM | 3 | 66.7% | 1.46 | Rs 3,731 | 3.7% | 21.5% |
| NSE:TATASTEEL | 3 | 33.3% | 0.44 | -Rs 8,032 | -8.0% | 37.4% |
| NSE:TCS | 2 | 0.0% | 0.00 | -Rs 26,720 | -26.7% | 28.4% |
| NSE:WIPRO | 5 | 80.0% | 2.01 | Rs 40,392 | 40.4% | 23.6% |

Benchmark: equal-weight buy-and-hold of the same 18 instruments over the window returned 10.5% before costs.

**Backtest trade report**

Full trade logs (CSV, every trade with its cost breakdown): [15m](trades/rsi_15m.csv), [1h](trades/rsi_1h.csv), [4h](trades/rsi_4h.csv), [1d](trades/rsi_1d.csv), [1w](trades/rsi_1w.csv).

Exit reasons: rsi_oversold 26, rsi_overbought 19, backtest_end 16.
Sides: LONG 34, SHORT 27.
Longest losing streak: 15 trades.

Best 5 and worst 5 trades:

| # | Instrument | Side | Entry | Exit | Entry px | Exit px | Exit reason | Net PnL |
|---|---|---|---|---|---|---|---|---|
| 1 | NSE:ADANIGREEN | LONG | 2026-01-23 15:30 | 2026-05-01 15:30 | 772.80 | 1,227.15 | rsi_overbought | Rs 63,128 |
| 2 | NSE:INFY | SHORT | 2026-01-23 15:30 | 2026-03-13 15:30 | 1,670.80 | 1,248.30 | rsi_oversold | Rs 33,386 |
| 3 | NSE:WIPRO | LONG | 2024-05-31 15:30 | 2024-11-14 15:30 | 219.10 | 283.35 | rsi_overbought | Rs 29,780 |
| 4 | NSE:ADANIGREEN | SHORT | 2023-12-08 15:30 | 2024-11-22 15:30 | 1,550.30 | 1,051.80 | rsi_oversold | Rs 29,613 |
| 5 | NSE:WIPRO | SHORT | 2024-12-13 15:30 | 2025-05-02 15:30 | 309.95 | 241.68 | rsi_oversold | Rs 28,937 |
| 6 | NSE:HINDUNILVR | LONG | 2025-12-12 15:30 | 2026-09-29 15:30 | 2,260.60 | 1,863.80 | backtest_end | -Rs 24,247 |
| 7 | NSE:ICICIBANK | SHORT | 2024-02-02 15:30 | 2026-09-29 15:30 | 1,024.00 | 1,292.20 | backtest_end | -Rs 25,877 |
| 8 | NSE:INFY | LONG | 2026-03-20 15:30 | 2026-09-29 15:30 | 1,255.90 | 1,015.40 | backtest_end | -Rs 32,996 |
| 9 | NSE:ITC | LONG | 2025-02-14 15:30 | 2026-09-29 15:30 | 410.25 | 265.10 | backtest_end | -Rs 38,070 |
| 10 | NSE:WIPRO | LONG | 2026-02-27 15:30 | 2026-09-29 15:30 | 200.96 | 156.79 | backtest_end | -Rs 39,859 |

**Verdict: DEPRECATE** - profitable but earns only 2.6% a year net - below the 6% risk-free hurdle (bank FD); all of the 1w profit comes from shorts (Rs 1,46,024 over 27 trades, avg hold 213 d), which cannot be carried overnight in cash equities; the long side alone nets -Rs 4,528.

### 4.9 Donchian Breakout 20 - DEPRECATE

*Code:* `strategies/__init__.py::BreakoutStrategy`  
*Timeframe:* No timeframe in the rules -> tested on 15m, 1h, 4h, 1d, 1w  
*Universe:* 18 NSE instruments, 1 sleeve of Rs 1,00,000 each

**Strategy rules (as implemented)**

- Channel = highest high / lowest low of the previous 19 bars (`[-20:-1]` slice).
- BUY on a close above the channel high; SELL on a close below the channel low.
- Engine semantics: long on the breakout, exit on the breakdown, short on the next bar that is still below the channel.
- Sizing: all available sleeve cash. No stop other than the opposite channel.

**Implementation findings**

- Lookback is effectively 19 bars, not 20.
- Shorts in cash equities are not executable overnight.

**Backtest results**

| Run | Trades | Win rate | Profit factor (net) | Expectancy / trade | Net PnL | Return | CAGR | Max drawdown | Avg hold |
|---|---|---|---|---|---|---|---|---|---|
| 15m | 5971 | 29.2% | 0.69 | -Rs 172 (-0.31%) | -Rs 10,25,861 | -57.0% | -24.6% | 58.1% | 2.5 d |
| 1h | 1681 | 34.1% | 0.73 | -Rs 365 (-0.46%) | -Rs 6,13,347 | -34.1% | -13.0% | 39.2% | 9.3 d |
| 4h | 520 | 36.0% | 0.78 | -Rs 538 (-0.52%) | -Rs 2,79,876 | -15.5% | -5.5% | 25.8% | 30.0 d |
| 1d (shown) | 278 | 39.6% | 0.91 | -Rs 290 (-0.27%) | -Rs 80,630 | -4.5% | -1.5% | 16.6% | 56.9 d |
| 1w | 59 | 30.5% | 0.44 | -Rs 4,467 (-4.31%) | -Rs 2,63,573 | -14.6% | -5.1% | 18.0% | 209.9 d |

**Hidden execution costs**

| Run | Gross PnL | Commission + levies | Spread | Slippage | Total costs | Net PnL | Costs / gross | Net if spread+slippage 2x |
|---|---|---|---|---|---|---|---|---|
| 15m | Rs 2,17,898 | Rs 9,76,233 | Rs 64,738 | Rs 2,02,787 | Rs 12,43,758 | -Rs 10,25,861 | 571% | -Rs 12,93,386 |
| 1h | -Rs 1,68,544 | Rs 3,50,227 | Rs 22,985 | Rs 71,591 | Rs 4,44,803 | -Rs 6,13,347 | 264% | -Rs 7,07,923 |
| 4h | -Rs 1,29,796 | Rs 1,17,666 | Rs 7,915 | Rs 24,499 | Rs 1,50,080 | -Rs 2,79,876 | 116% | -Rs 3,12,290 |
| 1d | Rs 3,082 | Rs 65,508 | Rs 4,452 | Rs 13,752 | Rs 83,712 | -Rs 80,630 | 2717% | -Rs 98,834 |
| 1w | -Rs 2,47,640 | Rs 12,562 | Rs 824 | Rs 2,547 | Rs 15,933 | -Rs 2,63,573 | 6% | -Rs 2,66,944 |

Long vs short (net): 15m: long -Rs 2,60,581 (2961 trades), short -Rs 7,65,279 (3010); 1h: long -Rs 11,465 (821 trades), short -Rs 6,01,881 (860); 4h: long Rs 10 (266 trades), short -Rs 2,79,886 (254); 1d: long -Rs 63,222 (145 trades), short -Rs 17,408 (133); 1w: long -Rs 1,06,649 (27 trades), short -Rs 1,56,924 (32).

**Per instrument (1d)**

| Instrument | Trades | Win rate | Net PF | Net PnL | Return | Max DD |
|---|---|---|---|---|---|---|
| NSE:ADANIGREEN | 18 | 27.8% | 0.64 | -Rs 36,822 | -36.8% | 62.9% |
| NSE:BANKNIFTY | 17 | 29.4% | 0.70 | -Rs 9,850 | -9.9% | 21.9% |
| NSE:BHARTIARTL | 9 | 55.6% | 2.54 | Rs 52,333 | 52.3% | 24.9% |
| NSE:HCLTECH | 14 | 64.3% | 1.35 | Rs 18,899 | 18.9% | 33.4% |
| NSE:HDFCBANK | 13 | 46.2% | 1.25 | Rs 7,789 | 7.8% | 24.7% |
| NSE:HINDUNILVR | 14 | 42.9% | 1.01 | Rs 536 | 0.5% | 25.2% |
| NSE:ICICIBANK | 21 | 19.0% | 0.17 | -Rs 53,499 | -53.5% | 59.3% |
| NSE:INFY | 16 | 43.8% | 0.79 | -Rs 15,549 | -15.5% | 38.9% |
| NSE:ITC | 16 | 37.5% | 0.50 | -Rs 22,572 | -22.6% | 35.0% |
| NSE:LT | 20 | 25.0% | 0.25 | -Rs 51,088 | -51.1% | 60.0% |
| NSE:NIFTY | 18 | 33.3% | 0.65 | -Rs 12,111 | -12.1% | 24.0% |
| NSE:RELIANCE | 12 | 50.0% | 0.98 | -Rs 753 | -0.8% | 29.1% |
| NSE:SBIN | 15 | 53.3% | 1.83 | Rs 23,721 | 23.7% | 26.4% |
| NSE:SENSEX | 17 | 35.3% | 0.70 | -Rs 9,167 | -9.2% | 21.0% |
| NSE:TATACONSUM | 12 | 50.0% | 0.80 | -Rs 6,533 | -6.5% | 26.9% |
| NSE:TATASTEEL | 15 | 33.3% | 0.77 | -Rs 15,659 | -15.7% | 45.3% |
| NSE:TCS | 16 | 50.0% | 1.34 | Rs 14,412 | 14.4% | 22.5% |
| NSE:WIPRO | 15 | 46.7% | 2.09 | Rs 35,283 | 35.3% | 19.7% |

Benchmark: equal-weight buy-and-hold of the same 18 instruments over the window returned 10.5% before costs.

**Backtest trade report**

Full trade logs (CSV, every trade with its cost breakdown): [15m](trades/breakout_15m.csv), [1h](trades/breakout_1h.csv), [4h](trades/breakout_4h.csv), [1d](trades/breakout_1d.csv), [1w](trades/breakout_1w.csv).

Exit reasons: breakout_down 145, breakout_up 116, backtest_end 17.
Sides: SHORT 133, LONG 145.
Longest losing streak: 12 trades.

Best 5 and worst 5 trades:

| # | Instrument | Side | Entry | Exit | Entry px | Exit px | Exit reason | Net PnL |
|---|---|---|---|---|---|---|---|---|
| 1 | NSE:BHARTIARTL | LONG | 2023-11-20 15:30 | 2024-11-04 15:30 | 961.40 | 1,591.25 | breakout_down | Rs 62,541 |
| 2 | NSE:TATASTEEL | LONG | 2023-11-29 15:30 | 2024-07-10 15:30 | 127.75 | 167.98 | breakout_down | Rs 30,174 |
| 3 | NSE:HCLTECH | LONG | 2024-06-07 15:30 | 2025-01-14 15:30 | 1,431.50 | 1,813.55 | breakout_down | Rs 28,225 |
| 4 | NSE:ADANIGREEN | SHORT | 2024-10-22 15:30 | 2025-03-19 15:30 | 1,684.45 | 911.20 | breakout_up | Rs 26,865 |
| 5 | NSE:SBIN | LONG | 2025-09-17 15:30 | 2026-03-09 15:30 | 857.15 | 1,098.50 | breakout_down | Rs 26,162 |
| 6 | NSE:INFY | LONG | 2026-04-08 15:30 | 2026-04-24 15:30 | 1,346.20 | 1,154.60 | breakout_down | -Rs 16,672 |
| 7 | NSE:BHARTIARTL | SHORT | 2024-11-19 15:30 | 2024-12-13 15:30 | 1,525.50 | 1,681.75 | breakout_up | -Rs 16,980 |
| 8 | NSE:HCLTECH | LONG | 2026-04-08 15:30 | 2026-04-22 15:30 | 1,461.00 | 1,285.30 | breakout_down | -Rs 17,150 |
| 9 | NSE:ADANIGREEN | LONG | 2024-06-03 15:30 | 2024-06-04 15:30 | 2,038.00 | 1,646.00 | breakout_down | -Rs 17,955 |
| 10 | NSE:TATASTEEL | SHORT | 2025-04-07 15:30 | 2025-05-12 15:30 | 129.48 | 151.63 | breakout_up | -Rs 22,640 |

**Verdict: DEPRECATE** - loses money after costs (net PF 0.91, net -Rs 80,630).

## 5. Recommendations

- **EXPERIMENTAL:** Index Options OI Momentum
- **DEPRECATE:** Four Indicator System, NIFTY No Brainer, MCX Trend Rider, Equity Swing VCP, Lorentzian Classification ML, EMA Crossover Momentum, RSI Mean Reversion, Donchian Breakout 20

**What is worth salvaging / next steps**

- **MCX Trend Rider** is the only strategy with an edge above the hurdle, and only when run with its own SMA(200) regime filter switched on and at least Rs 10 lakh (so one lot is survivable): net PF 1.39, CAGR 9.0%, max drawdown 29.2% over 45 trades - almost all of it from gold/silver longs; crude and every short lost. This is on proxy data: re-test on real MCX continuous futures before reviving it, and fix the sizing so the 1% risk rule is respected instead of forcing one lot.
- **Four Indicator System** is profitable before costs (gross Rs 23,675) but costs Rs 72,162 over 346 trades. Only a much lower trade frequency (e.g. a daily-trend or higher-timeframe filter) could make the edge survive execution costs - re-audit before any revival.
- **Index OI Momentum** stays in paper trading. Breeze's 1-minute F&O history carries open interest, which could support an approximate (1-minute rather than tick) backtest; until then judge it on at least 3 months of forward paper results, net of the same costs.
- **Generic strategies** (EMA, RSI, Donchian, Lorentzian): costs grow with trade frequency, so every intraday timeframe loses far more than daily/weekly. The EMA and RSI implementations also have correctness bugs (see findings) and should not be revived as-is.

The dashboard now carries these results: every strategy card has segment / instrument / direction / hedging / style / horizon flags you can filter on, and deprecated strategies are hidden unless 'Show deprecated' is ticked (`platform_config/strategy_flags.yaml`, `platform_config/strategy_audit.json`).

