#!/usr/bin/env bash
# Daily scalper paper-trading summary to Telegram, run from cron at 15:25 IST (09:55 UTC) Mon-Fri,
# after the last square-off (15:10). Skips NSE holidays. Read-only: it only reads the paper state files.
#   DAY=2026-10-01 deploy/linux/scalp_summary.sh    # re-send a past day
set -euo pipefail
APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
export TZ=Asia/Kolkata
cd "$APP_DIR"
PY=.venv/bin/python
DAY="${DAY:-$(date +%F)}"

if ! $PY -c "from datetime import date; from market_data.trading_days import TradingCalendar as C; import sys; sys.exit(0 if C().is_trading_day(date.fromisoformat('$DAY')) else 1)"; then
  echo "$(date '+%F %T') $DAY is not a trading day - no scalper summary"
  exit 0
fi

$PY -m tools.scalping.daily_summary --date "$DAY"
echo "$(date '+%F %T') sent scalper summary for $DAY"
