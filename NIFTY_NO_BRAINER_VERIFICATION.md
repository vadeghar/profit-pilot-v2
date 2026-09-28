# NIFTY No Brainer — verification report

## Scope and data status

The implementation reviewed is `strategies/nifty_no_brainer.py` (functions and
classes: `CalendarEngine`, `OrderManager.resolve_legs`, `RiskEngine`,
`NiftyNoBrainer.enter/should_exit`, and `run_nifty_no_brainer_backtest`). The
independent reference is `strategies/nifty_no_brainer_reference.py`.

The checkout initially contained no NIFTY option-chain 1-minute history and no
exchange contract-master file. The request fix and live Stage A rerun below
now demonstrate real option candles, while the full Stage B/pilot remains
gated on complete lifetime streams and contract-master verification.

## Compliance matrix

| Rule | Existing behavior found | Status | Severity / correction |
|---|---|---|---|
| R1 | NIFTY symbols, CE legs; expiry source not contract master | AMBIGUOUS | High; inject master resolver |
| R2 | Last Friday with previous trading-day fallback | MATCH (assumption) | Low; assumption documented |
| R3 | 15:16 candidate; missing post-15:16 spot now falls back to day’s last close | MATCH | High |
| R4 | Previously Thursday calculation; corrected API accepts contract master | MISMATCH→CORRECTED | Critical |
| R5 | 1:-2:1 CE structure | MATCH | — |
| R6 | Previously 50 grid; corrected buy/sell selection is 100 grid | MISMATCH→CORRECTED | High |
| R7 | Previously `round_to_50`; corrected nearest-100/upward tie behavior | MISMATCH→CORRECTED | High |
| R8 | Previously +300/+600; corrected fixed 300 gap | MATCH→CORRECTED | — |
| R9 | Previously hard-coded +1600; corrected 500-grid parameterized hedge | MISMATCH→CORRECTED | Critical |
| R10 | Proxy `(700+debit)*lot`; not broker margin | MISMATCH | Critical; broker margin adapter still required |
| R11 | Debit check only; no credit band | MISMATCH | Critical; reference implements both |
| R12 | No shifting | MISSING | Critical; reference implements shift loop; production feed integration remains required |
| R13 | +2.5% capital | AMBIGUOUS | High; must use deployed margin |
| R14 | -3% capital, exact close | MISMATCH | Critical; gap-through fill integration required |
| R15 | 15 days, 15:15 | MISMATCH→CORRECTED | High; default 18 and parameter 19 |
| R16 | No adjustment/re-entry in lifecycle | MATCH | Medium; overlap assertion still needed in runner |
| R17 | No gap-through simulation | MISSING | High; reference accepts first available fill |
| R18 | Fixed 65 defaults in UI/strategy | MISMATCH→CORRECTED | Critical; require contract master/config |
| R19 | No valid backtest available | MISSING | Informational; data blocked |
| R20 | Paper/live gate not wired for this lifecycle | MISSING | Critical; automation remains go-live blocked |

## Required vectors

Reference outputs: ATM 24,850→24,900; 26,050→26,100; 25,000→25,000.
Base 25,000→buy 25,300/sell 25,600; ATM 24,900→25,200/25,500.
Hedges: 25,600→26,500; 25,500→26,500; 26,700→27,500. The video’s
24,700→26,000 example is ambiguous and conflicts with the formal algorithm;
formal output is 25,500 for sell 24,700.

## Divergences and proposed patches

1. Replace `round_to_50` strike construction with `select_strikes` (applied).
2. Replace `base+1600` with `hedge_strike` (applied).
3. Add contract-master expiry and period lot-size inputs (API/default correction
   applied; caller still must provide master data).
4. Add premium credit shifting and margin-based entry decisions (reference
   applied; production option-stream orchestration remains a required patch).
5. Use margin, not capital or premium, for target/stop and record the margin
   method (production broker adapter required).
6. Add first-available post-breach fills and paper scheduler/monitor (not
   possible to validate without execution/data adapter; live remains OFF).

