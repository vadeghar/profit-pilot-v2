# Broker Credentials Configuration

## Overview

The trading platform **DOES use `.env`** for storing broker credentials. The file is located at:

```
/Users/apple/work/automated-engines/.env (project root)
```

## Current Status

### ✅ Angel One SmartAPI - WORKING
- **Status:** Authenticated successfully
- **Client ID:** S948479
- **Positions Found:** 1 position (NFO:NIFTY22SEP2623150PE)
- **Configuration Source:** `.env`

### ✅ ICICI Breeze - Wired (lazy-loaded)
- **Status:** Import conflict **resolved** — the platform config package is now `platform_config/` (was `config/`), so it no longer shadows the `config` module used internally by the `breeze_connect` SDK
- **Behaviour:** The SDK is imported lazily, only when an ICICI connection is actually requested. If it cannot load, the rest of the platform is unaffected
- **Requirement:** A valid `BREEZE_SESSION_TOKEN` (see `tools/breeze/breeze_auto_login.py`)
- **Note:** The SDK downloads its security master over HTTPS during import, so internet access and valid CA certificates are required

### ✅ Mock Broker - WORKING
- **Status:** Fully operational
- **No credentials needed:** Works out of the box for testing and backtesting

## Credentials in .env

The `.env` file contains the following broker credentials:

```bash
# ICICI Breeze
BREEZE_API_KEY=...
BREEZE_API_SECRET=...
BREEZE_USER_ID=...
BREEZE_PASSWORD=...
BREEZE_SESSION_TOKEN=...

# Angel One SmartAPI
ANGEL_API_KEY=VdyRYhsR
ANGEL_CLIENT_CODE=S948479
ANGEL_PASSWORD_OR_MPIN=...
ANGEL_TOTP_SECRET=...

# Telegram (for notifications)
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USERS=...
TELEGRAM_HOME_CHANNEL=...
```

## How the Platform Loads Credentials

The platform loads credentials from `.env` in test scripts like this:

```python
def load_env(path: str = None) -> dict:
    """Load environment variables from .env"""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    
    env = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip()
    return env
```

## Using Brokers in Your Code

### Option 1: Use Mock Broker (Default - No Setup)

The platform defaults to Mock broker for backtesting. No configuration needed.

```bash
./run_strategy.sh backtest ema_crossover
```

### Option 2: Use Angel One (Live/Paper Trading)

Angel One credentials are already configured in `.env` and working.

To use Angel One in your strategies or tests:

```python
from brokers.angel_one import AngelOneBroker

def load_env():
    env = {}
    with open('.env') as f:
        for line in f:
            if line.strip() and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
    return env

env = load_env()
config = {
    'api_key': env.get('ANGEL_API_KEY'),
    'client_id': env.get('ANGEL_CLIENT_CODE'),
    'password': env.get('ANGEL_PASSWORD_OR_MPIN'),
    'totp_secret': env.get('ANGEL_TOTP_SECRET')
}

broker = AngelOneBroker(config)
broker.authenticate()

# Get live positions
positions = broker.get_positions()
print(f"Positions: {len(positions)}")
```

### Option 3: Configure via platform_config.yaml

You can also configure brokers using the YAML config file:

```yaml
# platform_config/platform_config.yaml
broker:
  active: "angel_one"  # or "mock" or "icici"
  angel_one:
    api_key: "VdyRYhsR"
    client_id: "S948479"
    password: "YOUR_PASSWORD"
    totp_secret: "YOUR_TOTP_SECRET"
```

## Testing Broker Connections

Test if your broker credentials work:

```bash
cd /Users/apple/work/automated-engines/trading-platform
source .venv/bin/activate

# Test with the new script
python3 tests/test_broker_credentials.py
```

**Expected Output:**
```
✓ Loading credentials from: ..env
✓ Loaded 16 environment variables

TESTING ANGEL ONE (SMARTAPI)
API Key: VdyRYhsR
Client ID: S948479

1. Authenticating...
   Status: ✅ SUCCESS

2. Fetching positions...
   Found 1 positions
   Sample: NFO:NIFTY22SEP2623150PE
```

## Security Notes

⚠️ **Important:** The `.env` file contains sensitive credentials:
- Keep it out of version control (should be in `.gitignore`)
- Never share or commit this file
- Rotate credentials regularly
- Use environment-specific credentials (dev vs prod)

## Recommendation

For **backtesting and testing**, continue using the **Mock broker** (no credentials needed).

For **live/paper trading**, use **Angel One** which is already configured and verified working.

## Files Created

1. **`test_broker_credentials.py`** - New script to test broker connections
2. **`BROKER_CREDENTIALS.md`** - This documentation (you're reading it)

## Quick Reference

```bash
# Backtest with Mock broker (default)
./run_strategy.sh backtest ema_crossover

# Test broker credentials
python3 tests/test_broker_credentials.py

# Use Angel One for forward testing
# (See test_forward_test.py for examples)
```

---

**Last Updated:** September 17, 2026  
**Angel One Status:** ✅ Verified Working  
**ICICI Breeze Status:** ✅ Wired (import conflict resolved; lazy-loaded)  
**Mock Broker Status:** ✅ Fully Operational
