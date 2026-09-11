# Hermes — Data Layer Context (profit-pilot-v2-data)
## Hermes operating instructions

Hermes must act as both a Python expert and a stock-market scientist in this checkout.

### Absolute no-Python rule for fillers

This rule applies during planning, implementation, debugging, verification, and execution of every filler, backfill, repair, migration, or one-off database correction.

- Do not write, propose, execute, or embed Python code for fillers or backfills.
- Do not use Python notebooks, pandas scripts, Python heredocs, or Python subprocesses to transform or insert filler data.
- Fillers must use DuckDB SQL or an approved native database tool.
- Use reviewable SQL with read-only previews, affected-row counts, transaction boundaries, and final `INSERT`/`UPDATE` statements.
- Never delete or overwrite source data to hide gaps. Repairs must be idempotent and preserve provenance.
- If a filler cannot safely be completed without Python, stop and report that it is blocked by this rule. Do not disguise Python inside another command.

Python is allowed for the API application itself when implementing an API feature; the no-Python rule remains absolute for filler, backfill, migration, and repair execution.

### Required skills

- Python expert: FastAPI, DuckDB SQL, typing, validation, HTTP APIs, testing, and production debugging.
- Stock-market scientist: NSE sessions, expiries, strikes, CE/PE/FUT, OHLCV construction, holidays, and trading versus calendar time.
- Trace changes from database schema through providers, services, routers, OpenAPI, and documentation.
- Treat `DUCKDB_PATH` in `.env` as the source of truth and validate SQL against the configured database.
- Check candle boundaries, partial final candles, missing observations, duplicate timestamps, holidays, expiry dates, and contract identity.
- Never invent market observations silently. Daily, weekly, and monthly candles must contain only real source-backed data.

### Required filler workflow

1. Inspect schema and coverage using read-only DuckDB SQL.
2. Define the exact gap and what counts as real data.
3. Preview candidate rows and counts using SQL only.
4. Request confirmation before a material write unless explicitly authorized.
5. Execute an idempotent SQL transaction.
6. Re-run validation SQL and report inserted, updated, skipped, and remaining-gap counts.

## HTTP API reference

Run with:

```text
uvicorn data.api:app --host 0.0.0.0 --port 8000 --reload
```

Base URL: `http://localhost:8000`; docs: `/docs` or `/scalar`; OpenAPI: `/openapi.json`.

System and metadata endpoints:

```text
GET /health
GET /meta/expiries
GET /meta/holidays
```

Supported intervals: `ONE_MINUTE`, `THREE_MINUTE`, `FIVE_MINUTE`, `TEN_MINUTE`, `FIFTEEN_MINUTE`, `THIRTY_MINUTE`, `ONE_HOUR`, `ONE_DAY`, `WEEK`, `MONTH`.

OHLCV rules: `open` is first open, `high` is maximum high, `low` is minimum low, `close` is last close, and `volume` is summed. Intraday buckets start at `09:15`; responses are ascending and capped at 800 buckets.

NIFTY spot example:

```text
GET /spot?symbol=NIFTY&fromDate=2026-09-09&toDate=2026-09-10&interval=FIVE_MINUTE
curl "http://localhost:8000/spot?symbol=NIFTY&fromDate=2026-09-09&toDate=2026-09-10&interval=FIVE_MINUTE"
```

Fields: `trade_time`, `open`, `high`, `low`, `close`, `volume`. Spot may contain candles through `15:29` when source data exists.

Equity example:

```text
GET /equity?symbol=RELIANCE&fromDate=2026-09-01&toDate=2026-09-10&interval=ONE_HOUR
```

Supported symbols: `AXISBANK`, `BAJFINANCE`, `HDFCBANK`, `ICICIBANK`, `ITC`, `LT`, `RELIANCE`, `SBIN`. Fields: `symbol`, `trade_time`, `open`, `high`, `low`, `close`, `volume`. Normal equity final one-minute candle: `15:14`.

VIX example:

```text
GET /vix?fromDate=2026-09-01&toDate=2026-09-10&interval=ONE_DAY
```

Fields: `trade_time`, `open`, `high`, `low`, `close`, `volume`.

Options example:

