"""Real Breeze backtests for the two NIFTY option strategies.

Runs the platform's own runners unchanged (Four Indicator System, NIFTY No
Brainer) with zero slippage so that slippage/spread can be attributed
separately in the report. Raw runner output lands in
data/strategy_audit/options/<strategy>.json.

    python -m tools.strategy_audit.run_options [four_indicator|nifty_no_brainer ...]
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from tools.strategy_audit.common import AUDIT_DATA_DIR, ENV_FILE
from backtest.four_indicator_backtest import run_four_indicator_backtest
from backtest.nifty_no_brainer_runner import run_backtest as run_nifty_no_brainer
from market_data.breeze_data_provider import BreezeHistoricalDataProvider

OUT_DIR = AUDIT_DATA_DIR / "options"
START = date(2025, 1, 1)
END = date(2026, 9, 29)
CAPITAL = 100_000.0
# Lot-size regimes from nifty_expiries.json (lot_size_effective).
FOUR_INDICATOR_SEGMENTS = [(date(2025, 1, 1), date(2025, 12, 31), 75), (date(2026, 1, 1), END, 65)]


def _provider() -> BreezeHistoricalDataProvider:
    p = BreezeHistoricalDataProvider(env_path=str(ENV_FILE), persist_cache=False)
    p.verify_once = True
    p.ensure_authenticated()
    return p


def run_four_indicator(provider) -> dict:
    balance = CAPITAL
    trades, equity, segments = [], [], []
    for seg_start, seg_end, lot in FOUR_INDICATOR_SEGMENTS:
        rep = run_four_indicator_backtest(provider, seg_start, seg_end, capital=balance, lot_size=lot,
                                          progress=lambda t: print(f"  4IND {t.entry_time} {t.side} {t.strike} pnl={t.pnl:,.0f}", flush=True))
        balance = rep["summary"]["final_balance"]
        trades += rep["trades"]
        equity += rep["equity_curve"]
        segments.append({"start": seg_start.isoformat(), "end": seg_end.isoformat(), "lot_size": lot,
                         "summary": rep["summary"], "data_source": rep["data_source"], "params": rep["params"]})
    return {"strategy": "four_indicator_system", "start": START.isoformat(), "end": END.isoformat(),
            "initial_capital": CAPITAL, "final_balance": balance, "segments": segments,
            "trades": trades, "equity_curve": equity}


def run_no_brainer(provider) -> dict:
    rep = run_nifty_no_brainer(provider, START, END, capital=CAPITAL, slippage_points=0.0,
                               expiry_path="nifty_expiries.json",
                               progress=lambda t: print(f"  NNB {t.month} {t.status} {t.decision} pnl={t.pnl_rupees}", flush=True))
    return {"strategy": "nifty_no_brainer", **rep}


def main(argv: list[str]) -> None:
    wanted = argv or ["four_indicator", "nifty_no_brainer"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    provider = _provider()
    jobs = {"four_indicator": run_four_indicator, "nifty_no_brainer": run_no_brainer}
    for name in wanted:
        print(f"== {name} {START}..{END}", flush=True)
        result = jobs[name](provider)
        path = OUT_DIR / f"{name}.json"
        path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"== wrote {path}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
