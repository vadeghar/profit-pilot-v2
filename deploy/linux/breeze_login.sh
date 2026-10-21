#!/usr/bin/env bash
# Daily ICICI Breeze session refresh, run from cron at 08:25 IST Mon-Fri with retries at 09:00 and 09:45.
#  * skips NSE holidays (no pointless OTP prompts);
#  * skips when the session was already refreshed today: the source of truth is the stamp the login
#    writes above the token in .env ("# BREEZE_SESSION_TOKEN last updated: YYYY-MM-DD HH:MM:SS IST"),
#    so the 09:00 / 09:45 runs only prompt for an OTP if the earlier one was missed. FORCE=1 overrides;
#  * runs the headless auto-login (ICICI sends an OTP; the bot asks for it on Telegram - reply within ~90 s);
#  * verifies the new BREEZE_SESSION_TOKEN against the Breeze API;
#  * optionally copies the token into other .env files that use the SAME Breeze account
#    (SYNC_ENV_FILES="/path/a/.env /path/b/.env"), because a new login invalidates the old session.
set -euo pipefail
APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
export TZ=Asia/Kolkata
cd "$APP_DIR"
PY=.venv/bin/python

if ! $PY -c "from datetime import date; from market_data.trading_days import TradingCalendar as C; import sys; sys.exit(0 if C().is_trading_day(date.today()) else 1)"; then
  echo "$(date '+%F %T') not a trading day - skipping Breeze login"
  exit 0
fi

stamp=$(sed -n 's/^# BREEZE_SESSION_TOKEN last updated: //p' .env | tail -1)
if [ "${FORCE:-0}" != 1 ] && [ "${stamp:0:10}" = "$(date +%F)" ]; then
  echo "$(date '+%F %T') Breeze session already refreshed today ($stamp) - skipping"
  exit 0
fi

$PY tools/breeze/breeze_auto_login.py

$PY - <<'EOF'
from market_data.breeze_data_provider import BreezeHistoricalDataProvider
p = BreezeHistoricalDataProvider(persist_cache=False)
p.ensure_authenticated()
print("Breeze session verified")
EOF

if [ -n "${SYNC_ENV_FILES:-}" ]; then
  token=$(grep -E '^BREEZE_SESSION_TOKEN=' .env | tail -1 | cut -d= -f2-)
  for f in $SYNC_ENV_FILES; do
    [ -f "$f" ] || { echo "skip missing $f"; continue; }
    if grep -qE '^BREEZE_SESSION_TOKEN=' "$f"; then
      sed -i "s#^BREEZE_SESSION_TOKEN=.*#BREEZE_SESSION_TOKEN=${token}#" "$f"
    else
      printf '\nBREEZE_SESSION_TOKEN=%s\n' "$token" >> "$f"
    fi
    echo "synced session token to $f"
  done
fi
