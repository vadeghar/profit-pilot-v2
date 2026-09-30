# Running on a Linux server

One systemd service (`automation-engines`) runs the dashboard, the tick recorder and every paper
session. It listens on **127.0.0.1:9090 only** - nothing is exposed on the public IP. You reach it
privately through Tailscale (recommended) or an SSH tunnel.

## 1. Install

```bash
ssh user@server
curl -fsSL https://raw.githubusercontent.com/vadeghar/profit-pilot-v2/feature/automation-engines/deploy/linux/bootstrap.sh | bash
```

The first run stops before installing the service because `.env` is missing. From your Windows
machine copy the secrets over SSH (never through git - the repo is public):

```bash
scp D:\Work\automation_engines\.env user@server:~/automation_engines/.env
```

then run the bootstrap again. It installs Python 3.12 via `uv`, the dependencies, the systemd
units, starts the service and health-checks `/api/catalog`.

Optional: carry over recorded ticks and paper-trading state:

```bash
scp -r D:\Work\automation_engines\data\ticks D:\Work\automation_engines\data\forward_test user@server:~/automation_engines/data/
```

## 2. Reach the dashboard privately

**Tailscale (recommended):** install it on the server and on your laptop/phone, log both into the
same tailnet, then publish the local port inside the tailnet only:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale serve --bg 9090
```

Open `https://<server-name>.<tailnet>.ts.net` from any of your devices (HTTPS certificate included).

**SSH tunnel (no extra software):** `ssh -N -L 9090:127.0.0.1:9090 user@server`, then open
`http://localhost:9090` locally.

Firewall: keep only SSH open (`sudo ufw allow OpenSSH && sudo ufw enable`); Tailscale needs no
inbound port.

## 3. Operate

| Task | Command |
|---|---|
| Status / logs | `systemctl status automation-engines`, `journalctl -u automation-engines -f` |
| Restart | `sudo systemctl restart automation-engines` |
| Update to the latest pushed code | `bash ~/automation_engines/deploy/linux/update.sh` (refuses during market hours unless `FORCE=1`) |
| Recorder status | `curl -s localhost:9090/api/ticks/status` |
| Import a past day | `cd ~/automation_engines && .venv/bin/python -m tools.scalping.import_breeze_1s --date 2026-09-29` |

The service restarts on crash and on reboot. The five scalping paper sessions persist their balance
and trades in `data/forward_test/scalping/` and **resume by themselves** after a restart (only a
Stop from the dashboard ends them). Older strategies' paper sessions must be restarted from their cards.

## 4. Broker sessions

- **Angel One** logs in by itself (API key, client code, MPIN and TOTP secret from `.env`).
  If Angel requires a registered static IP for your API key, register the server's public IP in the
  SmartAPI dashboard.
- **ICICI Breeze** needs a fresh session token every day. Either paste it into `.env` as before, or
  enable the headless auto-login (asks for the OTP on Telegram; needs `BREEZE_*` and `TELEGRAM_*`
  in `.env`):

  ```bash
  cd ~/automation_engines && .venv/bin/python -m playwright install --with-deps chromium
  sudo systemctl enable --now breeze-login.timer      # runs Mon-Fri 08:40 IST
  ```

## 5. Server clock

The unit sets `TZ=Asia/Kolkata` for the app; all trading logic is IST-aware regardless. Keep NTP on
(`timedatectl` should show "System clock synchronized: yes") - the recorder's 09:12 start and the
15:00 square-off depend on it.
