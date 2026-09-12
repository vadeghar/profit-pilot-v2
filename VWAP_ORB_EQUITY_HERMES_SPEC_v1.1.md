# VWAP + ORB Equity Strategy — Hermes Automation Specification

**Version:** 1.1 (reviewed/finalized)
**Purpose:** Deterministic intraday strategy specification for Profit Pilot / Hermes
**Market:** NSE India cash equities
**Primary timeframe:** 5-minute bars
**Opening Range:** First 15 minutes (09:15–09:30 IST)

> Research/backtesting specification only. Do not deploy live until the strategy passes out-of-sample, cost/slippage, robustness, and paper-trading validation.

## 0. Changelog (v1.0 → v1.1)

Fixes applied during review — all are spec clarifications, no thresholds changed:

1. **RVOL lookback gap (critical):** v1.0's `SMA(volume,20)` cannot be computed until bar 20 of the session (~10:55 IST), leaving only ~35 minutes of the 09:30–11:30 signal window where RVOL is even computable. Section 9 now defines an explicit `RVOL_MODE` so this isn't silently discovered mid-backtest.
2. **Signal-window boundary ambiguity:** "time > 09:30 and time <= 11:30" didn't say whether "time" is bar-open or bar-close. Section 8 now pins this to bar-open time.
3. **Same-bar SL/TP tie-break was unspecified:** Section 17 now names the default conservative assumption explicitly instead of just saying "conservative."
4. **No tick-size/rounding rule:** Entry, stop, and target prices weren't rounded to NSE tick size (₹0.05) before order generation — added to Section 11.
5. **No point-in-time universe requirement:** Section 3 now explicitly requires the historical (not current) constituent/tradeable list, to avoid survivorship bias separate from the "don't optimize on future performance" rule already present.
6. **Holiday/half-day sessions:** Section 2 now explicitly rejects non-standard session days in V1 rather than silently truncating windows.

Everything else below is unchanged from v1.0 and was already internally consistent (VWAP slope calc, stop/target formulas, risk sizing, state machine, rejection list, logging schema, experiment sequence).

## 1. Strategy Objective

Trade liquid NSE equities when the opening range breaks with directional confirmation from:

1. Opening Range Breakout (ORB)
2. Session VWAP direction
3. Breakout volume
4. Breakout candle strength
5. Risk/reward and daily risk controls

The system must be fully rule-based. Hermes must not invent discretionary conditions or alter thresholds during execution.

## 2. Market Session

For NSE equities, regular trading begins at **09:15 IST**. The exchange also has a pre-open session beginning at 09:00. The strategy must use the regular session for ORB calculations and must not use pre-open prints as ORB candles.

Default:
- Timezone: `Asia/Kolkata`
- Session: `09:15–15:30`
- ORB window: `09:15–09:30`
- Signal window: `09:30–11:30`
- Force exit: `15:15`
- No new entries after: `11:30`

The exact exchange calendar/holiday calendar must drive session availability.

**v1.1:** Days with a non-standard session (special/half-day sessions with a different close time, e.g. Muhurat trading) must be excluded from V1 entirely — do not rescale the ORB/signal/force-exit windows for them. Treat as a hard rejection at the day level, logged with reason `NON_STANDARD_SESSION`.

## 3. Instrument Universe

Start with liquid NSE cash equities only.

Universe filters:
- Valid OHLCV data for the complete test period
- No suspended/illiquid symbols
- Minimum historical data: 60 trading sessions
- Minimum price: configurable, default ₹100
- Minimum average daily traded value: configurable
- Corporate-action-adjusted historical prices
- Exclude symbols with missing/inconsistent intraday bars

Do not optimize the universe using future performance.

**v1.1:** The universe must be constructed **point-in-time** — i.e., "was this symbol liquid/tradeable as of date D" using only information available as of D, not today's current liquidity/index-membership list applied retroactively. Using today's F&O/index constituent list as the historical universe is a form of survivorship bias distinct from parameter optimization and must be avoided separately.

## 4. Required Data

Hermes/backtester must have:

- Timestamp
- Open
- High
- Low
- Close
- Volume
- Symbol
- Trading date
- Exchange calendar/session metadata

Optional but recommended:
- Previous-day close
- ATR(14)
- Market/index OHLCV
- Corporate-action events

VWAP must be calculated from intraday price/volume and reset at every regular session.

## 5. Opening Range Calculation

