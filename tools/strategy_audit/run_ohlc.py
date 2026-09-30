"""Audit backtests for the OHLC (non-option) strategies.

    python -m tools.strategy_audit.run_ohlc daily      # 1d + 1w, VCP, MCX (yfinance, no broker quota)
    python -m tools.strategy_audit.run_ohlc intraday   # 15m / 1h / 4h (Breeze)

Timeframes: a strategy whose rules fix a timeframe runs on it (VCP, MCX Trend
Rider: daily). Generic strategies whose rules don't (EMA crossover, RSI,
Donchian breakout, Lorentzian ML) run on 15m, 1h, 4h, 1d and 1w.
Each symbol is an independent Rs 1,00,000 sleeve (the platform engine sizes
every entry with all available cash); VCP and MCX run as one shared account
with the strategy's own risk-based sizing.
"""
from __future__ import annotations

import json
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, time
from pathlib import Path

from tools.strategy_audit.common import AUDIT_DATA_DIR  # noqa: F401  (sets ENV_FILE)
from tools.strategy_audit import data as D
from tools.strategy_audit import sim
from utils.timezone import IST

WINDOW_START = datetime(2023, 10, 1, 0, 0, tzinfo=IST)
WINDOW_END = datetime(2026, 9, 29, 23, 59, tzinfo=IST)
CAPITAL = 100_000.0
DAILY_FETCH_START = date(2020, 6, 1)
INTRADAY_FETCH_START = {"15m": date(2023, 8, 1), "1h": date(2022, 9, 1), "4h": date(2022, 9, 1)}
MULTI_TF = ["15m", "1h", "4h", "1d", "1w"]
OUT = AUDIT_DATA_DIR / "ohlc"

GENERIC = {
    "ema_crossover": {"fast_period": 9, "slow_period": 21, "quantity": 1},
    "rsi": {"period": 14, "oversold": 30.0, "overbought": 70.0, "quantity": 1},
    "breakout": {"lookback": 20, "quantity": 1},
    "lorentzian_ml": {"neighbors_count": 8, "max_bars_back": 2000, "feature_count": 5,
                      "use_volatility_filter": True, "use_regime_filter": True, "regime_threshold": -0.1,
                      "use_kernel_filter": True, "use_dynamic_exits": False, "quantity": 1,
                      "min_history_bars": 60},
}


def _bars(symbol: str, tf: str):
    if tf == "1d":
        return D.daily(symbol, DAILY_FETCH_START, WINDOW_END.date())
    if tf == "1w":
        return D.weekly(D.daily(symbol, DAILY_FETCH_START, WINDOW_END.date()))
    return D.intraday(symbol, tf, INTRADAY_FETCH_START[tf], WINDOW_END.date())


def _segment(symbol: str) -> str:
    return "index_fut" if D.instrument(symbol)["type"] == "index" else "eq"


def _dump(acct) -> dict:
    return {"capital": acct.capital, "trades": sim.trades_as_dicts(acct),
            "equity": [(ts.isoformat(), v) for ts, v in acct.equity]}


def sleeve_job(strategy_id: str, tf: str, symbol: str) -> dict:
    from strategies import StrategyRegistry
    params = dict(GENERIC[strategy_id])
    if strategy_id == "lorentzian_ml":
        params.update(timeframe={"1w": "1wk"}.get(tf, tf), ticker=symbol)
    strat = StrategyRegistry.create(strategy_id, f"audit_{strategy_id}", params)
    strat.initialize()
    bars = _bars(symbol, tf)
    is_index = _segment(symbol) == "index_fut"
    acct = sim.run(strat, {symbol: bars}, capital=CAPITAL, window_start=WINDOW_START, window_end=WINDOW_END,
                   segment_of=_segment, sizing="all_in",
                   semantics="target" if strategy_id == "lorentzian_ml" else "engine",
                   fractional=lambda _i: is_index)
    return {"strategy": strategy_id, "tf": tf, "symbol": symbol, "coverage": D.coverage(bars), **_dump(acct)}


