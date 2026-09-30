"""Load raw audit runs into sim.Account books + metrics (shared by the report)."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.strategy_audit import costs, sim
from tools.strategy_audit.common import AUDIT_DATA_DIR

OHLC_DIR = AUDIT_DATA_DIR / "ohlc"
OPT_DIR = AUDIT_DATA_DIR / "options"
TF_ORDER = ["15m", "1h", "4h", "1d", "1w"]
HURDLE_CAGR = 0.06  # a strategy must beat a bank fixed deposit to be worth its risk


def _acct(part: dict) -> sim.Account:
    a = sim.Account(part["capital"], lambda _i: "eq")
    a.trades = [sim.TradeRec(**t) for t in part["trades"]]
    a.equity = [(datetime.fromisoformat(ts), v) for ts, v in part["equity"]]
    a.cash = a.capital + sum(t.net_pnl for t in a.trades)
    return a


def load_ohlc(strategy: str, tf: str) -> dict[str, Any] | None:
    path = OHLC_DIR / f"{strategy}_{tf}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    ws, we = (datetime.fromisoformat(x) for x in raw["window"])
    sleeves = {p["symbol"]: _acct(p) for p in raw["sleeves"]}
    book = sim.merge(list(sleeves.values())) if len(sleeves) > 1 else next(iter(sleeves.values()))
    return {"strategy": strategy, "tf": tf, "window": (ws, we), "book": book,
            "metrics": sim.metrics(book, ws, we),
            "per_symbol": {s: sim.metrics(a, ws, we) for s, a in sorted(sleeves.items())},
            "coverage": {p["symbol"]: p.get("coverage") for p in raw["sleeves"]}}


def _option_book(trades: list[sim.TradeRec], capital: float) -> sim.Account:
    a = sim.Account(capital, lambda _i: "opt")
    a.trades = sorted(trades, key=lambda t: t.exit_time)
    bal, eq = capital, []
    for t in a.trades:
        bal += t.net_pnl
        eq.append((datetime.fromisoformat(t.exit_time), bal))
    a.equity, a.cash = eq, bal
    return a


def load_four_indicator() -> dict[str, Any] | None:
    path = OPT_DIR / "four_indicator.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    recs = []
    for t in raw["trades"]:
        if t["status"] != "CLOSED":
            continue
        turnover = (t["entry_premium"] + t["exit_premium"]) * t["quantity"]
        spread = costs.OPTION_HALF_SPREAD_PCT * turnover
        slip = costs.OPTION_SLIPPAGE_PCT * turnover
        et, xt = datetime.fromisoformat(t["entry_time"]), datetime.fromisoformat(t["exit_time"])
        recs.append(sim.TradeRec(
            instrument=f"NIFTY {t['strike']} {t['side']} {t['expiry']}", side=f"LONG {t['side']}",
            qty=t["quantity"], entry_time=t["entry_time"], entry_price=t["entry_premium"],
            exit_time=t["exit_time"], exit_price=t["exit_premium"], entry_reason=f"{t['side']}_entry",
            exit_reason=t["exit_reason"], notional=t["entry_premium"] * t["quantity"],
            gross_pnl=t["gross_pnl"], commission=t["charges_total"], spread=spread, slippage=slip,
            net_pnl=t["gross_pnl"] - t["charges_total"] - spread - slip,
            holding_days=(xt - et).total_seconds() / 86400, intraday=et.date() == xt.date()))
    book = _option_book(recs, raw["initial_capital"])
    ws = datetime.fromisoformat(raw["start"] + "T00:00:00+05:30")
    we = datetime.fromisoformat(raw["end"] + "T23:59:00+05:30")
    return {"strategy": "four_indicator_system", "tf": "5m", "window": (ws, we), "book": book,
            "metrics": sim.metrics(book, ws, we), "raw": raw,
            "open_at_end": [t for t in raw["trades"] if t["status"] != "CLOSED"]}


def load_no_brainer() -> dict[str, Any] | None:
    path = OPT_DIR / "nifty_no_brainer.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    recs = []
    for m in raw["months"]:
        if m["status"] not in ("CLOSED", "OPEN") or m.get("gross_pnl") is None:
            continue
        qty = m["lots"] * m["lot_size"]
        units_per_side = 4 * qty  # near 1x + sell 2x + hedge 1x
        spread = costs.NNB_HALF_SPREAD_POINTS * units_per_side * 2
        slip = costs.NNB_SLIPPAGE_POINTS * units_per_side * 2
        et, xt = datetime.fromisoformat(m["entry_time"]), datetime.fromisoformat(m["exit_time"])
        s = m["strikes"]
        recs.append(sim.TradeRec(
            instrument=f"NIFTY {s['near_buy']}/{s['sell']}x2/{s['hedge']} CE exp {m['expiry']}",
            side="1:-2:1 CE", qty=m["lots"], entry_time=m["entry_time"],
            entry_price=m.get("net_premium_rupees") or 0.0, exit_time=m["exit_time"], exit_price=0.0,
            entry_reason=m["decision"], exit_reason=m["exit_reason"] + (" (open, MTM)" if m["status"] == "OPEN" else ""),
            notional=m["margin"], gross_pnl=m["gross_pnl"], commission=m["charges_total"], spread=spread,
            slippage=slip, net_pnl=m["gross_pnl"] - m["charges_total"] - spread - slip,
            holding_days=(xt - et).total_seconds() / 86400, intraday=False))
    book = _option_book(recs, raw["params"]["capital"])
    ws = datetime.fromisoformat(raw["params"]["start"] + "T00:00:00+05:30")
    we = datetime.fromisoformat(raw["params"]["end"] + "T23:59:00+05:30")
    return {"strategy": "nifty_no_brainer", "tf": "1m/5m", "window": (ws, we), "book": book,
            "metrics": sim.metrics(book, ws, we), "raw": raw,
            "skipped": [m for m in raw["months"] if m["status"] in ("SKIPPED", "ERROR")]}


# ------------------------------------------------------------------ verdicts ---
def verdict(m: dict[str, Any], *, min_trades: int = 30, tf_selected: bool = False) -> tuple[str, str]:
    """KEEP / WATCH / DEPRECATE with the reason, from net-of-cost metrics."""
    pf, n = m["profit_factor"], m["trades"]
    robust = m["net_pnl_2x_friction"] > 0
    if n == 0:
        return "DEPRECATE", "no trades in the test window"
    if m["net_pnl"] <= 0 or pf < 1.0:
        return "DEPRECATE", f"loses money after costs (net PF {fmt_pf(pf)}, net {inr(m['net_pnl'])})"
    if m["cagr"] < HURDLE_CAGR:
        return "DEPRECATE", (f"profitable but earns only {pct(m['cagr'])} a year net - below the "
                             f"{pct(HURDLE_CAGR, 0)} risk-free hurdle (bank FD)")
    need_pf = 1.5 if tf_selected else 1.3
    if pf >= need_pf and n >= min_trades and m["max_dd_pct"] <= 0.25 and robust:
        return "KEEP", f"net PF {fmt_pf(pf)} over {n} trades, max DD {pct(m['max_dd_pct'])}, survives 2x friction"
    why = []
    if pf < need_pf:
        why.append(f"thin edge (net PF {fmt_pf(pf)} < {need_pf})")
    if n < min_trades:
        why.append(f"small sample ({n} trades)")
    if m["max_dd_pct"] > 0.25:
        why.append(f"deep drawdown ({pct(m['max_dd_pct'])})")
    if not robust:
        why.append("turns negative if slippage/spread double")
    return "WATCH", "; ".join(why)


def horizon(m: dict[str, Any]) -> str:
    if m["trades"] and m["intraday_share"] >= 0.9:
        return "Intraday"
    return "Short term" if m["avg_hold_days"] <= 60 else "Long term"


# ------------------------------------------------------------------- format ---
def inr(x: float) -> str:
    sign = "-" if x < 0 else ""
    x = abs(x)
    whole = f"{x:,.0f}"
    s = str(int(round(x)))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}Rs {whole}"


def pct(x: float, d: int = 1) -> str:
    return f"{x * 100:.{d}f}%"


def fmt_pf(x: float) -> str:
    return "inf" if math.isinf(x) else f"{x:.2f}"
