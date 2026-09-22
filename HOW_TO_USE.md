# How to Use the Trading Strategy Execution Platform

A step-by-step operational guide for configuring, testing, running strategies, and using both the CLI runner and the Web Dashboard.

---

## Table of Contents

1. [Architecture Overview & Prerequisites](#1-prerequisites--environment-setup)
2. [Configuration Guide (Where to Configure What)](#2-configuration-guide-where-to-configure-what)
3. [Step-by-Step Usage](#3-step-by-step-usage)
   - [Step 1: Running Backtests via Bash Runner](#step-1-running-backtests-via-bash-runner)
   - [Step 2: Running Backtests via Python CLI](#step-2-running-backtests-via-python-cli)
   - [Step 3: Using the Interactive Web Dashboard](#step-3-using-the-interactive-web-dashboard)
   - [Step 4: Managing Strategy Instances](#step-4-managing-strategy-instances)
   - [Step 5: Managing Positions & Orders](#step-5-managing-positions--orders)
4. [Broker Setup (Mock, Angel One, ICICI Breeze)](#4-broker-setup)
5. [Log Files, Data Storage & Audit Trails](#5-log-files-data-storage--audit-trails)
6. [Troubleshooting & FAQ](#6-troubleshooting--faq)

---

## 1. Prerequisites & Environment Setup

The platform is written in **Python 3.12** and runs natively on Linux/macOS.

### Directory Structure

```text
/Users/apple/work/automated-engines/
├── backtest/                 # Backtesting engine, metrics & OI-momentum tick backtest
├── brokers/                  # Broker adapters (Mock, Angel One, ICICI Breeze)
├── cli/                      # CLI parser & handlers
├── platform_config/          # System, execution & broker configs + central paths
├── core/                     # Domain models (Order, Trade, Position, Candle)
├── execution/                # Order routing, Multi-leg orchestrator, Risk manager
├── market_data/              # WebSocket tick consumer, CandleBuilder, resolvers
├── persistence/              # JSONL append-only audit journal & snapshots
├── strategies/               # ema_crossover, rsi, breakout, mcx_trend_rider,
│                             #   equity_swing_vcp, index_oi_momentum
├── tools/breeze/             # ICICI Breeze auto-login & page inspectors
├── scripts/                  # start_dashboard.sh, setup_breeze_auto_login.sh
├── tests/                    # Broker, forward-test & tuning scripts
├── docs/                     # Reports, guides & Breeze login screenshots
├── data/                     # Runtime data (historical cache, backtests, forward tests)
├── run_strategy.sh           # Executable bash launcher
├── web_app.py                # Interactive FastAPI & Web Dashboard
├── main.py                   # CLI entrypoint
├── requirements.txt          # Pinned dependencies
└── setup.py                  # Package installation file
```

### Installation Verification

Verify that the `trading-platform` CLI is registered:

```bash
trading-platform --help
# Or test the bash launcher:
/Users/apple/work/automated-engines/run_strategy.sh --help
```

---

## 2. Configuration Guide (Where to Configure What)

All platform parameters are defined in YAML and validated by `/Users/apple/work/automated-engines/platform_config/__init__.py`. A template configuration file is provided at `/Users/apple/work/automated-engines/platform_config/platform_config.example.yaml`.

### Configuration Matrix

| Section | Parameter | Default Value | Description |
| --- | --- | --- | --- |
| `system` | `dataDir` | `"data"` | Folder for storing snapshots, backtest pickle files, and logs. |
|  | `logLevel` | `"INFO"` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
|  | `heartbeat.intervalSeconds` | `30` | Heartbeat write frequency for health checks. |
|  | `heartbeat.stallThresholdSeconds` | `120` | Threshold after which the daemon is considered stalled. |
|  | `marketData.staleThresholdSeconds` | `10` | Disconnect detection time if no ticks arrive. |
| `broker` | `active` | `"mock"` | Broker to route orders through (`mock`, `angel_one`, `icici`). |
|  | `angel_one.api_key` | `""` | SmartAPI API key from Angel One developer portal. |
|  | `angel_one.client_id` | `""` | Angel One Client Code / Login ID. |
|  | `angel_one.totp_secret` | `""` | Base32 secret for automated TOTP token generation. |
|  | `icici.api_key` | `""` | ICICI Breeze API key. |
|  | `icici.session_key` | `""` | Daily generated session key from Breeze portal. |
| `execution` | `orderRetryAttempts` | `3` | Max retry attempts for rejected or timed-out orders. |
|  | `partialFillTimeoutSeconds` | `30` | Seconds to wait before taking compensating action on partial multi-leg fills. |
|  | `partialFillBehavior` | `"ROLLBACK"` | Action on failed multi-leg: `ROLLBACK` (exit filled leg) or `FREEZE`. |
|  | `maxDailyLossInr` | `25000.0` | Global kill-switch threshold in Indian Rupees. |
| `persistence` | `journalEnabled` | `true` | Enables append-only JSONL event journal (`events.jsonl`). |
|  | `snapshotIntervalSeconds` | `300` | State snapshot interval (atomic temp file rename with `fcntl` lock). |
| `notifications` | `telegram.enabled` | `false` | Enable/disable Telegram alerts. |
|  | `telegram.botToken` | `""` | Telegram bot token from @BotFather. |
|  | `telegram.chatId` | `""` | Chat ID or channel ID for emergency alerts. |
| `backtest` | `fillModel` | `"TICK"` | Simulated execution model: `INSTANT`, `TICK`, or `CANDLE_CLOSE`. |
|  | `slippagePercent` | `0.05` | Simulated slippage percentage per trade (0.05 = 0.05%). |
|  | `commissionPercent` | `0.03` | Brokerage & exchange transaction fee percentage. |

---

## 3. Step-by-Step Usage

### Step 1: Running Backtests via Bash Runner

The script `/Users/apple/work/automated-engines/run_strategy.sh` wraps environment configuration, paths, and execution flags:

1. **Backtest EMA Crossover with defaults (NIFTY 50, 90 days, ₹1,00,000 capital):**

   ```bash
   /Users/apple/work/automated-engines/run_strategy.sh backtest ema_crossover
   ```

2. **Backtest RSI Strategy on Bank Nifty with 15-minute candles:**

   ```bash
   /Users/apple/work/automated-engines/run_strategy.sh backtest rsi --instrument NSE:BANKNIFTY --timeframe 15m --capital 250000
   ```

3. **Backtest Breakout Strategy with custom parameters:**

   ```bash
   /Users/apple/work/automated-engines/run_strategy.sh backtest breakout --params '{"lookback": 15, "quantity": 2}'
   ```

4. **List all available strategies:**

   ```bash
   /Users/apple/work/automated-engines/run_strategy.sh list
   ```

---

### Step 2: Running Backtests via Python CLI

You can also use the direct installed command `trading-platform`:

```bash
# Basic syntax:
trading-platform backtest <strategy_id> [flags]

# Specific Date Range:
trading-platform backtest ema_crossover --start-date 2026-06-01 --end-date 2026-09-15

# Specific Instrument & Capital:
trading-platform backtest breakout --instrument MCX:CRUDEOIL --capital 500000 --timeframe 1h
```

**Backtest Output Metrics Explained:**

- **Total Return:** Net profit/loss in INR and percentage gain.
- **Max Drawdown:** Peak-to-trough equity decline percentage (measures downside risk).
- **Win Rate:** Percentage of trades closed with positive realized P&L.
- **Sharpe Ratio:** Risk-adjusted return measure.
- **Profit Factor:** Gross profits divided by gross losses.
- **Results Path:** Saved as a pickle file in `data/backtests/<strategy>_<timestamp>.pkl`.

---

### Step 3: Using the Interactive Web Dashboard

The web dashboard provides a graphical backtester, real-time equity curve visualization, and a strategy manager.

1. **Start the Web Dashboard (if not already running):**

   ```bash
   /Users/apple/work/automated-engines/run_strategy.sh web 8080
   ```

2. **Open in Browser**:Navigate to `http://localhost:8080` (or view directly in the sandbox preview tab).

3. **Run Backtests Interactively:**

   - Select strategy from the dropdown (`EMA Crossover`, `RSI`, `Donchian Breakout`).
   - Select instrument (`NSE:NIFTY`, `NSE:BANKNIFTY`, `MCX:CRUDEOIL`, `NSE:RELIANCE`).
   - Tune strategy parameters in the UI (e.g., Fast EMA: `9`, Slow EMA: `21`).
   - Click **RUN SIMULATION**.
   - Review the **Equity Curve chart** and the **Trade Log Table** showing trade IDs, entry/exit prices, and realized P&L.

---

### Step 4: Managing Strategy Instances

To manage strategy execution in paper/live mode:

```bash
# 1. Start the platform daemon
trading-platform start

# 2. Start a specific strategy instance
trading-platform strategy start ema_crossover --instrument NSE:NIFTY

# 3. Check running status
trading-platform strategy status ema_crossover

# 4. Stop a strategy instance
trading-platform strategy stop ema_crossover

# 5. Stop the engine
trading-platform stop
```

---

### Step 5: Managing Positions & Orders

Check active exposure and past order audit trails:

```bash
# View open positions and unrealized P&L
trading-platform position list

# View order status and execution fills
trading-platform order list

# View overall platform health
trading-platform status
```

---

## 4. Broker Setup

### A. Mock Broker (Default & Backtest)

No API keys required. Operates offline with realistic simulated slippage, commissions, and synthetic data for Indian equities, indices, and MCX commodities.

### B. Angel One SmartAPI (Live / Paper Mode)

1. Register at [smartapi.angelbroking.com](https://smartapi.angelbroking.com) and create an app.

2. Obtain `api_key`, `client_id`, `password`, and your `totp_secret`.

3. Configure in `platform_config.yaml`:

   ```yaml
   broker:
     active: "angel_one"
     angel_one:
       api_key: "YOUR_API_KEY"
       client_id: "YOUR_CLIENT_ID"
       password: "YOUR_PASSWORD"
       totp_secret: "YOUR_TOTP_SECRET"
   ```

### C. ICICI Breeze

1. Register at [icicidirect.com/api](https://api.icicidirect.com).

2. Obtain `api_key`, `user_id`, `password`, and login daily for the `session_key`.

3. Configure in `platform_config.yaml`:

   ```yaml
   broker:
     active: "icici"
     icici:
       api_key: "YOUR_API_KEY"
       user_id: "YOUR_USER_ID"
       password: "YOUR_PASSWORD"
       session_key: "SESSION_KEY"
   ```

---

## 5. Log Files, Data Storage & Audit Trails

- **Snapshots & Journals:** Stored in `/Users/apple/work/automated-engines/data/`

  - `events.jsonl`: Immutable chronological record of every order submission, fill, cancel, and position change.
  - `state_snapshot.json`: Periodic snapshot for recovery after engine crash.
  - `data/backtests/`: Saved backtest results (`.pkl`).

- **Platform Logs:** Formatted with timestamp, component name, log level, and message:

  ```text
  2026-09-16 08:44:41 | backtest.rsi | INFO | Backtest completed: 68 trades, Return: 646.07%
  ```

---

## 6. Troubleshooting & FAQ

**Q1: What does** `Insufficient capital for NSE:NIFTY` **mean in backtest?**\
The strategy tried to take a position where `price * quantity + slippage + commission` exceeded remaining capital. Increase initial capital via `--capital 500000` or decrease strategy quantity in params (`--params '{"quantity": 1}'`).

**Q2: Can I backtest my own custom strategy?**\
Yes. Inherit from `StrategyBase` in `/Users/apple/work/automated-engines/strategies/`, register it with `@StrategyRegistry.register("my_strategy")`, implement `on_candle` and `on_tick`, and run it immediately using `./run_strategy.sh backtest my_strategy`.