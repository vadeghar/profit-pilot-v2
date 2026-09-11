# ProfitPilot Data Bridge — API Usage Guide

Local REST API exposing normalized DuckDB market data and metadata.
Every data endpoint returns **aggregated OHLCV candles**, one consolidated
endpoint per data type.

- **Base URL:** `http://localhost:8000` (when running)
- **Interactive docs (Scalar):** `http://localhost:8000/docs`
- **OpenAPI schema:** `http://localhost:8000/openapi.json`
- **Health check:** `http://localhost:8000/health`

```
uvicorn data.api:app --host 0.0.0.0 --port 8000 --reload
```

> **Applicability note**
> Aggregation on the below data endpoints applies to **equity, NIFTY spot, VIX
> and options** OHLCV candle data. **Institutional data is NOT OHLCV candle
> data and is excluded** from this service.

---

## 1. Interval aggregation (`?interval=…`)

All four data endpoints aggregate the raw **1-minute** candles into OHLCV
buckets using the **required** `interval` query parameter. Each response is
capped to the **800 most recent** aggregated buckets.

| `interval` value   | Aggregation                    |
| ------------------ | ------------------------------ |
| `ONE_MINUTE`       | 1-minute buckets               |
| `THREE_MINUTE`     | 3-minute aggregation           |
| `FIVE_MINUTE`      | 5-minute aggregation           |
| `TEN_MINUTE`       | 10-minute aggregation          |
| `FIFTEEN_MINUTE`   | 15-minute aggregation          |
| `THIRTY_MINUTE`    | 30-minute aggregation          |
| `ONE_HOUR`         | 1-hour aggregation             |
| `ONE_DAY`          | 1-day aggregation              |
| `WEEK`             | 1-week aggregation             |
| `MONTH`            | 1-month aggregation            |

### How each bucket is computed

Within each interval bucket:

- `open`   = first open, ordered by `trade_time`
- `high`   = maximum high
- `low`    = minimum low
- `close`  = last close, ordered by `trade_time`
- `volume` = sum of volume

### Bucket alignment — the 09:15 rule

For **intraday intervals** (`ONE_MINUTE`, `THREE_MINUTE`, `FIVE_MINUTE`,
`TEN_MINUTE`, `FIFTEEN_MINUTE`, `THIRTY_MINUTE`, `ONE_HOUR`), the **first
candle of each trading day always starts at 09:15** (NSE market open).
Pre-market data before 09:15 is excluded from aggregation. This applies
uniformly to `/equity`, `/spot`, `/vix` and `/options`.

For example, `THIRTY_MINUTE` buckets are: `09:15`, `09:45`, `10:15`, `10:45`, ...

`ONE_DAY`, `WEEK` and `MONTH` buckets keep their natural calendar boundaries
(not anchored to 09:15).

Passing an invalid `interval` value returns `422` listing the supported values.

### Dynamic session boundaries

Each symbol type has a parameterized session end time. The time grid always
starts at 09:15 and extends to the session end for that type:

| Symbol type | Session end | Example endpoints |
| ----------- | ----------- | ----------------- |
| Equity      | 15:15       | `/equity`         |
| Spot (NIFTY)| 15:30       | `/spot`           |
| VIX         | 15:30       | `/vix`            |
| Options     | 15:39       | `/options`        |

The grid is **continuous** — every interval bucket from 09:15 to session end
is present in the response, even if no source data exists for that bucket.

### Missing-data imputation

If a 1-minute candle is missing (illiquidity, network drops, etc.) or an
entire multi-minute interval has no data, the engine **imputes** the missing
timestamps to produce a fixed-gap, continuous time series:

- **Price (O, H, L, C)**: forward-filled from the `close` of the most recent
  valid candle. If the grid starts before any data (e.g. `from_date` has no
  data at 09:15), the first valid candle's `close` is **backward-filled**
  to the start of the grid.
- **Volume**: explicitly set to `0` for any artificially filled interval.

This guarantees zero missing intervals on the timeline.

---

## 2. Authentication (future enhancement)

An `X-Authentication` header is declared as an OpenAPI security scheme and is
shown in Scalar's **Auth** panel. It is currently expected to be **EMPTY** and
**no validation is performed** — it is reserved for a future authentication
enhancement. You may safely omit it or send it empty.

