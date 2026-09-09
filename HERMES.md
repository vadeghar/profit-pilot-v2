# Hermes — Data Layer Context (profit-pilot-v2-data)
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
