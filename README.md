# Trading Strategy Execution Platform

An institutional-grade algorithmic trading platform for Indian markets (NSE, NFO, BSE, MCX), implementing the **v2 Architecture**:

- **WebSocket-first market data** with an in-memory `CandleBuilder` (avoids REST rate limits).
- **Append-only JSONL event journals** with atomic snapshots.
- **Multi-leg execution orchestrator** with leg-risk rollback.
- **Simulated & live backtesting engine** (slippage, commissions, Sharpe, drawdown, equity curve).
- **FastAPI web dashboard** for backtests, live instances, paper trading and monitoring.
- **Six built-in strategies**: `ema_crossover`, `rsi`, `breakout`, `mcx_trend_rider`, `equity_swing_vcp`, `index_oi_momentum`.

---

## Project Layout

```
automated-engines/
├── backtest/          # Backtesting engine + OI-momentum tick backtest
├── brokers/           # Mock, Angel One SmartAPI, ICICI Breeze adapters
├── cli/               # CLI parser & command handlers
├── core/              # Domain models (Order, Trade, Position, Candle, Signal)
├── execution/         # Order routing, risk manager, forward-test runner
├── market_data/       # WebSocket feed, CandleBuilder, symbol resolver, universes
├── persistence/       # JSONL journal + atomic state snapshots
├── platform_config/   # Configuration + central project paths
├── strategies/        # All strategy implementations + registry
├── tools/breeze/      # ICICI Breeze auto-login & page inspectors
├── scripts/           # start_dashboard.sh, setup_breeze_auto_login.sh
├── tests/             # Broker / forward-test / tuning scripts
├── docs/              # Reports, guides, Breeze login screenshots
├── data/              # Runtime data (historical cache, backtests, forward tests)
├── logs/              # Runtime logs
├── web_app.py         # FastAPI dashboard & REST API
├── main.py            # CLI entry point
├── run_strategy.sh    # Primary bash launcher
├── requirements.txt   # Pinned dependencies
└── .env           # Broker credentials (NOT committed)
```

> **Configuration lives in `platform_config/`** (renamed from `config/` so it no longer
> shadows the `config` module used internally by the `breeze_connect` SDK).

---

## 1. Environment Setup

```bash
cd /Users/apple/work/automated-engines
source .venv/bin/activate
# (first time only)
python -m pip install -r requirements.txt
```

---

## 2. Quick Start — Bash Runner (`run_strategy.sh`)

```bash
./run_strategy.sh list                      # list all registered strategies

./run_strategy.sh backtest ema_crossover    # backtest any strategy
./run_strategy.sh backtest rsi --instrument NSE:BANKNIFTY --timeframe 15m --capital 200000
./run_strategy.sh backtest mcx_trend_rider --instrument "MCX_GOLDM, MCX_SILVERM, MCX_CRUDEOIL" --capital 2000000
./run_strategy.sh backtest index_oi_momentum --instrument "NIFTY, BANKNIFTY"

./run_strategy.sh web 8080                  # launch the web dashboard
```

---

## 3. Python CLI

The package is installed (editable) as `trading-platform`:

```bash
trading-platform strategy list
trading-platform backtest <strategy_id> [--instrument <inst>] [--timeframe <tf>] [--capital <cap>]
trading-platform status
trading-platform position list
trading-platform order list
```

Or directly:

```bash
python3 main.py strategy list
python3 main.py backtest ema_crossover --capital 200000
```

---

## 4. Web Dashboard

```bash
source .venv/bin/activate
python3 -m uvicorn web_app:app --host 0.0.0.0 --port 8080
# or:  ./run_strategy.sh web 8080
# or:  ./scripts/start_dashboard.sh
```

Then open **http://localhost:8080** for: interactive backtests with equity curves,
strategy parameter tuning, live instance deployment, forward/paper testing and the
positions/orders monitor.

Key REST endpoints: `/api/catalog`, `/api/strategies`, `/api/status`,
`/api/backtest`, `/api/backtest/oi-momentum`, `/api/strategy/start`,
`/api/order/place`, `/api/forward-test/register`, `/api/paper/oi-momentum/start`.

---

## 5. Brokers & Credentials

Credentials are read from **`.env`** at the project root (Git-ignored).

| Broker | Status | Notes |
|---|---|---|
| **Mock** | ✅ Default, no setup | Used for backtests and the dashboard |
| **Angel One SmartAPI** | ✅ Configured | `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PASSWORD_OR_MPIN`, `ANGEL_TOTP_SECRET` |
| **ICICI Breeze** | ✅ Wired | Lazy-loaded; needs a valid Breeze session token. Auto-login: `tools/breeze/breeze_auto_login.py` |

---

## 6. ICICI Breeze Auto-Login

```bash
source .venv/bin/activate
python tools/breeze/breeze_auto_login.py --visible   # Playwright + Telegram OTP
python tools/breeze/breeze_auto_login.py --headless   # cron / background
```

See `docs/BREEZE_AUTO_LOGIN_GUIDE.md` for the full guide.