```
X-Authentication: <empty>
```

---

## 3. Endpoints

### 3.0 Common query parameters (data endpoints)

| Param      | Type   | Required | Description                                        |
| ---------- | ------ | -------- | -------------------------------------------------- |
| `fromDate` | date   | Yes      | Start date (YYYY-MM-DD)                           |
| `toDate`   | date   | No       | End date (YYYY-MM-DD). **When omitted, the first 800 candles from `fromDate` are returned (ascending order).** |
| `interval` | string | Yes      | Aggregation interval (see table in §1)            |

> **toDate behavior:**
> - **With `toDate`:** Returns the most recent 800 candles within the date range, with gap-filling for a continuous time series.
> - **Without `toDate`:** Returns the first 800 candles starting from `fromDate` (ascending order). No gap-filling is applied.
> - To request a single day, set `toDate` equal to `fromDate`.

### 2.1 System / Metadata (unchanged)

```
GET /health          # API status and total row counts
GET /meta/expiries   # Expiry calendar
GET /meta/holidays   # Trading holidays
```

---

### 3.2 VIX

```
GET /vix?fromDate=2026-09-01&toDate=2026-09-10&interval=ONE_HOUR
```

**Response fields:** `trade_time`, `open`, `high`, `low`, `close`, `volume`
(`trade_time` is the bucket start; first candle of each day is at 09:15).

---

### 2.3 Spot (NIFTY)

```
GET /spot?symbol=NIFTY&fromDate=2026-09-09&toDate=2026-09-10&interval=FIVE_MINUTE
```

| Param    | Type   | Required | Description                                      |
| -------- | ------ | -------- | ------------------------------------------------ |
| `symbol` | string | Yes      | Currently only `NIFTY` is supported (case-insensitive) |

**Response fields:** `trade_time`, `open`, `high`, `low`, `close`, `volume`

---

### 3.4 Options

```
GET /options?strike=24000&expiry=2026-09-30&optionType=CE&fromDate=2026-09-09&toDate=2026-09-10&interval=FIVE_MINUTE
```

| Param        | Type   | Required | Description                                      |
| ------------ | ------ | -------- | ------------------------------------------------ |
| `strike`     | int    | Yes      | Strike price (e.g. `24000`)                     |
| `expiry`     | date   | Yes      | Contract expiry date (YYYY-MM-DD)               |
| `optionType` | string | No       | `CE`, `PE` or `FUT`. **Default is `CE` & `PE`** |
| `fromDate`   | date   | Yes      | Start date (YYYY-MM-DD)                         |
| `toDate`     | date   | No       | End date (YYYY-MM-DD). **When omitted, the first 800 candles from `fromDate` are returned (ascending order).** |
| `interval`   | string | Yes      | Aggregation interval                            |

**Response fields:** `trade_time`, `open`, `high`, `low`, `close`, `volume`

---

### 3.5 Equity

```
GET /equity?symbol=RELIANCE&fromDate=2025-08-01&toDate=2025-08-05&interval=ONE_HOUR
```

| Param     | Type   | Required | Description                                      |
| --------- | ------ | -------- | ------------------------------------------------ |
| `symbol`  | string | Yes      | One of the supported 8 equities (below)         |
| `fromDate`| date   | Yes      | Start date (YYYY-MM-DD)                         |
| `toDate`  | date   | No       | End date (YYYY-MM-DD). **When omitted, the first 800 candles from `fromDate` are returned (ascending order).** |
| `interval`| string | Yes      | Aggregation interval                            |

**Currently supported symbols (8):**
`AXISBANK`, `BAJFINANCE`, `HDFCBANK`, `ICICIBANK`, `ITC`, `LT`, `RELIANCE`, `SBIN`

**Response fields:** `symbol`, `trade_time`, `open`, `high`, `low`, `close`, `volume`

---

## 4. Response cap

Each data endpoint returns **at most 800** aggregation buckets, always in
ascending time order.

- **With `toDate`:** The most recent 800 buckets within the date range are
  returned, with gap-filling for a continuous time series.
- **Without `toDate`:** The first 800 buckets starting from `fromDate` are
  returned (ascending order). No gap-filling is applied.