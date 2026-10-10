# Scalping Strategy Rules (as implemented)

All seven share the engine in `scalp_strategies/engine.py`. Ticks are aggregated per contract into
**15-second buckets**; "k minutes" below means the last k minutes of closed buckets. Signals are
checked once per closed bucket; exits on every tick of the held contract.

**ATM** = NIFTY spot rounded to the nearest 50. **Volume spike x N** = volume of the last 1 minute >=
N x the average 1-minute volume of the preceding baseline window (15 min for S1-S4, 20 min for
OI Burst). **VWAP** = the option's own volume-weighted price since the open. **CVD** = aggressive-buy
minus aggressive-sell volume (a trade at/above the ask is a buy, at/below the bid a sell; without
quotes, the tick rule). **Big print** = a traded tick whose LTQ >= 5x the running average LTQ
(exponential, alpha 0.02). **dOI(3m)** = OI change over the last 3 minutes.

## Common risk and exit rules

| Rule | S1-S4 | OI + Volume Burst |
|---|---|---|
| Entry window | 09:20-14:45 | 09:20-14:45 |
| Hard exit | 15:00 | 15:00 |
| Max trades / day | 3 | 3 |
| Stop for the day after | 2 losing trades | 2 losing trades |
| Cooldown after an exit | 3 min | 3 min |
| Stop loss | -10% of premium | -10% of premium |
| Target | +20% of premium | +20% of premium |
| Trailing | from +10%: stop = max(entry, peak - 8%) | stop raised to the previous 1-minute low |
| Other exits | 5-min time stop if the peak never reached +5% | LTP below VWAP; 5-min time stop (< +5%) |
| Capital / sizing | Rs 50,000; whole current balance per trade (compounding) | same |
| Expiry days | **never trade** (`EXPIRY_MODE = "skip"`) | **never trade** |

S1-S4 and OI + Volume Burst were calibrated on normal sessions; on the weekly expiry day premiums are
cheap and move fast, so the percentage stops that work on Rs 100 premiums get hit in seconds (on
2026-10-06 three such entries lost Rs 18k combined in trades lasting 13-74 s). They now stand aside on
expiry day and leave it to the two dedicated expiry cards (`EXPIRY_MODE = "only"`).

Expiry Trend Breakout and Expiry Gamma Squeeze have their own limits and exits - see their sections.

All thresholds are parameters (`ScalpConfig`); stop, target, risk, trade and loss limits and
slippage are editable on each card.

## S1 - Writer Squeeze (High-OI Wall Breach)

For calls (puts mirrored with strikes below spot):
1. Candidate walls: ATM, ATM+50, ATM+100, ATM+150 calls not more than 10 pts below spot; the wall is
   the one with the highest OI, and spot must be within 20 pts of it.
2. Wall call writers flee: wall CE dOI(3m) <= -3%.
3. Opposite side written: the same strike's PE dOI(3m) > 0.
4. ATM call volume spike x 2.5 (15-min baseline).
5. ATM call closes above its previous 5-minute high and above its VWAP.
6. Futures confirm: futures price up and futures OI up over 3 min.

Entry: buy the ATM call.

## S2 - Stealth Accumulation (Big-Print Breakout)

1. Spot's high-low range over the previous 15 minutes is <= 40 pts (`BOX_PTS`, the coil).
2. Spot closes above the box (calls) or below it (puts).
3. For the ATM or next strike on the breakout side: >= 3 big prints in the last 3 minutes (`MIN_BIG`,
   large-LTQ trades) and a volume spike x 2 (`VOL_MULT`).

Entry: buy that option, at half balance (`deploy_pct` 0.5).

Recalibrated from the original CVD / stealth-accumulation rules, which never fired - a <=20-pt box, CVD
>= 20% of volume and dOI >= 15% of volume are each ~2% likely, so their conjunction was ~never, and the
tight box did not predict good breakouts. The one predictive filter on the recorded week was big prints:
requiring >= 3 lifted the "+20% before the stop" rate from 26% (all breakouts) to 50%. Four non-expiry
days: 0 trades -> +Rs 1,138 (up on three of four days), and it wins on 2026-10-05 where S3 loses. Still
regime-sensitive - breakouts fail in chop - so it wants the same trend-day gate S3 does (regime log).

