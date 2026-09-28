#!/usr/bin/env python3
"""Backtest NIFTY No Brainer straight from Breeze (no local candle cache).

    python tools/run_nifty_no_brainer_backtest.py                       # 2026-01-01 -> today
    python tools/run_nifty_no_brainer_backtest.py --start 2026-01-01 --end 2026-08-31 --hold 19

Needs a live BREEZE_SESSION_TOKEN (python tools/breeze/breeze_auto_login.py --visible).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest.nifty_no_brainer_runner import MonthTrade, run_backtest, save_report  # noqa: E402
from backtest.charges import ChargeConfig  # noqa: E402
from brokers.breeze_margin import margin_settings  # noqa: E402
from market_data.breeze_data_provider import BreezeHistoricalDataProvider  # noqa: E402


def _show(t: MonthTrade) -> None:
    pnl = f"{t.pnl_rupees:>10,.0f} ({t.pnl_pct_margin:+.2%})" if t.pnl_rupees is not None else " " * 22
    print(f"{t.month} {t.status:<8} {t.decision:<10} entry={t.entry_date} exp={t.expiry} "
          f"strikes={list(t.strikes.values())} lots={t.lots} deployed={t.deployed_capital and round(t.deployed_capital)} "
          f"exit={t.exit_reason or '-':<9} net={pnl} charges={t.charges_total and round(t.charges_total)} "
          f"bal={t.balance_after and round(t.balance_after)} {'; '.join(t.flags)[:60]}",
          flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=date.fromisoformat, default=date(2026, 1, 1))
    ap.add_argument("--end", type=date.fromisoformat, default=date.today())
    ap.add_argument("--hold", type=int, default=19, choices=(18, 19),
                    help="max hold days (default 19); target/stop exit earlier")
    ap.add_argument("--timeframe", default="5m", help="lifecycle bar size (1m/5m/15m/30m)")
    ap.add_argument("--slippage", type=float, default=0.0, help="points per leg, each side")
    ap.add_argument("--margin", default="calibrated",
                    help="calibrated (Breeze margin_calculator, default) | span (own SPAN from NSE files) | proxy | rupees per set")
    ap.add_argument("--capital", type=float, default=100_000.0, help="initial capital; lots compound from balance")
    ap.add_argument("--no-compound", action="store_true", help="size every trade from the initial capital")
    ap.add_argument("--brokerage", type=float, default=20.0, help="Rs per executed order (your plan)")
    ap.add_argument("--margin-multiplier", type=float, default=1.0)
    ap.add_argument("--costs", type=float, default=0.0, help="flat rupees per round trip")
    ap.add_argument("--out", default=None, help="report path (default logs/nifty_no_brainer_<start>_<end>.json)")
    args = ap.parse_args()

    provider = BreezeHistoricalDataProvider(persist_cache=False)
    provider.verify_once = True
    provider.ensure_authenticated()  # fail fast on an expired session
    margin = margin_settings(provider, args.margin)
    print("MARGIN", margin["margin_method"], flush=True)
    report = run_backtest(provider, args.start, args.end, max_hold_days=args.hold,
                          lifecycle_timeframe=args.timeframe, slippage_points=args.slippage,
                          margin_multiplier=args.margin_multiplier, costs_per_trade=args.costs, **margin,
                          capital=args.capital, compound=not args.no_compound,
                          charges=ChargeConfig(brokerage_per_order=args.brokerage),
                          progress=_show)
    out = save_report(report, args.out or ROOT / "logs" / f"nifty_no_brainer_{args.start}_{args.end}.json")
    print("\nSUMMARY", report["summary"], "\nDATA", report["data_source"], f"\nsaved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
