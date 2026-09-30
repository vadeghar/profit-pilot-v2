#!/usr/bin/env bash
# One-shot, idempotent install of the trading dashboard on a Linux server.
#
#   curl -fsSL https://raw.githubusercontent.com/vadeghar/profit-pilot-v2/feature/automation-engines/deploy/linux/bootstrap.sh | bash
#   # or, from a checkout:  bash deploy/linux/bootstrap.sh
#
# Env overrides: APP_DIR (default ~/automation_engines), BRANCH (feature/automation-engines),
# PORT (9090), REPO (https://github.com/vadeghar/profit-pilot-v2.git), SKIP_SYSTEMD=1.
# Needs sudo for OS packages and the systemd unit. Secrets are NOT handled here:
# copy your .env to $APP_DIR/.env (chmod 600) - see deploy/linux/README.md.
set -euo pipefail

REPO="${REPO:-https://github.com/vadeghar/profit-pilot-v2.git}"
BRANCH="${BRANCH:-feature/automation-engines}"
APP_DIR="${APP_DIR:-$HOME/automation_engines}"
PORT="${PORT:-9090}"
PY="${PY:-3.12}"
say() { printf '\n==> %s\n' "$*"; }

say "OS packages (git, curl, build tools)"
if command -v apt-get >/dev/null; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl ca-certificates build-essential
elif command -v dnf >/dev/null; then
  sudo dnf install -y -q git curl ca-certificates gcc gcc-c++ make
else
  echo "Unsupported distro: install git, curl and a C compiler, then re-run." >&2; exit 1
fi

say "uv (manages Python $PY independently of the distro's python)"
if ! command -v uv >/dev/null && [ ! -x "$HOME/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

say "Code: $REPO ($BRANCH) -> $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
  git -C "$APP_DIR" checkout --quiet "$BRANCH"
  git -C "$APP_DIR" pull --ff-only --quiet origin "$BRANCH"
else
  git clone --quiet --branch "$BRANCH" "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"

say "Python $PY virtualenv + dependencies"
[ -x .venv/bin/python ] || uv venv --quiet --python "$PY" .venv
uv pip install --quiet --python .venv/bin/python -r requirements.txt

say "Data directories"
mkdir -p data/ticks data/forward_test data/cache data/historical logs

if [ ! -f .env ]; then
  echo
  echo "!! $APP_DIR/.env is missing. Copy it from your machine (scp .env user@host:$APP_DIR/.env),"
  echo "   chmod 600 it, then re-run this script. Stopping before the service is installed."
  exit 2
fi
chmod 600 .env

if [ "${SKIP_SYSTEMD:-0}" != "1" ]; then
  say "systemd service automation-engines (127.0.0.1:$PORT)"
  fill() { sed -e "s#__USER__#$(id -un)#g" -e "s#__APP_DIR__#$APP_DIR#g" -e "s#__PORT__#$PORT#g" "$1"; }
  fill deploy/linux/automation-engines.service | sudo tee /etc/systemd/system/automation-engines.service >/dev/null
  fill deploy/linux/breeze-login.service | sudo tee /etc/systemd/system/breeze-login.service >/dev/null
  sudo cp deploy/linux/breeze-login.timer /etc/systemd/system/breeze-login.timer
  sudo systemctl daemon-reload
  sudo systemctl enable --quiet automation-engines
  sudo systemctl restart automation-engines

  say "Health check"
  for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:$PORT/api/catalog" >/dev/null 2>&1; then
      echo "OK - dashboard answering on 127.0.0.1:$PORT"
      curl -fsS "http://127.0.0.1:$PORT/api/ticks/status" | head -c 400; echo
      exit 0
    fi
    sleep 2
  done
  echo "Service did not answer within 60 s - check: journalctl -u automation-engines -n 100" >&2
  exit 3
fi