For every trading day and symbol:

```text
OR_START = 09:15
OR_END   = 09:30
```

Include bars whose timestamps belong to the 09:15–09:29 interval.

Calculate:

```text
OR_HIGH = maximum(high)
OR_LOW  = minimum(low)
OR_RANGE = OR_HIGH - OR_LOW
OR_MID = (OR_HIGH + OR_LOW) / 2
```

The OR becomes immutable after 09:30.

Never use future bars to modify OR_HIGH/OR_LOW.

## 6. ORB Quality Filter

Reject the session if the opening range is abnormal.

Default relative range:

```text
OR_RANGE_PCT = OR_RANGE / OR_MID
```

Initial research bounds:

```text
MIN_OR_RANGE_PCT = 0.25%
MAX_OR_RANGE_PCT = 2.00%
```

These are research defaults, not proven optimal values. Hermes must expose them as configuration and test them without look-ahead.

## 7. VWAP Calculation

For each regular-session bar:

```text
TypicalPrice = (High + Low + Close) / 3

VWAP = Σ(TypicalPrice × Volume) / Σ(Volume)
```

Reset cumulative price-volume and volume at 09:15 each day.

For long setup:
- Close > VWAP
- VWAP must be rising

For short setup:
- Close < VWAP
- VWAP must be falling

Default VWAP slope test:

```text
VWAP_current > VWAP_3_bars_ago  → rising
VWAP_current < VWAP_3_bars_ago  → falling
```

Do not use a slope threshold until validated by research.

## 8. Breakout Confirmation

**v1.1:** All "time" comparisons in this section and in the pseudocode (Sections 24–25) refer to the **breakout candle's open (start) timestamp**, not its close timestamp. A bar starting at 11:25 (closing 11:30) is inside the signal window; a bar starting at 11:30 is not.

### Long breakout

A valid long breakout requires:

1. OR is complete.
2. Current bar closes above OR_HIGH.
3. Current bar's open time is inside the permitted signal window (09:30 ≤ open time ≤ 11:25, given 5-min bars and an 11:30 cutoff).
4. Close > VWAP.
5. VWAP is rising.
6. Breakout volume passes the volume filter.
7. Candle strength passes the candle filter.
8. Entry risk passes the maximum-risk filter.
9. Daily risk limits have not been reached.
10. No existing position in the symbol.

### Short breakout

Mirror the long rules:

1. Close < OR_LOW.
2. Close < VWAP.
3. VWAP is falling.
4. Volume passes.
5. Candle strength passes.
6. Risk passes.
7. Daily limits pass.
8. No existing position.

A wick through OR_HIGH/OR_LOW without a qualifying close is **not a breakout**.

## 9. Volume Confirmation

Use relative volume rather than raw volume.

Default:

```text
RVOL = breakout_bar_volume / SMA(volume, 20)
```

Initial threshold:

```text
RVOL >= 1.50
```

The 20-bar average must use only bars available before the breakout bar.

If insufficient history exists, reject the signal.

**v1.1 — RVOL_MODE (must be explicitly configured, do not leave implicit):**

With 5-min bars, the 09:30 bar is only the 4th bar of the session — same-session-only history never reaches 20 bars until ~10:55 IST, which would silently reject nearly all signals for the first ~85 minutes of the 2-hour signal window. Hermes must implement both modes and run them as separate labeled experiments, not silently default to one:

- `RVOL_MODE = same_session_only` — strictly today's bars only, as in v1.0. Accept that early-window signals will be rejected for `RVOL_UNAVAILABLE` until 20 same-day bars exist; report what fraction of the signal window is affected.
- `RVOL_MODE = rolling_cross_session` — the 20-bar lookback may extend into the prior trading session's regular-session bars (never pre-open/post-close bars, never the current unclosed bar). This makes RVOL computable from the first eligible bar of the day.

V1 baseline (Section 29) should run with `rolling_cross_session` so the strategy is actually testable across the full signal window, with `same_session_only` as a comparison experiment.

## 10. Candle Strength

The breakout candle must demonstrate directional commitment.

For each breakout candle:

```text
CandleRange = High - Low
CloseLocation = (Close - Low) / CandleRange
```

Long:

```text
CloseLocation >= 0.70
```

Short:

```text
CloseLocation <= 0.30
```

Reject zero-range candles.

## 11. Entry Model

Default entry model: **bar-close confirmation**.

