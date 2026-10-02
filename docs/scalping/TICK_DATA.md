# Tick Data: Source Format and Storage

## Angel One SmartAPI WebSocket 2.0 (live source)

The recorder uses `SmartApi.smartWebSocketV2` in **SNAP_QUOTE mode (3)** - the only mode whose
packet carries open interest, last traded quantity and market depth (LTP mode = price only; QUOTE
mode = OHLC/volume without OI). Each binary packet is decoded by the library into a dict:

| Field | Meaning | Unit / note |
|---|---|---|
| `token` | exchange token | NIFTY 50 = `99926000` on NSE (exchangeType 1); F&O on NFO (exchangeType 2) |
| `exchange_timestamp` | exchange time of the update | epoch **milliseconds** |
| `last_traded_price` | LTP | **paise** (divide by 100) |
| `last_traded_quantity` | LTQ of the last trade | units |
| `average_traded_price` | ATP for the day | paise |
| `volume_trade_for_the_day` | cumulative volume | units (0 for the index) |
| `total_buy_quantity` / `total_sell_quantity` | pending bid / ask quantity | units |
| `open_price_of_the_day`, `high_price_of_the_day`, `low_price_of_the_day`, `closed_price` | day OHLC / previous close | paise |
| `last_traded_timestamp` | time of the last trade | epoch seconds |
| `open_interest` | OI | contracts x lot units (0 for the index) |
| `open_interest_change_percentage` | OI change vs previous day | |
| `best_5_buy_data` / `best_5_sell_data` | 5 levels of bids / asks: `price` (paise), `quantity`, `no of orders` | the library's `flag` field swaps sides internally; the recorder re-checks bid <= ask |
| `upper_circuit_limit`, `lower_circuit_limit`, `52_week_high_price`, `52_week_low_price` | | paise |

A tick is a **snapshot**, sent when anything in it changes - not one packet per trade. OI is updated
by the exchange every 1-3 s. The library allows one WebSocket per session; Angel allows a small
number of concurrent connections per client, so the platform opens exactly **one** (the tick hub)
and fans ticks out to every scalper in-process.

Subscription (per trading day, from the Angel scrip master): NIFTY 50 spot, the nearest NIFTY
future, and nearest-weekly-expiry CE + PE for ATM +/- 10 strikes (44 tokens). If spot moves more than
6 strikes from the centre, the missing strikes are subscribed too (capped at 100 tokens).

## Stored format (all sources)

```
data/ticks/<source>/<YYYY-MM-DD>/instruments.json
data/ticks/<source>/<YYYY-MM-DD>/ticks.csv      # while the day is being recorded
data/ticks/<source>/<YYYY-MM-DD>/ticks.csv.gz   # after 15:42 compaction
```

`ticks.csv` columns (prices in **rupees**, timestamps ISO-8601 IST with milliseconds):

| Column | Meaning |
|---|---|
| `ts` | exchange timestamp |
| `token` | instrument key into `instruments.json` |
| `ltp`, `ltq` | last traded price, last traded quantity |
| `volume` | cumulative day volume |
| `oi` | open interest |
| `bid`, `ask`, `bid_qty`, `ask_qty` | best bid / ask and their quantities (0 when unknown) |
| `atp` | average traded price |
| `tbq`, `tsq` | total pending buy / sell quantity |
| `ltt` | last traded time (epoch seconds) |

`instruments.json`: `{source, underlying, expiry, feed, instruments: {token: {token, symbol, kind
(IDX/FUT/CE/PE), strike, lot, expiry, exchange}}}`. A restarted recorder appends to the same day.

Size: roughly 50-150 MB of CSV per day for 44 tokens, about 5-6x smaller after gzip.

Read it back with `market_data.tick_store.read_ticks(source, day)` (sorted by timestamp) and
`load_instruments(source, day)`; `list_days()` lists everything recorded.

## Breeze 1-second pseudo-ticks (past days)

Angel has no tick history. `tools/scalping/import_breeze_1s.py` rebuilds a past session from ICICI
Breeze `get_historical_data_v2(interval="1second")`, which returns OHLC, that second's volume and open
interest for NIFTY spot, futures and expired options. Each 1-second bar becomes one row in the same
schema under `data/ticks/breeze_1s/<date>/`: `ltp` = close, `ltq` = the second's volume, `volume`
= cumulative, `oi` = open interest, **bid/ask = 0**. Tokens are synthetic (`NIFTY-IDX`,
`NIFTY-FUT`, `NIFTY-22550-CE`, ...), strikes are the opening ATM +/- 5.

When a day exists from both sources, backtests use the Angel ticks.
