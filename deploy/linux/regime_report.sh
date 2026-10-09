#!/usr/bin/env bash
# Daily market-regime panel, run from cron at 15:55 IST (10:25 UTC) Mon-Fri, after the recorder stops
# (15:42) and the condition report (15:50). Appends one row to logs/regime_panel.csv - the labelled
# trend/chop dataset a future S3 activation gate will be built on. Read-only on paper state and ticks.
#   DAY=2026-10-08 deploy/linux/regime_report.sh    # re-run a past day
set -euo pipefail
APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
export TZ=Asia/Kolkata
cd "$APP_DIR"
DAY="${DAY:-$(date +%F)}"

if ! ls data/ticks/*/"$DAY"/ticks.csv* >/dev/null 2>&1; then
  echo "$(date '+%F %T') no recorded ticks for $DAY - skipping regime report"
  exit 0
fi

nice -n 10 .venv/bin/python -m scalp_strategies.tools.regime_report --date "$DAY"
echo "$(date '+%F %T') appended $DAY to logs/regime_panel.csv"