Long:
- Signal is generated only after the breakout candle closes above OR_HIGH.
- Enter at the next executable price/bar open in the backtester.

Short:
- Signal is generated only after the breakout candle closes below OR_LOW.
- Enter at the next executable price/bar open.

Never enter using the same bar's closing price unless the execution model explicitly supports market-on-close execution.

This prevents look-ahead bias.

**v1.1 — Tick size rounding:** Entry price, stop price, and target price must each be rounded to the NSE tick size (default ₹0.05) before order generation and before risk/quantity calculation, using a direction-aware rounding rule (round stops away from the position in the safer direction; round targets toward the achievable side). Document the exact rounding function used, since it has a small but real effect on `risk_per_share` and therefore quantity.

## 12. Retest Mode

Do not enable retest entries in V1.

V1 should test the pure breakout model first.

V2 may test:

```text
Break OR_HIGH
→ price retests OR_HIGH
→ holds above OR_HIGH
→ VWAP remains aligned
→ enter on bullish confirmation
```

and the mirrored short setup.

Keep V1 and V2 as separate experiments.

## 13. Stop Loss

Default V1 stop:

### Long

```text
SL = OR_HIGH - 0.10 × OR_RANGE
```

### Short

```text
SL = OR_LOW + 0.10 × OR_RANGE
```

However, if this produces excessive stop-outs, Hermes should research alternative models separately:

- Breakout candle low/high
- OR opposite side
- ATR-based stop
- OR level ± volatility buffer

Do not mix stop models inside one backtest without labeling them.

## 14. Position Risk

Risk must be calculated before order generation.

Default:

```text
MAX_RISK_PER_TRADE = 0.50% of strategy equity
```

Position quantity:

```text
risk_amount = equity × risk_percent

risk_per_share = abs(entry_price - stop_price)

quantity = floor(risk_amount / risk_per_share)
```

Then apply:
- Available cash/margin constraints
- Exchange lot/quantity rules
- Maximum position value
- Minimum quantity

If calculated quantity is zero, reject the trade.

## 15. Take Profit

V1 primary target:

```text
TARGET = ENTRY + 2 × INITIAL_RISK     (long)
TARGET = ENTRY - 2 × INITIAL_RISK     (short)
```

Therefore:

```text
Risk : Reward = 1 : 2
```

Do not optimize the target during the initial implementation.

Later experiments can compare:
- 1.5R
- 2R
- 2.5R
- trailing stop
- partial exit + runner

Each must be a separate experiment.

## 16. Break-Even Rule

V1: **Disabled**.

Reason: first establish the raw strategy expectancy.

V2 experiment:

```text
When unrealized P&L >= +1R:
    move SL to entry price + costs
```

Do not implement this until V1 is validated.

## 17. Trade Management

At any time after entry:

### Long
- SL hit → exit
- TP hit → exit
- 15:15 → exit
- Never hold overnight

### Short
- SL hit → exit
- TP hit → exit
- 15:15 → exit
- Never hold overnight

**v1.1 — Same-bar SL/TP tie-break (explicit default):** If both SL and TP fall within the same OHLC bar's high-low range and tick-level/intrabar data is unavailable, **assume SL is hit first** (i.e., record the trade as a loss for that bar) unless higher-resolution data proves otherwise. This is the conservative assumption referenced in v1.0 — it was correct in spirit but must be stated explicitly so every implementation (and every future contributor) resolves the ambiguity the same way instead of picking whichever is convenient.

## 18. Trade Frequency

Default:

```text
MAX_TRADES_PER_SYMBOL_PER_DAY = 1
```

Do not immediately re-enter after a stopped-out ORB.

Optional future experiment:
- one long + one short maximum
- first valid breakout only
- first breakout per direction

V1 remains one trade per symbol per day.

## 19. Market-Level Risk Filter

Optional but recommended for research.

Use NIFTY 50 as market regime context.

For long:
- Prefer NIFTY close > NIFTY VWAP

For short:
- Prefer NIFTY close < NIFTY VWAP

Do NOT make this mandatory in V1 unless NIFTY intraday data is already reliable.

Run it later as a separate A/B experiment.

## 20. Gap Filter

Do not use a gap filter in V1.

Gap behavior should be measured first.

Later experiments can classify:

```text
Gap Up
Gap Down
Flat Open
```

and compare strategy expectancy independently.

## 21. News/Event Filter

Do not silently incorporate news into the strategy.