## S3 - Delta-PCR Velocity

1. Across ATM +/-2 strikes, sum call dOI and put dOI over the latest 3 minutes and the 3 minutes before.
2. Both windows: call OI falling and put OI rising -> bullish (calls); the reverse -> bearish (puts).
   In the latest window the falling side must shed at least 20% of what the rising side added
   (`PcrVelocity.MIN_UNWIND_RATIO`), and **both legs must actually move** - the smaller leg at least
   20% of the larger (`MIN_TWO_SIDED_RATIO`). This rejects one-sided OI shifts: on 2026-10-06 calls
   unwound 4.28M while puts built only 117k (3%), which is not a real PCR rotation.
3. The ATM option on that side: volume spike x 2, above VWAP; futures price moving the same way over 3 min.

Entry: buy the ATM option.

**Sizing (overrides the shared default).** On the recorded week S3's OI signal was marginal in the
choppy regime - right direction, often shaken out before the move. So S3 trades at **33% of balance**
with a **-20% stop and +50% target** (give the 15-30 min signal room), via its own `default_config`.
On the four non-expiry days this turned -Rs 12k into about +Rs 5k and cut the worst day from -Rs 12k to
-Rs 5k. It is capital protection, not an edge: the real fix is a trend-day activation gate, for which
`scalp_strategies/tools/regime_report.py` now logs a daily regime panel (ADX, opening-range hold, VWAP
extension/adherence, net/range) to `logs/regime_panel.csv` next to S3's outcome, to be calibrated once
enough days accumulate. India VIX will join the panel once its token is wired into the recorder.

## S4 - Trap Fade (trend-aligned)

1. Range = spot high/low over the 15 minutes before the last 2 minutes.
2. Trend = the futures' 60-minute efficiency ratio: net move / sum of the absolute 15-second moves
   (+1 = straight up, -1 = straight down, about 0 = chop).
3. Bull trap in a falling hour: in the last 2 minutes spot poked above the range high but is back below
   it, and the trend is <= -0.02 -> buy the ATM **put**.
4. Bear trap in a rising hour: mirrored (poke below the range low, back above it, trend >= +0.02) ->
   buy the ATM **call**.

Sizing and exits (S4's own defaults): half the balance per trade, stop -20%, target +40%, no trailing
stop, and an unconditional exit after 45 minutes. A stop costs about 10% of the balance.

**Why it was recalibrated (week of 2026-10-01..08).** The original rules also required futures OI flat,
the breakout side's writers adding >= 3% OI on a 2x volume spike, and the opposite option at a new
1-minute high. They traded twice in the week and lost both. "Futures OI flat" held 74-99% of the time
(no information); +3% ATM OI in 3 minutes held 10-20% of the time and did not predict the fade. The bare
poke-and-fall-back was a coin flip - it won on range days and lost on trend days, because half the fades
were bets against an established move. Splitting the same traps by the hour's direction separated them:

| Same trap, entered... | Trades | Win rate | Avg per trade |
|---|---|---|---|
| against the hour (the original idea) | 28 | 46% | about -1% |
| with the hour (now S4) | 18 | 83% | about +14% |
| with the hour, no trap (trend only, control) | 34 | 50% | about +5% |

(15-second-bar study, four non-expiry days, stop -20% / target +40% / 45-minute exit.) The result held
for 10-20 minute ranges, 2-3 minute pokes and trend floors of 0.00-0.04; a 30-minute range did not work,
a 5-minute poke was weaker, and a trailing stop cut the winners short. On the tick-level engine with the
live defaults (fresh Rs 50,000 each day, max 3 trades): 11 trades, 9 winners, +Rs 16.3k / +5.4k / +4.0k /
+19.3k on 1, 5, 7, 8 Oct; worst trade -Rs 4.0k. Expiry day (6 Oct) lost Rs 7.8k, so S4 keeps skipping
expiry days.

Caveats: 11 trades over four days is a small sample, the week had two strong down days (8 of the 11
trades were puts), and the rule was chosen on the same days it is reported on - expect live results well
below these. Half size is the guard until more days accumulate.

## OI + Volume Burst

Candidates: ATM CE and PE plus one ITM and one OTM strike on each side (a call is ITM below spot, a
put above). All four must hold for a candidate:

1. **Volume spike + LTQ burst:** last 1-minute volume >= 3x the average of the previous twenty
   1-minute bars, and >= 5 big prints (LTQ >= 5x the average LTQ) within the last 10 seconds.
2. **Buildup:** over 3 minutes, price up with OI up (long buildup) or price up with OI down (short
   covering). Price down with OI up (short buildup) is rejected.
3. **Opposite-side unwinding:** the opposite ATM option's OI fell >= 2% in 3 minutes (for a call,
   the ATM put; and vice versa).
