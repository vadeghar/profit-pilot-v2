# ICICI Breeze expired NIFTY options verification

## Execution status

The live redacted probe was rerun on 2026-09-24. Breeze accepted the sampled
requests (`Status: 200`) but returned `Success: []` for all sampled January
2024 entry-day strikes, including far-OTM 26,500 and 27,500 CE requests. This
establishes API reachability, not expired-option availability. No real
one-minute candle was returned, so no pilot backtest or coverage claim is made.
The metadata-only log is `logs/breeze_nifty_probe.jsonl`; credentials and
response payloads are not logged.

The existing authentication path was used:

```text
market_data.breeze_data_provider.BreezeHistoricalDataProvider.ensure_authenticated
lorentzian_strategy.data_loader.connect_breeze
```

Credential values were not printed or logged. The earlier authentication check
reported an expired session:

```text
Session key is expired.
```

That earlier result was superseded by the later redacted probe run above. No
real option rows or loader cache were created. The exact refresh action remains:

```powershell
python tools/breeze/breeze_auto_login.py --visible
```

The earlier blocked status was persisted in `breeze_data_coverage.json` and was
superseded by the golden-request and Stage A results recorded below.

## Official primary sources

1. ICICI Direct Breeze API documentation:
   https://api.icicidirect.com/breezeapi/documents/index.html
2. Official Idirect-Tech Python SDK repository:
   https://github.com/Idirect-Tech/Breeze-Python-SDK
3. Official SDK README (raw):
   https://raw.githubusercontent.com/Idirect-Tech/Breeze-Python-SDK/main/README.md
4. Official SDK implementation (raw):
   https://raw.githubusercontent.com/Idirect-Tech/Breeze-Python-SDK/main/breeze_connect/breeze_connect.py
5. Official PyPI package:
   https://pypi.org/project/breeze-connect/

## Documentation findings

| Topic | Official finding | Empirical result |
|---|---|---|
| SDK method | Installed SDK signature is `get_historical_data_v2(self, interval='', from_date='', to_date='', stock_code='', exchange_code='', product_type='', expiry_date='', right='', strike_price='')` | Not called because session gate failed |
| Option request | `exchange_code=NFO`, `product_type=options`, `stock_code=NIFTY`, `right=call/put`, strike supplied as a string, expiry supplied separately | Not empirically verified |
| Intervals | Official docs/SDK describe `1second`, `1minute`, `5minute`, `30minute`, `1day` | Not empirically verified |
| Date format | SDK README shows ISO strings with examples including `T05:30:00.000Z` and date-time ISO strings; official examples also show `T06:00:00.000Z` | Not empirically verified; repository handles Breeze’s observed wall-clock-Z quirk in `parse_broker_timestamp` |
| Candle cap | Official documentation states a maximum of 1000 records/candles per request; one-minute NSE session is approximately 375 bars, so chunking is required for larger windows | Not empirically verified |
| Rate limits | Official documentation states 100 API calls/minute and 5000/day | No calls made; no incidents |
| Session | Official SDK requires `generate_session(api_secret, session_token)`; repository requires a fresh daily token | Session verification failed: expired |
| Expired-contract history | Official sources reviewed do not state a guaranteed lookback limit specifically for expired NIFTY option contracts | DATA BLOCKED; probe required |
| Per-request date range | No reliable expired-option-specific date-range guarantee found in the reviewed primary sources | DATA BLOCKED; probe required |
| Margin | `get_margin(exchange_code=...)` is documented as account margin, with an NFO note; no documented hypothetical multi-leg basket margin calculator was found | DATA BLOCKED; likely broker-margin integration still required |
| NIFTY spot | Official examples distinguish equity/cash requests using NSE/cash and NIFTY; the exact historical-v2 NIFTY spot behavior remains to be empirically checked | DATA BLOCKED |
| Response fields | Official examples and repository normalization expect datetime/open/high/low/close/volume; open interest must be checked from live option response | DATA BLOCKED |

The official SDK README also claims access to up to ten years of historical
market data, but that is a general capability statement, not proof that every
expired NIFTY option strike has a complete one-minute history.

## Probe plan preserved in code

`tools/breeze/nifty_expired_options_probe.py` performs the small probes only
after authentication succeeds. It reuses
`market_data.option_symbol.get_option_symbol("breeze", ...)` and records only
redacted metadata. Planned checks include:

- January 2024 entry-day CE contracts and next-month expiry;
- `T06:00:00.000Z` versus `T07:00:00.000Z` expiry;
- over-1000-candle response behavior;
- earliest available month;
- far-OTM 26,500/27,500-type strikes;
- 15:16 addressing and timestamp fields;
- NIFTY spot request;
- safe call rate, retries, empty responses, invalid strikes, and expired sessions.

## Loader status

The loader was **not** implemented because the task explicitly gates it on
successful live probes. Building it before verifying expired-option semantics
would risk caching an incorrect timezone, date range, candle cap, or contract
availability assumption. Existing generic Breeze caching remains untouched.

After a valid session and successful probes, the loader must implement Stage A
entry-day candidate-strike requests and Stage B full-lifetime requests, with a
manifest containing contract key, fetched range, row count, hash, fetch time,
errors, fallback used, and REAL/PARTIAL/MISSING status.

## Coverage and go/no-go

| Measure | Result |
|---|---:|
| Months attempted | 0 |
| Months READY | 0 |
| Earliest usable month | None |
| API calls used | 0 |
| Rate-limit incidents | 0 |
| Real option rows | 0 |
| Modeled rows | 0 |

**Go/no-go: NO-GO for the full Breeze-backed NIFTY No Brainer backtest.**

Reason: the daily session token is expired and no real expired-option data has
been fetched. Refresh the token, rerun the probe script, then implement the
loader only after the probe results resolve the undocumented behaviors above.