# Scalping Strategy Rules (as implemented)

All five share the engine in `strategies/scalping/engine.py`. Ticks are aggregated per contract into
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

## S2 - Stealth Accumulation (CVD + Box Breakout)

1. Spot's high-low range over the previous 15 minutes is <= 20 pts (the box).
2. Spot closes above the box (calls) or below it (puts).
3. For the ATM or next strike on the breakout side: 15-minute CVD >= 20% of its volume; >= 3 big
   prints in the last 3 minutes; volume spike x 2; |dOI(3m)| >= 15% of the 3-minute volume (churn filter).

Entry: buy that option.

## S3 - Delta-PCR Velocity

1. Across ATM +/-2 strikes, sum call dOI and put dOI over the latest 3 minutes and the 3 minutes before.
2. Both windows: call OI falling and put OI rising -> bullish (calls); the reverse -> bearish (puts).
   In the latest window the falling side must shed at least 20% of what the rising side added
   (`PcrVelocity.MIN_UNWIND_RATIO`), so a token unwind against heavy writing does not count as a shift.
3. The ATM option on that side: volume spike x 2, above VWAP; futures price moving the same way over 3 min.

Entry: buy the ATM option.

## S4 - Trap Fade

1. Range = spot high/low over the 15 minutes before the last 2 minutes.
2. Bull trap: in the last 2 minutes spot poked above the range high but is back below it; futures OI
   flat or down (dOI(3m) <= +0.1%); ATM call writers added >= 3% OI with a volume spike x 2; the ATM put
   breaks its previous 1-minute high -> buy the ATM **put**.
3. Bear trap: mirrored -> buy the ATM **call**.

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

## Differences from the source script

- One position and one set of daily limits **per strategy** (the script shared them across S1-S4),
  so each card runs independently.
- Fills at real bid/ask instead of LTP +/- 0.5 when quotes exist; charges from the platform table
  (STT 0.15% from Apr-2026) instead of the script's 0.1%.
- Sizing by the whole compounding balance (Rs 50,000 start) instead of a fixed 1 lot.
- Empty 15-second buckets carry the last price/OI forward (the script merged gaps into one bucket).
