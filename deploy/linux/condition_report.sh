#!/usr/bin/env bash
# Daily scalper condition report, run from cron at 15:40 IST (10:10 UTC) Mon-Fri, after the recorder
# stops at 15:32. Replays the day's recorded ticks through every scalper and writes
# logs/condition_report_<date>.md. Read-only: it does not touch the running service or its paper state.
#   DAY=2026-10-01 deploy/linux/condition_report.sh    # re-run a past day
set -euo pipefail
APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
export TZ=Asia/Kolkata
cd "$APP_DIR"
DAY="${DAY:-$(date +%F)}"
mkdir -p logs

if ! ls data/ticks/*/"$DAY"/ticks.csv* >/dev/null 2>&1; then
  echo "$(date '+%F %T') no recorded ticks for $DAY - skipping condition report"
  exit 0
fi

nice -n 10 .venv/bin/python -m tools.scalping.condition_report --date "$DAY" --out "logs/condition_report_$DAY.md" >/dev/null
echo "$(date '+%F %T') wrote logs/condition_report_$DAY.md"