If a news/event filter is introduced, it must be:
- Explicit
- Timestamped
- Reproducible
- Available historically
- Tested as a separate experiment

No future knowledge.

## 22. Costs and Slippage

Every backtest must include realistic:

- Brokerage
- STT
- Exchange transaction charges
- GST
- SEBI charges
- Stamp duty
- Slippage

Costs must be applied to both entry and exit.

Run at least:

```text
ZERO_COST
BASE_COST
STRESS_COST
```

Do not report only gross P&L.

## 23. Signal State Machine

Hermes should implement explicit states:

```text
PRE_SESSION
    ↓
OR_BUILDING
    ↓
OR_LOCKED
    ↓
WAITING_FOR_BREAKOUT
    ↓
SIGNAL_CONFIRMED
    ↓
POSITION_OPEN
    ↓
POSITION_CLOSED
    ↓
SESSION_COMPLETE
```

Invalid state transitions must be rejected.

## 24. Long Signal Pseudocode

```python
if state == OR_LOCKED:
    if open_time >= 09:30 and open_time <= 11:25:
        if close > or_high:
            if close > vwap:
                if vwap > vwap_3_bars_ago:
                    if rvol >= 1.50:
                        if close_location >= 0.70:
                            if risk_limits_ok:
                                generate_long_signal()
```

## 25. Short Signal Pseudocode

```python
if state == OR_LOCKED:
    if open_time >= 09:30 and open_time <= 11:25:
        if close < or_low:
            if close < vwap:
                if vwap < vwap_3_bars_ago:
                    if rvol >= 1.50:
                        if close_location <= 0.30:
                            if risk_limits_ok:
                                generate_short_signal()
```

## 26. Hard Rejection Conditions

Reject the trade if any of these occur:

- Missing OHLCV
- Invalid timestamp
- OR incomplete
- OR range outside configured bounds
- Breakout occurs before OR lock
- Breakout is wick-only
- VWAP unavailable
- VWAP direction fails
- RVOL unavailable/insufficient history
- Volume threshold fails
- Candle strength fails
- Position size = 0
- Daily loss limit reached
- Maximum trades reached
- Symbol already has an open position
- Trading session is closed
- Corporate-action/data integrity issue
- Non-standard session day (v1.1, see Section 2)

## 27. Daily Risk Controls

Default:

```text
MAX_DAILY_LOSS = 1.50% of starting-day equity
MAX_TRADES_TOTAL = configurable
MAX_CONCURRENT_POSITIONS = configurable
```

Once maximum daily loss is reached:

```text
STOP ALL NEW ENTRIES
```

Existing positions continue to follow their predefined exits unless the risk engine specifies an emergency flatten rule.

## 28. Data Integrity Rules

Before each session:

- Verify 09:15–09:29 bars exist.
- Verify no duplicate bars.
- Verify timestamps are monotonic.
- Verify OHLC relationships:
  - High >= max(Open, Close)
  - Low <= min(Open, Close)
- Verify volume >= 0.
- Detect missing bars.
- Record data-quality status.

If critical data is invalid, do not trade that symbol/day.

## 29. Backtest Protocol

Hermes must not optimize everything simultaneously.

### Experiment V1

Fixed:

```text
ORB = 15 min
Timeframe = 5 min
VWAP = session VWAP
VWAP slope = 3 bars
RVOL = 20-bar SMA, RVOL_MODE = rolling_cross_session (v1.1)
RVOL threshold = 1.50
Candle strength = 70/30
Entry = next bar
SL = OR level + 0.10R buffer
TP = 2R
Max trades/symbol/day = 1
Risk/trade = 0.50%
No breakeven
No trailing
No retest
No news filter
No market filter
```

### Evaluation

Report:

- Number of trades
- Win rate
- Gross P&L
- Net P&L
- Profit factor
- Expectancy/trade
- Average R
- Max drawdown
- Max consecutive losses
- Sharpe/Sortino where appropriate
- Average holding time
- Long vs short performance
- Symbol-level performance
- Month-by-month performance
- Day-of-week performance
- Entry-time distribution
- Slippage sensitivity
- Cost sensitivity
- Signal-window RVOL-availability rate (v1.1 — % of otherwise-valid setups rejected for `RVOL_UNAVAILABLE`, to make the same_session_only vs rolling_cross_session comparison legible)

## 30. Anti-Lookahead Requirements

This is mandatory.

