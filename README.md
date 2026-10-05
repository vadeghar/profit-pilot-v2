# Automation Engines

Paper-trading and backtesting platform for Indian markets (NSE / BSE derivatives), with a FastAPI
dashboard. Strategies live in three folders by trading style; everything else is shared platform code.

## Folder structure

```
automation_engines/
├── scalp_strategies/        # tick scalpers on NIFTY weekly options (7 strategies, all paper)
│   ├── engine.py            #   shared engine: tick aggregation, fills, risk limits, exits
│   ├── orderflow.py         #   S1 Writer Squeeze, S2 Stealth Accumulation, S3 Delta-PCR Velocity, S4 Trap Fade
│   ├── oi_burst.py          #   OI + Volume Burst
│   ├── expiry_breakout.py   #   Expiry Trend Breakout (expiry day only)
│   ├── expiry_gamma.py      #   Expiry Gamma Squeeze (expiry day only)
│   ├── backtest.py          #   replay of recorded ticks through the same engines
│   ├── paper_trader.py      #   live paper sessions fed by the tick recorder
│   ├── tools/               #   condition_report, daily_summary (Telegram), import_breeze_1s
│   └── research/            #   expiry-day data download and rule studies
├── trading_strategies/      # regular intraday / swing strategies (not scalpers)
│   ├── index_oi_momentum/   #   Index Options OI Momentum - switched off (deprecated until further notice)
│   └── nifty_afternoon_momentum/  # NIFTY Afternoon Momentum - experimental research candidate (own backtest)
├── investment_strategies/   # long-horizon strategies (empty for now)
│
├── core/                    # domain models (Order, Trade, Candle, Signal) + StrategyBase / StrategyRegistry
├── backtest/                # generic candle backtest engine, charge tables, streaming job manager
├── execution/               # order execution + risk manager, forward-test runner, runner registry
├── market_data/             # data providers (Breeze, Angel, yfinance), tick recorder/store, option symbols, calendars
├── brokers/                 # Mock, Angel One SmartAPI and ICICI Breeze adapters
├── persistence/             # JSONL journal + atomic state snapshots
├── platform_config/         # paths, universe.yaml (symbols), strategy_flags.yaml (dashboard flags / status)
├── utils/                   # logging, IST time helpers, Telegram, Breeze SDK loader
├── cli/ + main.py           # command-line interface
├── web_app.py               # FastAPI dashboard & REST API (uvicorn web_app:app)
├── tools/breeze/            # ICICI Breeze auto-login
├── deploy/linux/            # server install, systemd unit, cron scripts
├── docs/                    # scalping/ (rules, tick data), trading/, broker and login guides
└── tests/
```

Runtime data (never committed): `data/ticks/` recorded ticks, `data/forward_test/` paper state,
`data/cache/`, `logs/`, and `.env` with broker credentials.

## Where a new strategy goes

| Kind | Folder | How it plugs in |
|---|---|---|
| Tick scalper | `scalp_strategies/` | Subclass `ScalpEngine`, add the class to `SCALP_STRATEGIES` in `scalp_strategies/__init__.py`, add a card in `web_app.py` (`_SCALP_CARDS`) and flags in `platform_config/strategy_flags.yaml`. Backtest, paper trading, the daily report and the Telegram summary pick it up from the registry. |
| Regular trading strategy | `trading_strategies/<name>/` | Subclass `core.strategy.StrategyBase` in `strategy.py`, register it in `trading_strategies/__init__.py`, add a catalog entry in `web_app.py` and flags in `strategy_flags.yaml`. It then runs on the generic backtest engine, the CLI and the dashboard. |
| Investment strategy | `investment_strategies/<name>/` | Same as a trading strategy, registered in `investment_strategies/__init__.py`. |

## Running

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m uvicorn web_app:app --host 127.0.0.1 --port 9090
```

Open http://localhost:9090. While the server runs it records NIFTY ticks 09:12-15:42 IST on trading
days and resumes every paper session that was running before a restart.

Scalper tools:

```bash
python -m scalp_strategies.tools.condition_report --date 2026-10-05   # why each scalper did / did not trade
python -m scalp_strategies.tools.daily_summary --dry-run              # preview the Telegram summary
python -m scalp_strategies.tools.import_breeze_1s --date 2026-09-29   # rebuild a past day from Breeze
```

CLI for the registered (non-scalper) strategies: `python main.py strategy list`, `python main.py status`.

Tests: `python -m pytest tests` (broker-credential and Playwright tests need a live login / browser).

## Documentation

- `docs/scalping/README.md` - how the scalpers run, endpoints, limitations
- `docs/scalping/STRATEGIES.md` - the exact rules of every scalper
- `docs/scalping/TICK_DATA.md` - tick format and storage
- `docs/trading/INDEX_OI_MOMENTUM_REPORT.md` - Index OI Momentum
- `docs/trading/NIFTY_AFTERNOON_MOMENTUM.md` - NIFTY Afternoon Momentum: research, rules, backtest and verdict
- `deploy/linux/README.md` - server deployment, schedules (Breeze login, Telegram summary, condition report)
- `docs/BROKER_CREDENTIALS.md`, `docs/BREEZE_AUTO_LOGIN_GUIDE.md` - credentials and the daily Breeze login

Credentials are read from `.env` at the project root (see `.env.example`); it is git-ignored.