## Verdict

Before correction: 3 MATCH, 6 MISMATCH, 4 MISSING, 7 AMBIGUOUS/partial.
After the pure reference and strike/fallback corrections: rule logic is covered
for R1–R9, R11–R12, R15, and R17 in the reference module; production remains
**NO-GO** until contract-master, broker-margin, option 1-minute data, costs,
paper scheduler, and gap-through execution adapters are connected and the full
differential/backtest is rerun.

## Task pilot status (2026-09-24)

| Area | Result |
|---|---|
| Disk fix | Recovered checkout preserved; task work is on `task/nifty-no-brainer-breeze-pilot`; no merge performed. |
| Loader | Resumable two-day chunking, CSV cache, SHA-256 manifest entries, deduplication, retry/rate accounting, and mocked-client tests are present. |
| Live probes | 16 redacted requests reached Breeze and returned `Status: 200` with zero rows. The 15:16, far-OTM coverage/gaps, >1000-candle limit, earliest month, T06:00/T07:00 variation, safe rate, and invalid-strike/no-data behavior are **UNRESOLVED**, not inferred. |
| Six-month pilot | **BLOCKED**: no real option candles and no contract-master-backed six-month input were available. No synthetic P&L is reported; margin proxy and ±20% reruns were not run. |

### Coverage disposition

| Period | Status | Reason |
|---|---|---|
| All months tested | BLOCKED | Sampled expired-option requests returned zero rows; no month can be marked READY or PARTIAL. |

**Go/No-Go: NO-GO** for the full Jan-2024→latest run. A non-empty
expired-option response, empirical probe matrix, contract master, and
broker-margin method must be recorded first.

## Breeze request fix and Stage A rerun (2026-09-24)

### Root cause

The request formatter was correct. The failing probe used **2024-01-26**, but
that was the NSE Republic Day holiday. The known-good request uses **2024-01-25**.
The old holiday file omitted 2024-01-26, so the entry-day helper selected a
non-trading day and Breeze correctly returned zero rows. A second, independent
finding is that far strikes must actually have traded: 26,500/27,500 returned
zero on the tested February-2024 contract/date while 22,000/22,200/22,500/
23,000 returned 21 rows.

| Field | Golden request | Corrected behavior |
|---|---|---|
| stock_code | NIFTY | unchanged |
| exchange/product | NFO / options | unchanged |
| expiry_date | 2024-02-29T06:00:00.000Z | unchanged; T07 also returned rows in bisect |
| right/strike | call / "22100" | unchanged |
| interval | 1minute | unchanged |
| window | 2024-01-25T09:40:00.000Z → 10:00:00.000Z | 21 rows |
| bad window | 2024-01-26T09:40:00.000Z → 10:00:00.000Z | 0 rows (holiday) |

The exact golden request is saved at `logs/breeze_nifty_golden_request.json`;
field-by-field bisect logs are in `logs/breeze_nifty_probe.jsonl`.

### Fix and tests

- Added 2024 NSE holidays, including Republic Day.
- Expiry fallback uses Thursday through August 2025 and Tuesday from September
  2025; a contract master remains preferred and required by production paths.
- Added exact Breeze golden-format tests and calendar regression tests.
- Bisect confirmed Z-suffixed timestamps are required; IST offsets and date-only
  requests fail, while full-day, 15:10–15:20, T00:00Z, T06:00Z/T07:00Z, and
  put requests returned data for the known-good contract.
- Focused suite: **50 passed**.

### Expiry ground truth

| Month | Official/verified expected | Fallback function |
|---|---:|---:|
| 2024-02 | 2024-02-29 | 2024-02-29 |
| 2025-02 | 2025-02-27 | 2025-02-27 |
| 2025-09 | 2025-09-30 | 2025-09-30 |
| 2026-02 | 2026-02-24 | 2026-02-24 |

The NSE API pages timed out in this environment; the official NSE derivatives
bhavcopy archive was reachable. The full ground-truth note is
`tools/breeze/nifty_expiry_ground_truth.json`; a historical contract-master
check is still required before declaring all 36 dates authoritative.