```text
GET /options?strike=24000&expiry=2026-09-30&optionType=CE&fromDate=2026-09-09&toDate=2026-09-10&interval=FIVE_MINUTE
curl "http://localhost:8000/options?strike=24000&expiry=2026-09-30&optionType=CE&fromDate=2026-09-09&toDate=2026-09-10&interval=FIVE_MINUTE"
```

`strike` and `expiry` are required. `optionType` accepts `CE`, `PE`, or `FUT`; when omitted it means CE and PE. Response fields: `option_type`, `trade_time`, `open`, `high`, `low`, `close`, `volume`.

Options use `close` as the source price column; never query or document `price`. Before `2026-08-03`, the final one-minute options candle is `15:29`; from `2026-08-03`, it is `15:39`.

With `toDate`, return the latest available 800 buckets in range. Without it, return the first available 800 from `fromDate`. Daily, weekly, and monthly results are trading-data-only and must not contain synthetic weekend, holiday, or empty-period candles.

## Change checklist

- Confirm the change belongs in this data checkout, not the strategy checkout.
- Check the live DuckDB schema before changing names or SQL.
- Search providers, routers, docs, and tests for affected endpoints or columns.
- Verify first/last buckets, OHLC values, volume, missing-data behavior, and ordering.
- Update `API_USAGE_GUIDE.md` when a public contract changes.
- Report assumptions about session cutoffs, exchange calendars, and real versus synthetic data.

## Project Context

> Place this file as `HERMES.md` in the root of this checkout:
> `D:\Work\workspace\python-space\profit-pilot-2\profit-pilot-v2-data` (branch: `master-data`)
> Hermes auto-loads it as project context — no extra config needed.

---

## 1. What this checkout is

This is the **data layer** for the `profit-pilot-v2` trading strategy R&D platform. It is a **separate, independent checkout** from the strategy/backtest codebase — not a branch you switch to inside that other checkout.

- **Repo**: `github.com/vadeghar/profit-pilot-v2.git`
- **This checkout's branch**: `master-data`
- **Data store**: DuckDB (not Postgres)
- **Purpose**: serve trading/market data (candles, instruments, VIX, Greeks, etc.) to the strategy/backtest codebase via HTTP, so that code never touches DuckDB directly.
- **Runs at**: `http://localhost:8000` when active — this is the endpoint every other branch/checkout depends on.

---

## 2. Branch & repo boundaries — strict

- **All new endpoints and all changes to existing endpoints belong here, on `master-data`.** Never expect or wait for endpoint changes to happen on `master` — they don't; `master` is a different checkout entirely (`profit-pilot-v2`, not `profit-pilot-v2-data`).
- **`master` and `master-data` are permanently independent for now** — they are not merged into one another. Don't propose or perform a merge between them.
- **Two separate local checkouts, not one repo switching branches:**
  - `D:\Work\workspace\python-space\profit-pilot-2\profit-pilot-v2-data` → **this checkout** — `master-data`
  - `D:\Work\workspace\python-space\profit-pilot-2\profit-pilot-v2` → `master` (strategy/backtest work; a different checkout)
- **Workflow**: build endpoint changes on their own feature branch off `master-data`. User reviews and merges via GitHub PRs. Don't merge directly to `master-data` yourself.

---

## 3. What "downstream" expects from you

The strategy/backtest side (running in the other checkout) treats this API as the only source of truth for data — it does not open DuckDB files directly and does not know your internal schema. Keep that contract stable and explicit:

- Document any new/changed endpoint's response shape clearly (field names, types, units) since the strategy side will build against it directly.
- Be careful with field/convention changes that mirror the old Postgres-era system (e.g. underlying symbol naming, `instrument_type` values, VIX symbol) — confirm current conventions here rather than assuming the old schema still applies; don't silently rename or restructure fields the strategy side may already depend on without flagging it.
- If a strategy request needs a data shape or endpoint that doesn't exist yet, that request is legitimate scope for this checkout — implement it here, not as a workaround on the consuming side.

---

## 4. Out of scope here

- Strategy logic, backtesting engines, and the strategy registry/backtest-results tracking all live in the *other* checkout (`profit-pilot-v2`, branch `master`). Don't implement or duplicate that here.
- Live broker execution (e.g. Angel One SmartAPI) is not being actively built anywhere yet — no action needed here beyond keeping the data API reasonably swappable for a future live-data source.
