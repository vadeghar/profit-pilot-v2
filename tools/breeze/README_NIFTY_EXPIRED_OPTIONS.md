# Expired NIFTY option probes and loader gate

## Current status

The live gate currently fails because the configured Breeze session is expired.
No historical-data request was issued and no real option data was created.

Refresh the daily session manually, without printing it:

```powershell
python tools/breeze/breeze_auto_login.py --visible
```

Then run:

```powershell
python tools/breeze/nifty_expired_options_probe.py
```

The probe writes only request metadata, response shape, row counts, field names,
timestamps, and redacted error text to `logs/breeze_nifty_probe.jsonl`.

The production loader is intentionally gated until the probes establish the
actual expired-contract behavior, timestamp semantics, safe chunk size, and
error/rate-limit behavior. It must use `market_data.option_symbol.get_option_symbol`
and `BreezeHistoricalDataProvider`; it must not create guessed symbols or
modeled prices.