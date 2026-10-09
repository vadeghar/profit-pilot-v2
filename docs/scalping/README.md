# NIFTY Option Tick Scalpers

Five intraday NIFTY option-buying scalpers driven by tick data, each running as its own
independent background paper session with its own dashboard card:

| Card | Strategy id | Idea |
|---|---|---|
| S1 Writer Squeeze | `scalp_writer_squeeze` | Breach of the highest-OI strike while its writers flee and the opposite side is written |
| S2 Stealth Accumulation | `scalp_stealth_accum` | Spot boxed in 20 pts while an option is quietly bought (CVD, big prints), then breaks out |
| S3 Delta-PCR Velocity | `scalp_pcr_velocity` | Two consecutive 3-minute windows of call-OI unwinding + put-OI building (or the reverse) |
| S4 Trap Fade | `scalp_trap_fade` | Fake range breakout with no futures OI and writers absorbing: buy the other side |
| Expiry Trend Breakout | `scalp_expiry_breakout` | Expiry day only: after 11:00 a new day high/low on a >= 0.5% range buys the option nearest Rs 40; stop -30%, target +100% |
| Expiry Gamma Squeeze | `scalp_expiry_gamma` | Expiry day only, 13:15-14:50: a Rs 12-25 option with falling own OI breaks its 5-min high on 2x volume and >= 60% ask aggression; one lot; logs every signal |
| OI + Volume Burst | `scalp_oi_volume_burst` | Volume spike + LTQ burst + long buildup/short covering + opposite-side OI unwinding + above VWAP |

Exact rules: [STRATEGIES.md](STRATEGIES.md). Tick format and storage: [TICK_DATA.md](TICK_DATA.md).
Sources: `learning_scalping_strategies.md` + `nifty_orderflow.py` (S1-S4) and the "OI + Volume Burst
Scalping" spec. **Paper only - these engines never place real orders.**

## How it fits together

```
Angel One SmartWebSocketV2 (SNAP_QUOTE: LTP, LTQ, volume, OI, best bid/ask)
        |
        v
market_data/tick_recorder.py  TickHub (one connection, NIFTY spot + future + ATM+/-10 CE/PE)
        |---> data/ticks/angel/<date>/ticks.csv(.gz)       every tick, saved for backtests
        |---> ScalpPaperSession x7  (scalp_strategies/paper_trader.py, one thread + queue each)
                    |
                    v
             scalp_strategies/  ScalpEngine subclasses (15 s buckets, fills at bid/ask, risk limits)

scalp_strategies/backtest.py  replays data/ticks through the SAME engines (live == backtest logic)
scalp_strategies/tools/import_breeze_1s.py  rebuilds past days from Breeze 1-second bars (pseudo-ticks)
```

## Running it

1. **Keep the dashboard server running during market hours.** The tick recorder starts itself at
   09:12 IST on every trading day and stops at 15:42 (compressing the day's file). No clicks needed;
   set `TICK_AUTO_RECORD=0` in the environment to disable. Angel credentials come from `.env`
   (`ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PASSWORD_OR_MPIN`, `ANGEL_TOTP_SECRET`).
2. **Start a strategy:** open its card, set capital (default Rs 50,000; stop / target / capital deployed in the parameter
   panel), click **START PAPER SCALPING**. It runs in the background until you stop it - across days;
   outside market hours it simply waits for ticks. Each card shows "PAPER RUNNING" and a Stop button.
3. **Backtest:** open a card, pick a date range and click **RUN BACKTEST**. It replays every
   recorded day in the range (real Angel ticks preferred, Breeze 1-second days otherwise).
   The card's panel lists which days are backtestable.
4. **Past days:** `python -m scalp_strategies.tools.import_breeze_1s --date 2026-09-29 --date 2026-09-26`
   (about 510 Breeze requests / 6 minutes per day; Breeze allows ~5,000 requests a day).

Warm-up: signals need 16-21 minutes of history per contract, so the first possible entry is around
09:31 (S1-S4) / 09:36 (OI Burst) even though entries are allowed from 09:20.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/paper/scalp/{id}/start` `{capital, params}` | start one strategy's paper session |
| `GET /api/paper/scalp/{id}/status` | balance, open position, trades, recorder status (`IDLE` if never started) |
| `POST /api/paper/scalp/{id}/stop` | stop it (open position closed at the last price) |
| `GET /api/ticks/status` | recorder state + list of backtestable days |
| `POST /api/ticks/start` / `stop` | manual recorder control (stop refuses while a scalper runs) |
| `POST /api/backtest/stream` with `strategy_id=scalp_*` | tick-replay backtest (dashboard RUN BACKTEST) |

Paper state (balance + full trade log) persists in `data/forward_test/scalping/<id>.json`. A session
that was running when the server stopped (restart, crash, reboot) resumes automatically at startup;
only a Stop from the dashboard ends it. Linux deployment: [deploy/linux/README.md](../../deploy/linux/README.md).

## Execution realism

- Buy fills at the **recorded best ask** + `slippage_ticks` x Rs 0.05, sells at the best bid - the
  spread is paid from real quotes, and reported per trade as `spread_cost` (vs the mid-price).
- Breeze 1-second days have no quotes: fills use LTP +/- 0.5 points.
- Charges: brokerage Rs 20/order, STT on the sell premium (0.15% from Apr-2026), exchange, SEBI,
  stamp, GST - `backtest/charges.py`.
- Capital: Rs 50,000 by default (paper and backtest), editable on each card.
- Sizing compounds: lots = floor(current balance x deploy_pct / (entry price x lot size)), max 27 lots
  (NSE freeze limit 1,800 / 65). With deploy_pct = 1 the whole balance is deployed, so every win grows and
  every loss shrinks the next trade. A signal is skipped (and counted on the card) when one lot costs more
  than the balance. `sizing="risk"` switches to risking `risk_pct` of the balance to the stop instead.

## Known limitations

- **LTQ-based signals** (S2 big prints, OI Burst LTQ burst) only mean what the spec intends on real
  Angel ticks. On Breeze 1-second days "LTQ" is the whole second's volume (median ~54,000 units on
  the 29-Sep-2026 ATM call, heavy-tailed), so "5 prints >= 5x average within 10 s" essentially never
  happens: on that imported day OI Burst saw 191 volume spikes and 364 opposite-side unwinds but zero
  LTQ bursts. Judge S2 and OI Burst on recorded Angel days; the backtest adds a warning otherwise.
- SnapQuote ticks are exchange snapshots, not every trade; OI updates arrive every 1-3 s, which is
  why every OI/volume feature is computed on 15-second buckets.
- **Daily Telegram summary:** `deploy/linux/scalp_summary.sh` (cron, 15:25 IST on trading days) sends each
  scalper's trades, wins/losses, net P&L and balance, plus the combined total (`scalp_strategies/tools/daily_summary.py`).
- **Daily regime panel:** `deploy/linux/regime_report.sh` (cron, 15:55 IST on trading days) appends one
  row (ADX, opening-range hold, VWAP extension/adherence, net/range, S3's P&L) to `logs/regime_panel.csv` -
  the trend/chop dataset for a future S3 day-type activation gate (`scalp_strategies/tools/regime_report.py`).
- **Daily condition report:** `deploy/linux/condition_report.sh` (cron, 15:50 IST on trading days) writes
  `logs/condition_report_<date>.md` - how often each entry condition held, the closest near-misses and
  what blocked them, for the live rules and for the experimental options.
- None of these strategies has been backtested yet - there is no tick history until the recorder has
  run (or days are imported). Treat them as experimental until they have a meaningful sample.
