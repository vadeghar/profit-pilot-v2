"""Daily paper-trading summary for the scalpers, sent to Telegram.

Reads each scalper's persisted paper state (data/forward_test/scalping/<id>.json:
balance + full trade log) and reports, per scalper, the day's trades, wins and
losses, net P&L and the running balance, then the combined total. Read-only.

    python -m tools.scalping.daily_summary [--date 2026-10-01] [--dry-run]

deploy/linux/scalp_summary.sh runs it from cron after the last square-off (15:20).
Telegram credentials come from the environment or .env (TELEGRAM_BOT_TOKEN,
TELEGRAM_HOME_CHANNEL or TELEGRAM_CHAT_ID).
"""
from __future__ import annotations

import argparse
import html
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import requests

from execution.scalping_paper_trader import persisted_status
from strategies.scalping import SCALP_STRATEGIES
from utils.timezone import now_ist

ROOT = Path(__file__).resolve().parents[2]


def _env(names: tuple[str, ...]) -> str:
    values = dict(os.environ)
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                values.setdefault(k.strip(), v.strip().strip("'\""))
    return next((values[n] for n in names if values.get(n)), "")


def _rs(x: float, sign: bool = False) -> str:
    return f"{'+' if sign and x > 0 else '-' if x < 0 else ''}Rs {abs(x):,.0f}"


def build(day: date, state_dir: Optional[Path] = None) -> str:
    """HTML-formatted Telegram message for ``day``."""
    d = day.isoformat()
    lines = [f"<b>Scalper paper summary - {day:%a %d %b %Y}</b>", ""]
    tot_net = tot_bal = tot_cap = 0.0
    tot_trades = tot_wins = 0
    for sid, cls in SCALP_STRATEGIES.items():
        st = persisted_status(sid, state_dir)
        name = html.escape(cls.name)
        if not st:
            lines += [f"<b>{name}</b>", "  never started", ""]
            continue
        trades = [t for t in st.get("trades", []) if t.get("date") == d]
        net = sum(t["net"] for t in trades)
        wins = sum(1 for t in trades if t["net"] > 0)
        cap, bal = st.get("capital", 0.0), st.get("balance", 0.0)
        tot_net, tot_bal, tot_cap = tot_net + net, tot_bal + bal, tot_cap + cap
        tot_trades, tot_wins = tot_trades + len(trades), tot_wins + wins
        stopped = "" if st.get("running") else "  (STOPPED)"
        lines.append(f"<b>{name}</b>{stopped}")
        if trades:
            lines.append(f"  {len(trades)} trade{'s' if len(trades) > 1 else ''}: {wins}W / {len(trades) - wins}L, "
                         f"day <b>{_rs(net, True)}</b>")
            for t in trades:
                lines.append(f"  {t['entry_time'][11:16]} {html.escape(t['symbol'][-7:])} x{t['lots']} "
                             f"{t['entry']:g} -&gt; {t['exit']:g} {html.escape(t['reason'])} {_rs(t['net'], True)}")
        else:
            lines.append("  no trades")
        since = f" ({(bal / cap - 1):+.1%} since start)" if cap else ""
        lines += [f"  balance {_rs(bal)}{since}", ""]
    lines.append(f"<b>All scalpers:</b> {tot_trades} trades ({tot_wins}W / {tot_trades - tot_wins}L), "
                 f"day <b>{_rs(tot_net, True)}</b>")
    if tot_cap:
        lines.append(f"Combined balance {_rs(tot_bal)} of {_rs(tot_cap)} started ({(tot_bal / tot_cap - 1):+.1%})")
    lines.append("Paper trades only - no real orders.")
    return "\n".join(lines)


def send(text: str) -> bool:
    token = _env(("TELEGRAM_BOT_TOKEN",))
    chat = _env(("TELEGRAM_HOME_CHANNEL", "TELEGRAM_CHAT_ID"))
    if not token or not chat:
        print("Telegram is not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_HOME_CHANNEL)", file=sys.stderr)
        return False
    res = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat, "text": text, "parse_mode": "HTML"}, timeout=15)
    if res.status_code == 200 and res.json().get("ok"):
        return True
    print(f"Telegram send failed: {res.status_code} {res.text[:200]}", file=sys.stderr)
    return False


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="trading day (default: today, IST)")
    ap.add_argument("--dry-run", action="store_true", help="print the message instead of sending it")
    a = ap.parse_args(argv)
    text = build(date.fromisoformat(a.date) if a.date else now_ist().date())
    if a.dry_run:
        print(text)
        return 0
    return 0 if send(text) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
