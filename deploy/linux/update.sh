#!/usr/bin/env bash
# Pull the latest code, refresh dependencies, restart, health-check.
#   bash ~/automation_engines/deploy/linux/update.sh
# Refuses to restart during market hours unless FORCE=1 (a restart drops the live tick feed
# for ~20 s and closes open paper positions at the last price).
set -euo pipefail
APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
BRANCH="${BRANCH:-feature/automation-engines}"
PORT="${PORT:-9090}"
export PATH="$HOME/.local/bin:$PATH"
cd "$APP_DIR"

now=$(TZ=Asia/Kolkata date +%H%M); dow=$(TZ=Asia/Kolkata date +%u)
if [ "${FORCE:-0}" != "1" ] && [ "$dow" -le 5 ] && [ "$now" -ge 0910 ] && [ "$now" -le 1535 ]; then
  echo "Market hours (IST $now): not restarting. Re-run after 15:35 or with FORCE=1." >&2; exit 1
fi

git fetch --quiet origin "$BRANCH"
git pull --ff-only --quiet origin "$BRANCH"
uv pip install --quiet --python .venv/bin/python -r requirements.txt
sudo systemctl restart automation-engines
for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$PORT/api/catalog" >/dev/null 2>&1 && { echo "updated to $(git log --oneline -1)"; exit 0; }
  sleep 2
done
echo "not healthy - journalctl -u automation-engines -n 100" >&2; exit 3