Hermes must guarantee:

- OR uses only bars inside 09:15–09:29.
- VWAP at signal uses data available at signal time.
- RVOL denominator excludes the current breakout bar.
- Entry cannot use future prices.
- Corporate actions use information available at the historical timestamp.
- Market filters use synchronized historical data.
- No future daily high/low/close information.
- No future news information.
- Universe membership as of the historical date, not today's list (v1.1, see Section 3).

Every signal should be reproducible from the historical event stream.

## 31. Logging

For every rejected or accepted signal, record:

```text
symbol
date
timestamp
direction
OR_HIGH
OR_LOW
OR_RANGE
close
VWAP
VWAP_SLOPE
volume
RVOL
RVOL_MODE
candle_strength
entry
stop
target
quantity
risk_amount
rejection_reason
strategy_version
```

For trades additionally record:

```text
entry_timestamp
entry_price
exit_timestamp
exit_price
exit_reason
gross_pnl
total_cost
net_pnl
R_multiple
MAE
MFE
```

## 32. Hermes Implementation Requirements

Hermes must:

1. Inspect the existing Profit Pilot architecture before modifying code.
2. Reuse existing data models, database schemas, backtest interfaces, cost models and execution abstractions where compatible.
3. Not create duplicate infrastructure.
4. Implement the strategy behind a clean strategy interface.
5. Keep all thresholds configurable.
6. Add deterministic unit tests for each rule.
7. Add integration tests for complete trade lifecycle.
8. Add data-quality tests.
9. Add look-ahead regression tests.
10. Run the existing test suite before and after changes.
11. Produce a backtest report with the metrics above.
12. Never push changes to remote unless explicitly instructed.
13. (v1.1) Add a unit test asserting `RVOL_MODE` never lets the lookback window include the current unclosed bar or any pre-open/post-close bar, regardless of mode.
14. (v1.1) Add a unit test for the same-bar SL/TP tie-break rule (Section 17) and for tick-size rounding (Section 11).

## 33. Recommended Configuration

```yaml
strategy:
  name: vwap_orb_equity
  version: "1.1"

session:
  timezone: Asia/Kolkata
  regular_start: "09:15"
  regular_end: "15:30"
  reject_non_standard_sessions: true

orb:
  duration_minutes: 15
  min_range_pct: 0.0025
  max_range_pct: 0.0200

signal:
  start: "09:30"
  end: "11:30"
  window_boundary: bar_open_time

vwap:
  slope_lookback_bars: 3

volume:
  lookback_bars: 20
  min_rvol: 1.50
  rvol_mode: rolling_cross_session

candle:
  long_min_close_location: 0.70
  short_max_close_location: 0.30

risk:
  risk_per_trade_pct: 0.50
  max_daily_loss_pct: 1.50
  max_trades_per_symbol_day: 1

exit:
  reward_risk: 2.0
  force_exit: "15:15"
  same_bar_sl_tp_priority: stop_loss_first

execution:
  entry: next_bar
  tick_size: 0.05
  slippage_model: configured
  commission_model: configured
```

## 34. Research Sequence

Do not optimize the entire strategy at once.

Run experiments in this order:

```text
E0  ORB only
E1  ORB + VWAP
E2  ORB + VWAP + Volume
E3  ORB + VWAP + Volume + Candle Strength
E4  Stop-loss comparison
E5  Target comparison
E6  OR duration comparison
E7  Signal-window comparison
E8  Market-regime filter
E9  Retest model
E10 Cost/slippage stress
E11 Out-of-sample validation
E12 Walk-forward validation
E13 RVOL_MODE comparison (v1.1 — same_session_only vs rolling_cross_session)
```

The purpose is to identify which component actually contributes edge.

## 35. Acceptance Criteria

Do not declare the strategy successful based on total profit alone.

A candidate should demonstrate:

- Positive net expectancy after realistic costs
- Acceptable drawdown
- Stable performance across time periods
- No dependence on a small number of symbols
- No dependence on one unusual market regime
- Reasonable long/short balance or a documented directional bias
- Robustness under slippage stress
- Robustness under parameter perturbation
- Positive out-of-sample performance
- No look-ahead/data leakage

## 36. Final Rule

**The backtest must tell us whether VWAP + ORB has an edge. We do not change the rules simply to make the historical equity curve look better.**

Every modification after V1 must receive a new strategy/experiment version and be compared against the frozen baseline.