4. **LTP above VWAP.**

If several candidates qualify, the one with the largest volume spike is bought. The spec lists
"LTP breaks the last 5-minute high with a volume spike" among monitored signals but not among the
entry rules; it is available as `OiVolumeBurst.REQUIRE_BREAKOUT` (off by default). The spec's
"buy the opposite side on short buildup" alternative is not implemented.

## Expiry Trend Breakout (expiry day only)

Found by testing ~3,600 time/price rule sets on every NIFTY and SENSEX weekly expiry from Sep-2025 to
Sep-2026 (1-minute Breeze data, `scalp_strategies/research/`). Blind option buying on expiry lost on average; the
rule below was profitable on both indices and in both halves of the year.

1. Today is the expiry day of the recorded option chain (other days it never trades).
2. Between 11:00 and 14:30: the index's range so far today (high - low of its 1-minute closes since
   09:15) is >= 0.5%.
3. A 1-minute close above that high buys a CE; below that low, a PE.
4. Strike by price: the option trading nearest Rs 40 (accepted range Rs 20-64).
5. Open interest agrees: summed over the ATM +/- 4 strikes, put OI exceeds call OI for a CE (put
   writers underneath), call OI exceeds put OI for a PE. A breakout that fails this is skipped, and
   the next new high/low is checked again.

One trade per direction per day; stop -30%, target +100%, square-off 15:10; no trailing or time stop.
Sizing: 25% of the current balance per trade (`deploy_pct`). The paper session starts with the app
(`AUTO_START`) and stays idle on non-expiry days; a Stop from the dashboard is remembered.

Study result for NIFTY replayed through this engine (`scalp_strategies/research/replay_expiry_engine.py`, 57 expiry
days, LTP +/- Rs 0.5 fills):

| Rules | Trades | Winners | Avg per trade | Longest losing streak | Rs 50,000 at 25% per trade | Max drawdown |
|---|---|---|---|---|---|---|
| 1-4, entries to 15:05 (original) | 44 | 34% | +10.4% | 6 | Rs 92,785 | -47.7% |
| 1-5, entries to 14:30 (current) | 31 | 48% | +31.4% | 4 | Rs 338,401 | -27.7% |

Rule 5 and the 14:30 cut-off were added on 2026-10-10 from a second pass over the same expiries.
Entries after 14:30 lost (too little time left to double). Every breakout taken against the OI balance
was stopped out, and the OI rule raised the average in all 30 start-time x range variants tried, in both
halves of the year, and with every stop/target/premium variant. No other indicator (trend strength,
momentum, option volume, straddle decay, time since the last extreme) helped on both indices and both
halves. Caveats: the OI rule was the best of about 30 indicators screened and SENSEX history has no OI,
so it is verified on NIFTY only and on the days it was chosen on - expect well under +31% live. On the
one tick-recorded expiry (2026-10-06) put OI led and the losing CE trade would still have been taken.
Other differences from the study: one position at a time (the study allowed a CE and a PE together),
and a restart during the session loses the morning's range.

## Expiry Gamma Squeeze (expiry day only, spec v2.0)