def vcp_job() -> dict:
    from strategies.equity_swing_vcp import EquitySwingVCPStrategy
    params = {"capital": CAPITAL, "risk_pct": 0.0125, "stop_pct": 0.07, "volume_breakout_mult": 1.3, "partial_r": 2.0}
    strat = EquitySwingVCPStrategy("audit_vcp", "equity_swing_vcp", params)
    bench = "NSE:NIFTY"
    bars = {bench: D.daily(bench, DAILY_FETCH_START, WINDOW_END.date())}
    for i in D.universe():
        if i["type"] == "equity":
            bars[i["symbol"]] = D.daily(i["symbol"], DAILY_FETCH_START, WINDOW_END.date())
    acct = sim.run(strat, bars, capital=CAPITAL, window_start=WINDOW_START, window_end=WINDOW_END,
                   segment_of=lambda _i: "eq", sizing="signal_cash", long_only=True, feed_only={bench})
    return {"strategy": "equity_swing_vcp", "tf": "1d", "symbol": "PORTFOLIO",
            "coverage": {s: D.coverage(b) for s, b in bars.items()}, **_dump(acct)}


MCX_VARIANTS = {
    # tf label: (capital, use_sma_filter) - "1d" is the catalog configuration.
    "1d": (CAPITAL, False),
    "1d_cap10L": (1_000_000.0, False),
    "1d_cap10L_sma200": (1_000_000.0, True),
}


def mcx_job(variant: str = "1d") -> dict:
    from strategies.mcx_trend_rider import COMMODITY_SPECS, MCXTrendRiderStrategy
    capital, sma = MCX_VARIANTS[variant]
    params = {"capital": capital, "risk_pct": 0.01, "use_loser_filter": True, "use_sma_filter": sma,
              "adx_threshold": 20.0}
    strat = MCXTrendRiderStrategy("audit_mcx", "mcx_trend_rider", params)
    bars = {n: D.mcx_proxy(n, DAILY_FETCH_START, WINDOW_END.date()) for n in D.MCX_PROXY}
    acct = sim.run(strat, bars, capital=capital, window_start=WINDOW_START, window_end=WINDOW_END,
                   segment_of=lambda _i: "mcx_fut", point_value_of=lambda i: COMMODITY_SPECS[i]["point_value"],
                   sizing="signal")
    return {"strategy": "mcx_trend_rider", "tf": variant, "symbol": "PORTFOLIO",
            "coverage": {s: D.coverage(b) for s, b in bars.items()}, **_dump(acct)}


def _safe(fn, *args):
    import logging
    logging.disable(logging.INFO)  # strategies log every signal; thousands of lines per job
    try:
        return fn(*args)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc(), "args": list(args)}


def main(argv: list[str]) -> None:
    mode = argv[0] if argv else "daily"
    only = set(argv[1:])
    tfs = ["1d", "1w"] if mode == "daily" else ["4h", "1h", "15m"]
    symbols = [i["symbol"] for i in D.universe()]
    if mode == "daily":  # warm the yfinance cache serially (yfinance is not process-safe on first fetch)
        for s in symbols:
            D.daily(s, DAILY_FETCH_START, WINDOW_END.date())
            D.splits(s)
        for n in D.MCX_PROXY:
            D.mcx_proxy(n, DAILY_FETCH_START, WINDOW_END.date())
    else:  # Breeze fetch is serial and rate-limited; do it up front, cached per chunk
        for tf in tfs:
            for s in symbols:
                print(f"fetch {s} {tf}", flush=True)
                D.splits(s)
                D.intraday(s, tf, INTRADAY_FETCH_START[tf], WINDOW_END.date())
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(sleeve_job, sid, tf, s) for tf in tfs for sid in GENERIC for s in symbols
            if not only or sid in only]
    if mode == "daily" and (not only or "equity_swing_vcp" in only):
        jobs.append((vcp_job,))
    if mode == "daily" and (not only or "mcx_trend_rider" in only):
        jobs += [(mcx_job, v) for v in MCX_VARIANTS]
    results: dict[tuple, list] = {}
    with ProcessPoolExecutor(max_workers=14) as ex:
        futs = {ex.submit(_safe, *j): j for j in jobs}
        for n, f in enumerate(as_completed(futs), 1):
            r = f.result()
            if "error" in r:
                print(f"[{n}/{len(jobs)}] ERROR {r['args']}: {r['error']}", flush=True)
                continue
            print(f"[{n}/{len(jobs)}] {r['strategy']} {r['tf']} {r['symbol']} trades={len(r['trades'])}", flush=True)
            results.setdefault((r["strategy"], r["tf"]), []).append(r)
    for (sid, tf), parts in results.items():
        path = OUT / f"{sid}_{tf}.json"
        path.write_text(json.dumps({"strategy": sid, "tf": tf, "window": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
                                    "sleeves": parts}, default=str), encoding="utf-8")
        print(f"wrote {path}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
