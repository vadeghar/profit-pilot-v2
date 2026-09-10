# Hermes Glossary — Domain Terms & Naming Quirks

Always load this file. It prevents Hermes from re-deriving (or getting
wrong) conventions that are already settled in the codebase.

## Instrument naming (master-data / DuckDB)
| Term as used in code/data | Meaning |
|---|---|
| `NIFTY 50` | Underlying symbol for NIFTY index — NOT `NIFTY` |
| `INDIA VIX` | Underlying symbol for the volatility index, instrument_type=INDEX |
| `CE` | Call option (instrument_type) |
| `PE` | Put option (instrument_type) |
| `EQ` | Equity (instrument_type) |
| `INDEX` | Index instrument (instrument_type) |

## Architecture terms
| Term | Meaning |
|---|---|
| `master-data` branch | Data-layer branch of profit-pilot-v2; serves candle/instrument data via HTTP on :8000. All data access from strategy/backtest code goes through this API, never direct DB queries. |
| `master` branch | Strategy/backtest code branch of profit-pilot-v2 |
| `OptionsStrategy` interface | Generic strategy interface (strategies/base.py) — use for strategies that fit simple per-bar entry/exit logic |
| `TradeResult` | Standard output shape all strategies must adapt into, so the UI/reporting layer doesn't need per-strategy changes |
| Dedicated state machine | Pattern used for strategies too complex for the generic engine (multi-stage averaging, continuous polling) — see NIFTY ATM Straddle as the reference implementation |

## Reference strategies (read before implementing something new)
- **NIFTY Blaze Butterfly** — weekly zero-adjustment Put Broken-Wing Butterfly
- **NIFTY Titan Condor** — weekly Friday-entry low-probability iron condor
- **NIFTY ATM Straddle** — intraday long ATM CE+PE straddle, 2pm entry,
  staged averaging + partial exits, cost-based trailing stop; built as its
  own state machine since it didn't fit the generic engine

## Data source
- Backtesting data: DuckDB, served via HTTP from `master-data` at
  `http://127.0.0.1:8000` (not direct Postgres access — that was the old
  profit-pilot v1 setup)