Order-flow option buying between 13:15 and 14:50 on the expiry day. Paper experiment: on 1-minute history
the testable parts (everything except ask aggression) came out between -17.6% and +1.6% per trade
depending on how fast the entry fills, so the aggression filter has to supply the edge.

Regime, re-checked on every closed 15-second bucket for each CE/PE strike:
1. premium Rs 12-25;
2. the strike's own OI fell >= 1.5% over the last 15 minutes (its writers are covering);
3. the near-month future is above its VWAP for a CE, below it for a PE.

Each strike in regime gets a resting stop-limit buy: trigger = its 5-minute high + 0.20, limit = trigger + 0.40.

Trigger, checked on every snapshot of an armed strike once LTP reaches the trigger:
1. the last 60 s of traded volume > 2x the average minute of the previous 15 minutes;
2. ask aggression over the last 30 s >= 60%: the share of traded volume in snapshots whose LTP was at or
   above the previous snapshot's best ask;
3. the ask (+ slippage ticks) is within the limit - otherwise no fill.

A rejected trigger disarms that strike until the next bucket re-checks the regime.

Exits: target +100%; stop -35% (filled as a market order, not the spec's stop-limit, so a fast fall cannot
leave the position open); stop lifted to entry + 0.50 once the premium is up 40%; out after 8 minutes if it
never reached +25%; flat at 15:10. Max 3 trades a day, none after 2 consecutive stop-outs (a breakeven or
time exit resets the count), 10-minute cooldown. Sizing: one lot (`sizing="fixed"`).

Forward-test log (`signals` in `data/forward_test/scalping/scalp_expiry_gamma.json` and in the status API):
every evaluated trigger with its quotes, flow numbers and outcome (`FILLED`, `REJECTED_VOLUME`,
`REJECTED_AGGRESSION`, `NO_FILL_ABOVE_LIMIT`); each trade with its worst and best excursion; and for a
breakeven exit a `shadow` row - what the original stop and target would have done.

Needs bid/ask: on Breeze 1-second days aggression falls back to the tick rule and fills to LTP + 0.5.
NIFTY only; SENSEX waits for BSE tick recording.

## Closing auction session

Since the closing auction session started the index stops updating at 15:15 while derivatives trade on
to 15:40 (seen in the 1-Oct-2026 recording: no index change after 15:15, options still trading). Both
expiry cards are therefore flat by 15:10; S1-S4 and OI Burst already square off at 15:00.

## Experimental options (off by default)

Both are `ScalpConfig` fields, so they can be switched per run without a code change. Live paper
trading uses the defaults; `scalp_strategies/tools/condition_report.py` replays them every day in its
"Experimental options" section so they can be judged on more than one session.

- `big_print_basis = "volume"` - a big print is a snapshot whose **traded volume since the previous
  snapshot** is >= 5x the running average, instead of one whose last-trade quantity is. Angel sends a
  snapshot roughly every 1.5-2 s and its LTQ is only the last of the trades in that gap, so the LTQ
  basis under-counts bursts (on 2026-10-01 the most big prints any strike had in 10 s was 2 by LTQ and
  4 by volume). Affects S2's big-print rule and OI Burst's burst rule.
- `box_rel = 0.75` - S2's box limit becomes max(20 pts, 0.75 x the median 15-minute spot range seen so
  far today), available after 30 minutes of evaluations. A fixed 20 pts cannot be met on a volatile
  day (2026-10-01: tightest 15-minute range 24 pts, median 51).

On 2026-10-01 neither option produced a trade: S2 was still blocked by its CVD rule and OI Burst by
its opposite-side rule.

## Differences from the source script

- One position and one set of daily limits **per strategy** (the script shared them across S1-S4),
  so each card runs independently.
- Fills at real bid/ask instead of LTP +/- 0.5 when quotes exist; charges from the platform table
  (STT 0.15% from Apr-2026) instead of the script's 0.1%.
- Sizing by the whole compounding balance (Rs 50,000 start) instead of a fixed 1 lot.
- Empty 15-second buckets carry the last price/OI forward (the script merged gaps into one bucket).