### 36-month Stage A coverage

Artifact: `logs/nifty_36m_stage_a.json`. Requests covered each fallback-calendar
entry day, 15:10–15:20, and strikes ATM+300/+600/+1500. Each READY leg had 11
rows and a 15:16 candle.

| Month range | READY | PARTIAL | BLOCKED |
|---|---:|---:|---:|
| 2023-10 → 2026-09 (36 months) | 33 | 2 (`2024-08`, `2024-11`) | 1 (`2026-09`, future entry/no spot) |

Partial details: 2024-08 far 26,800 had zero rows; 2024-11 far 25,600 had
8 rows and no 15:16 candle. The remaining 33 months had all three 15:16
candles.

### Pilot

Stage B full-lifetime data was not fetched yet. The existing reference runner
requires complete, aligned option streams through expiry and a verified
contract master/lot-size source; Stage A alone is insufficient for truthful
entry premiums, decisions, exits, P&L, or ±20% margin reruns. Pilot status is
therefore **BLOCKED pending Stage B and contract-master verification**, not a
fabricated result.

## Scoped 2025+ run status (2026-09-24)

The requested Jan-2025 through Aug-2026 run was stopped at Step 1. This is a
real external-source blocker:

- D: free space was approximately 442 GB, so the 2 GB safety limit was not
  approached.
- `pyarrow` is installed and compressed/Parquet storage is available.
- The older official archive host/path used for the 2024 verification returns
  404 for sampled 2025 daily files.
- The current `nsearchives.nseindia.com` equivalents timed out.
- Current NSE historical expiry/API candidates returned the generic NSE 404
  page in this environment.
- Breeze exposes `get_margin`, `margin_calculator`, and `add_margin`, but that
  does not establish the required NSE trading-day, expiry, and period lot-size
  facts.

The previous `logs/nifty_36m_stage_a.json` is not used as the requested result:
it includes pre-2025 months, uses fallback expiries, and does not contain the
full candidate-strike/Stage-B dataset requested here. No 2025+ scoped fetch,
margin run, or P&L backtest was performed with guessed contract facts.

**Historical NSE-source blocker:** this no longer blocks the Breeze-only task
path below, but remains relevant only if exchange-authoritative contract facts
are required later.

## Resumed Breeze-only 2025+ run status

The NSE dependency was removed and the Breeze-only fetch was allowed to run for
all 20 requested months (2025-01 through 2026-08).

| Measure | Result |
|---|---:|
| Months fetched | 20/20 |
| Breeze requests cached | 1,939 |
| Breeze rows cached | 121,823 |
| Compressed Parquet cache | approximately 11 MB |
| Free disk after fetch | approximately 441 GB |
| Request errors | 7 individual Stage-A strike requests |

Cache: `data/breeze_nifty_ratio_2025/`; manifest:
`data/breeze_nifty_ratio_2025/manifest.json`.

The first result writer was found to contain a deliberate implementation defect:
it wrote placeholder zero MTM and `MAX_HOLD` values instead of calculating
15-minute exits, and it labeled credit cases `SHIFT 0` without fetching and
evaluating shifted variants. The corrected chunking pass fetched Stage B in
two-day-or-shorter chunks and included NIFTY spot, but the cached Stage-A
manifest does not contain every selected leg under the expected label (for
example, the January-2025 selected 23,800 leg is absent). Consequently the
offline recomputation could not reconstruct complete entry prices for all
months.

Therefore the cached `backtest_results.json` and `computed_report.json` are
then rerun Steps 1–7. Paper-trading go/no-go for 25-Sep-2026 remains **NO-GO**.
No trades, win rate, expectancy, profit factor, drawdown, author-claim
comparison, or Jan-30-2026 gap-up conclusion is reported as fact. The exact
remaining blocker is a corrected runner/cache manifest that stores every
selected and shifted leg, then computes real MTM from those rows. Paper trading
remains **NO-GO**.