"""Build docs/strategy_audit/STRATEGY_AUDIT_REPORT.md + per-trade CSVs from the raw runs.

    python -m tools.strategy_audit.report
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from tools.strategy_audit import costs
from tools.strategy_audit import data as D
from tools.strategy_audit.common import REPORT_DIR
from tools.strategy_audit.results import (
    TF_ORDER, fmt_pf, horizon, inr, load_four_indicator, load_no_brainer, load_ohlc, pct, verdict,
)
from platform_config.strategy_meta import AUDIT_PATH as AUDIT_JSON, STATUS_BY_VERDICT, load_flags

TRADES_DIR = REPORT_DIR / "trades"
GENERATED = date.today().isoformat()

# --------------------------------------------------------------------------------------------
# Rules exactly as implemented in code (read from strategies/*.py), plus audit findings.
# --------------------------------------------------------------------------------------------
INFO: dict[str, dict[str, Any]] = {
    "ema_crossover": {
        "name": "EMA Crossover Momentum", "code": "strategies/__init__.py::EMACrossover",
        "tf_policy": "No timeframe in the rules -> tested on 15m, 1h, 4h, 1d, 1w",
        "universe": "18 NSE instruments (NIFTY, BANKNIFTY, SENSEX + 15 large caps); 1 sleeve of Rs 1,00,000 each",
        "rules": [
            "EMA(9) and EMA(21) of the close.",
            "BUY signal on every bar where EMA9 > EMA21; SELL signal on every bar where EMA9 < EMA21.",
            "Engine semantics: BUY while flat opens a long; SELL while long closes it; the next SELL opens a short "
            "(stop-and-reverse with a one-bar gap). Same-side signals are ignored.",
            "Sizing: every entry uses all available sleeve cash (platform engine rule), equities rounded down to 5 shares.",
            "No stop-loss, no ATR stop, no trailing stop.",
        ],
        "findings": [
            "**Crossover check is broken**: `prev_fast` and `prev_slow` are both `_price_history[-2]` (the previous "
            "close), so the 'crossover' condition is always true - the strategy fires on every bar of a regime, not "
            "on the cross. It behaves as an always-in-market EMA regime follower.",
            "The catalog description promises an 'ATR stop and trailing risk management' - none exists in the code.",
            "Short entries in cash equities cannot be carried overnight in India (only intraday MIS or via stock futures).",
        ],
    },
    "rsi": {
        "name": "RSI Mean Reversion", "code": "strategies/__init__.py::RSIStrategy",
        "tf_policy": "No timeframe in the rules -> tested on 15m, 1h, 4h, 1d, 1w",
        "universe": "18 NSE instruments, 1 sleeve of Rs 1,00,000 each",
        "rules": [
            "RSI(14) from simple averages of gains/losses (Cutler RSI, not Wilder's smoothing).",
            "BUY signal while RSI < 30; SELL signal while RSI > 70.",
            "Engine semantics: long on the first oversold bar, exit on the first overbought bar, short on the next "
            "overbought bar, cover when RSI < 30 again.",
            "Sizing: all available sleeve cash.",
            "No stop-loss.",
        ],
        "findings": [
            "The catalog promises 'trailing breakeven protection' - not implemented; positions have no stop at all, "
            "which is why average holding periods run to months.",
            "Shorts in cash equities are not executable overnight.",
        ],
    },
    "breakout": {
        "name": "Donchian Breakout 20", "code": "strategies/__init__.py::BreakoutStrategy",
        "tf_policy": "No timeframe in the rules -> tested on 15m, 1h, 4h, 1d, 1w",
        "universe": "18 NSE instruments, 1 sleeve of Rs 1,00,000 each",
        "rules": [
            "Channel = highest high / lowest low of the previous 19 bars (`[-20:-1]` slice).",
            "BUY on a close above the channel high; SELL on a close below the channel low.",
            "Engine semantics: long on the breakout, exit on the breakdown, short on the next bar that is still "
            "below the channel.",
            "Sizing: all available sleeve cash. No stop other than the opposite channel.",
        ],
        "findings": [
            "Lookback is effectively 19 bars, not 20.",
            "Shorts in cash equities are not executable overnight.",
        ],
    },
    "lorentzian_ml": {
        "name": "Lorentzian Classification ML", "code": "strategies/lorentzian_ml.py + lorentzian_strategy/",
        "tf_policy": "Timeframe is a user parameter (default 1d) -> tested on 15m, 1h, 4h, 1d, 1w",
        "universe": "18 NSE instruments, 1 sleeve of Rs 1,00,000 each",
        "rules": [
            "Features (Pine defaults): RSI(14), WaveTrend(10,11), CCI(20), ADX(20), RSI(9), each normalised.",
            "Approximate nearest neighbours: k = 8 neighbours by Lorentzian distance, every 4th bar sampled, "
            "training label = direction of the past 4-bar move (Pine-exact port).",
            "Filters: volatility filter, regime filter (threshold -0.1), Nadaraya-Watson kernel trend filter "
            "(h=8, r=8, x=25, lag=2).",
            "Entry: new long when the prediction turns positive with filters + bullish kernel; new short mirrored. "
            "An opposite entry reverses the position.",
            "Exit: strict 4-bar holding exit (dynamic kernel exits off by default).",
            "Platform adapter re-runs the pipeline on the last ~750 bars at every candle (max_bars_back capped to 400).",
            "Sizing: all available sleeve cash (the strategy itself trades a fixed quantity of 1).",
        ],
        "findings": [
            "The platform BacktestEngine desyncs on reversals: a BUY while short only closes the short (it never "
            "opens the long the strategy believes it holds), so later exits open unintended shorts. The audit uses "
            "reversal semantics, like the Lorentzian paper trader does.",
            "The Pine anchor `maxBarsBackIndex` is tied to the last bar of whatever data is passed in, so a single "
            "full-history run and the per-candle replay give different signals - only the per-candle replay matches live.",
        ],
    },
    "equity_swing_vcp": {
        "name": "Equity Swing VCP", "code": "strategies/equity_swing_vcp.py",
        "tf_policy": "Daily bars (defined by the strategy: Minervini trend template + VCP)",
        "universe": "15 NSE large caps in one shared Rs 1,00,000 account; NIFTY 50 as the regime benchmark",
        "rules": [
            "Trend template: close > SMA150 and SMA200; SMA150 > SMA200; SMA200 rising vs 20 bars ago; "
            "SMA50 > SMA150 > SMA200; close > SMA50; close >= 1.25 x 52-week low and >= 0.75 x 52-week high; "
            "6-month return >= 8%.",
            "Market regime: NIFTY 50 close above its 50-day SMA.",
            "VCP: 35-bar base depth 4-35%; ATR(10) now <= 95% of ATR(10) 25 bars ago; 5-day average volume "
            "<= 1.25 x 50-day average; pivot = highest high of the last 15 bars before today.",
            "Entry: close crosses above the pivot, volume >= 1.3 x 50-day average, close in the top 40% of the day's range.",
            "Stop: max(4%, distance to the 10-day low) capped at 7%; size = 1.25% risk of Rs 1,00,000, max 20% of capital per position.",
            "Management: stop to breakeven at +1R; sell 33% at +2R; exit the rest on a close below EMA(21) (only while in profit).",
            "Long only.",
        ],
        "findings": [
            "Stop exits fill exactly at the stop price even when the stock gaps below it (optimistic); the audit "
            "fills gap-downs at the open.",
            "'Relative strength rating' is a flat 6-month +8% momentum check, not a percentile RS rank vs the market.",
            "The platform BacktestEngine replaces the strategy's risk sizing with all-cash sizing; the audit uses the "
            "strategy's own sizing (as the paper trader does).",
            "The universe (15 mega caps) is structurally ill-suited to VCP, which targets growth leaders - hence very few setups.",
        ],
    },
    "mcx_trend_rider": {
        "name": "MCX Trend Rider", "code": "strategies/mcx_trend_rider.py",
        "tf_policy": "Daily bars (defined by the strategy: Turtle-style Donchian 20/55)",
        "universe": "MCX GOLDM, SILVERM, CRUDEOIL futures in one account (proxy data, see Methodology)",
        "rules": [
            "Entry: close above the 20-day high (below the 20-day low for shorts), both excluding today, with ADX(14) >= 20.",
            "Turtle loser-skip: after a losing trade the next entry needs a 55-day breakout.",
            "Optional regime filter: longs only above SMA(200), shorts only below (the code defaults it ON, the "
            "dashboard catalog turns it OFF).",
            "Size: lots = floor(1% of capital / (2 x ATR(20) x point value)), minimum 1 lot.",
            "Initial stop entry -/+ 2 x ATR(20); to breakeven at +1 ATR; Chandelier trail (highest high - 3 x ATR(14)) from +2 ATR.",
            "Other exits: close through the 10-day channel; 90 days held with < 0.5 ATR profit.",
        ],
        "findings": [
            "At Rs 1,00,000 the 1% risk rule always floors to 0 lots and the code forces 1 lot - one CRUDEOIL lot is "
            "~Rs 7 lakh notional, so a single stop costs up to ~40% of capital. The configured capital is far below "
            "what the sizing rule assumes.",
            "The catalog sets `use_sma_filter: False`, overriding the strategy's own default regime filter.",
            "No provider serves multi-year continuous MCX futures (Breeze has no MCX segment, Angel only live contracts).",
        ],
    },
    "four_indicator_system": {
        "name": "Four Indicator System", "code": "strategies/four_indicator_system.py + backtest/four_indicator_backtest.py",
        "tf_policy": "5-minute NIFTY bars (defined by the strategy)",
        "universe": "NIFTY weekly options (CE and PE)",
        "rules": [
            "Call entry, all on the same 5-minute close: SuperTrend(10,3) up; RSI(14) > 70; close above the prior "
            "day's pivot R1; close above the upper Bollinger band (20, 2).",
            "Put entry (exact mirror): SuperTrend down; RSI(14) < 30; close below prior-day S1; close below the lower band.",
            "Strike: nearest weekly expiry; the strike whose real premium is closest to 1% of spot (probed on Breeze).",
            "Exit: SuperTrend flip; intraday square-off at 15:20 if still open.",
            "Size: lots = floor(balance / Rs 50,000) (compounding), min 1; NIFTY lot 75 in 2025, 65 from Jan-2026.",
        ],
        "findings": [
            "The runner books statutory charges but no slippage or spread; the audit adds both. Lot sizing "
            "compounds on the runner's balance (before spread/slippage), so sizing is slightly optimistic.",
            "It is profitable only before costs: execution friction on ~350 short-hold option trades a year "
            "is larger than the edge.",
        ],
    },
    "nifty_no_brainer": {
        "name": "NIFTY No Brainer", "code": "strategies/nifty_no_brainer*.py + backtest/nifty_no_brainer_runner.py",
        "tf_policy": "Monthly cycle; 1-minute bars for the 15:16 entry, 5-minute bars for the lifecycle",
        "universe": "NIFTY monthly call options",
        "rules": [
            "Entry 15:16 IST on the last Friday of the month (previous trading day on a holiday), next month's monthly expiry.",
            "Structure (1:-2:1 CE): buy 1 x (ATM + 300), sell 2 x (near buy + 300), buy 1 far hedge on the 500-point "
            "grid ~1,000 points above the sold strike.",
            "Net premium rule: skip if the net debit exceeds 1% of margin; if the net credit exceeds 1% shift all "
            "strikes up 100 points until it doesn't.",
            "Exits: target +2.5% of margin; stop -3% of margin (booked at no worse than -3.3%); max hold 19 days; expiry.",
            "Margin: proxy of (700 points + debit) x lot size per set (historical broker margin is not recoverable); "
            "lots = floor(balance / margin per set), compounding.",
        ],
        "findings": [
            "The margin used for sizing and for the target/stop is a proxy, so returns on capital are estimates.",
        ],
    },
    "index_oi_momentum": {
        "name": "Index Options OI Momentum", "code": "strategies/index_oi_momentum.py",
        "tf_policy": "Tick-by-tick (Angel WebSocket SNAP_QUOTE)",
        "universe": "NIFTY / BANKNIFTY / SENSEX ATM options",
        "rules": [
            "OI velocity of the index future over a 60-90 s rolling window (k = 4) combined with a price/OI direction matrix.",
            "Confirmations: strike-level OI, option premium breakout + velocity, bid/ask spread <= 1.2% and depth.",
            "Modes: base vs expiry-day (auto from the expiry calendar); expiry mode checks the max-pain wall.",
            "Risk: 0.75% of capital per trade, hard stop 22% of premium, partial exits + trailing, quick exits, "
            "max 7 trades/day per index, daily loss limit -3%.",
        ],
        "findings": [
            "No provider serves historical tick-level open interest, so no real backtest is possible. The repo's "
            "`backtest/oi_momentum_backtest.py` runs on a synthetic tape and is not evidence of edge.",
            "Live paper trading started today; zero trades so far.",
        ],
    },
}

FLAGS = load_flags()

ORDER = ["four_indicator_system", "nifty_no_brainer", "index_oi_momentum", "mcx_trend_rider",
         "equity_swing_vcp", "lorentzian_ml", "ema_crossover", "rsi", "breakout"]
MULTI_TF = {"ema_crossover", "rsi", "breakout", "lorentzian_ml"}


# --------------------------------------------------------------------------------------------
def write_trades_csv(name: str, trades) -> str:
    TRADES_DIR.mkdir(parents=True, exist_ok=True)
    path = TRADES_DIR / f"{name}.csv"
    fields = ["instrument", "side", "qty", "entry_time", "entry_price", "exit_time", "exit_price",
              "entry_reason", "exit_reason", "gross_pnl", "commission", "spread", "slippage", "net_pnl",
              "holding_days"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for t in trades:
            row = {k: getattr(t, k) for k in fields}
            for k in ("entry_price", "exit_price", "gross_pnl", "commission", "spread", "slippage", "net_pnl",
                      "holding_days"):
                row[k] = round(row[k], 2)
            w.writerow(row)
    return f"trades/{path.name}"


def metric_row(label: str, m: dict) -> str:
    return (f"| {label} | {m['trades']} | {pct(m['win_rate'])} | {fmt_pf(m['profit_factor'])} | "
            f"{inr(m['expectancy'])} ({pct(m['expectancy_pct'], 2)}) | {inr(m['net_pnl'])} | "
            f"{pct(m['return_pct'])} | {pct(m['cagr'])} | {pct(m['max_dd_pct'])} | {m['avg_hold_days']:.1f} d |")


METRIC_HEAD = ("| Run | Trades | Win rate | Profit factor (net) | Expectancy / trade | Net PnL | Return | CAGR | "
               "Max drawdown | Avg hold |\n|---|---|---|---|---|---|---|---|---|---|")


def cost_row(label: str, m: dict) -> str:
    drag = m["costs"] / abs(m["gross_pnl"]) if m["gross_pnl"] else float("inf")
    return (f"| {label} | {inr(m['gross_pnl'])} | {inr(m['commission'])} | {inr(m['spread'])} | "
            f"{inr(m['slippage'])} | {inr(m['costs'])} | {inr(m['net_pnl'])} | "
            f"{'n/a' if drag == float('inf') else pct(drag, 0)} | {inr(m['net_pnl_2x_friction'])} |")


COST_HEAD = ("| Run | Gross PnL | Commission + levies | Spread | Slippage | Total costs | Net PnL | "
             "Costs / gross | Net if spread+slippage 2x |\n|---|---|---|---|---|---|---|---|---|")


def trade_summary(book, top: int = 5) -> list[str]:
    trades = book.trades
    if not trades:
        return ["No trades."]
    out = []
    reasons = Counter(t.exit_reason for t in trades)
    out.append("Exit reasons: " + ", ".join(f"{r} {n}" for r, n in reasons.most_common(8)) + ".")
    by_side = Counter(t.side for t in trades)
    out.append("Sides: " + ", ".join(f"{s} {n}" for s, n in by_side.items()) + ".")
    streak = worst = 0
    for t in sorted(trades, key=lambda t: t.exit_time):
        streak = streak + 1 if t.net_pnl <= 0 else 0
        worst = max(worst, streak)
    out.append(f"Longest losing streak: {worst} trades.")
    lines = ["| # | Instrument | Side | Entry | Exit | Entry px | Exit px | Exit reason | Net PnL |",
             "|---|---|---|---|---|---|---|---|---|"]
    ranked = sorted(trades, key=lambda t: t.net_pnl, reverse=True)
    for i, t in enumerate(ranked[:top] + ranked[-top:] if len(ranked) > 2 * top else ranked, 1):
        structure = t.side == "1:-2:1 CE"  # entry "price" is the structure's net premium in rupees
        entry_px = f"net {inr(t.entry_price)}" if structure else f"{t.entry_price:,.2f}"
        exit_px = "n/a" if structure else f"{t.exit_price:,.2f}"
        lines.append(f"| {i} | {t.instrument} | {t.side} | {t.entry_time[:16].replace('T', ' ')} | "
                     f"{t.exit_time[:16].replace('T', ' ')} | {entry_px} | {exit_px} | "
                     f"{t.exit_reason} | {inr(t.net_pnl)} |")
    title = f"Best {top} and worst {top} trades:" if len(ranked) > 2 * top else "All trades:"
    return out + ["", title, ""] + lines


def buy_and_hold(symbols: list[str], ws: datetime, we: datetime) -> dict[str, float]:
    out = {}
    for s in symbols:
        bars = [c for c in D.daily(s, date(2020, 6, 1), we.date()) if ws <= c.timestamp <= we]
        if bars:
            out[s] = bars[-1].close / bars[0].close - 1
    return out


# --------------------------------------------------------------------------------------------
def build() -> str:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    runs: dict[str, dict[str, Any]] = {}
    for sid in ["ema_crossover", "rsi", "breakout", "lorentzian_ml"]:
        runs[sid] = {tf: r for tf in TF_ORDER if (r := load_ohlc(sid, tf))}
    runs["equity_swing_vcp"] = {tf: r for tf in ["1d"] if (r := load_ohlc("equity_swing_vcp", tf))}
    runs["mcx_trend_rider"] = {tf: r for tf in ["1d", "1d_cap10L", "1d_cap10L_sma200"]
                               if (r := load_ohlc("mcx_trend_rider", tf))}
    fi, nnb = load_four_indicator(), load_no_brainer()
    runs["four_indicator_system"] = {"5m": fi} if fi else {}
    runs["nifty_no_brainer"] = {"monthly": nnb} if nnb else {}
    runs["index_oi_momentum"] = {}

    # ---- verdicts -------------------------------------------------------------------------
    summary: dict[str, dict[str, Any]] = {}
    for sid in ORDER:
        rs = runs[sid]
        if sid == "index_oi_momentum":
            summary[sid] = {"verdict": "EXPERIMENTAL", "why": "not backtestable on real data (no historical tick OI); "
                            "keep in paper trading only until it has a live track record", "primary": None}
            continue
        if not rs:
            summary[sid] = {"verdict": "PENDING", "why": "backtest not available", "primary": None}
            continue
        if sid in MULTI_TF:
            best_tf = max(rs, key=lambda tf: rs[tf]["metrics"]["net_pnl"])
            bm = rs[best_tf]["metrics"]
            v, why = verdict(bm, tf_selected=True)
            if bm["net_pnl"] > 0 and bm["long_net"] <= 0 < bm["short_net"]:
                short_note = (f"all of the {best_tf} profit comes from shorts ({inr(bm['short_net'])} over "
                              f"{bm['short_trades']} trades, avg hold {bm['avg_hold_days']:.0f} d), which cannot be "
                              f"carried overnight in cash equities; the long side alone nets {inr(bm['long_net'])}")
                why = short_note if v != "DEPRECATE" else f"{why}; {short_note}"
                v = "DEPRECATE"
            if v != "DEPRECATE":
                losing = [tf for tf in rs if rs[tf]["metrics"]["net_pnl"] <= 0]
                if len(losing) >= 3:
                    v, why = "WATCH", why + f"; only works on {best_tf} - loses on {', '.join(losing)} (fragile)"
            primary = best_tf
        elif sid == "mcx_trend_rider":
            primary = "1d"
            v, why = verdict(rs["1d"]["metrics"])
            if rs["1d"]["metrics"]["max_dd_pct"] >= 1.0:
                v = "DEPRECATE" if v != "KEEP" else v
                why = (f"as configured (Rs 1 lakh, forced 1 lot) the account is wiped out: max drawdown "
                       f"{pct(rs['1d']['metrics']['max_dd_pct'])} of capital; " + why)
        else:
            primary = next(iter(rs))
            v, why = verdict(rs[primary]["metrics"], min_trades=20 if sid == "nifty_no_brainer" else 30)
        summary[sid] = {"verdict": v, "why": why, "primary": primary}

    L: list[str] = []
    w = L.append
    w("# Strategy Audit - Real Backtests, Costs and Verdicts")
    w("")
    w(f"Generated {GENERATED} by `python -m tools.strategy_audit.report` (raw runs under `data/strategy_audit/`). "
      "Every number below is net of commission, statutory levies, bid/ask spread and slippage unless labelled gross.")
    w("")
    # ---- executive summary ----------------------------------------------------------------
    w("## 1. Executive summary")
    w("")
    w("| Strategy | Horizon | Verdict | Run shown | Trades | Win rate | Net PF | Expectancy / trade | Net PnL | Max DD | Why |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for sid in ORDER:
        s, info = summary[sid], INFO[sid]
        rs = runs[sid]
        if s["primary"]:
            m = rs[s["primary"]]["metrics"]
            hz = ", ".join(FLAGS[sid]["horizon"])
            w(f"| {info['name']} | {hz} | **{s['verdict']}** | {s['primary']} | {m['trades']} | {pct(m['win_rate'])} | "
              f"{fmt_pf(m['profit_factor'])} | {inr(m['expectancy'])} | {inr(m['net_pnl'])} | {pct(m['max_dd_pct'])} | {s['why']} |")
        else:
            w(f"| {info['name']} | {', '.join(FLAGS[sid]['horizon'])} | **{s['verdict']}** | - | - | - | - | - | - | - | {s['why']} |")
    w("")
    w("**Verdict rules** (applied to net-of-cost results): "
      "**KEEP** = net profit factor >= 1.3 (>= 1.5 when the best of several timeframes is picked, to discount "
      "selection bias), >= 30 trades (20 for the monthly No Brainer), max drawdown <= 25% and still profitable "
      "with doubled spread + slippage. **DEPRECATE** = loses money after costs on its defined timeframe (or on its "
      "best timeframe), earns less than a 6% a year risk-free hurdle (bank FD), or cannot survive at its "
      "configured capital. **WATCH** = profitable but fails one KEEP "
      "test. **EXPERIMENTAL** = cannot be backtested on real data yet.")
    w("")
    # ---- methodology ------------------------------------------------------------------------
    w("## 2. Methodology")
    w("")
    w("**Test windows.** OHLC strategies: 1-Oct-2023 to 29-Sep-2026 (3 years), with extra history before the "
      "window fed only to warm up indicators (positions opened during warm-up are tracked but excluded). "
      "Option strategies: 1-Jan-2025 to 29-Sep-2026 - the span covered by the repo's confirmed NIFTY lot-size and "
      "expiry metadata (`nifty_expiries.json`).")
    w("")
    w("**Data (all real exchange prices).**")
    w("")
    w("| Strategy group | Timeframes | Source |")
    w("|---|---|---|")
    w("| EMA / RSI / Donchian / Lorentzian, VCP | 1d, 1w | yfinance daily NSE bars (split-adjusted), weekly = Mon-Fri resample |")
    w("| EMA / RSI / Donchian / Lorentzian | 15m, 1h, 4h | Angel One SmartAPI 15-minute and 1-hour history (4h resampled from 1h, "
      "session-anchored at 09:15), split-adjusted with yfinance's split history |")
    w("| Four Indicator System, NIFTY No Brainer | 5m / 1m | ICICI Breeze - NIFTY spot and real expired NIFTY option contracts, "
      "via the platform's own runners (`backtest/four_indicator_backtest.py`, `backtest/nifty_no_brainer_runner.py`) |")
    w("| MCX Trend Rider | 1d | **Proxy**: COMEX gold/silver and NYMEX WTI continuous futures x USD/INR x Indian "
      "import duty (15% until 23-Jul-2024, 6% after) in MCX price units - see limitations |")
    w("")
    w("Tata Motors is excluded: its Oct-2025 demerger breaks price continuity. Broker history is unadjusted, so every "
      "intraday series is corrected for splits/bonuses (e.g. Reliance 1:1 bonus on 28-Oct-2024, HDFC Bank 1:1 on "
      "26-Aug-2025) - without this a bonus shows up as a fake 50% crash.")
    w("")
    w("**Fills and sizing.** Signals are acted on at the close of the signal bar (the platform engine's rule). "
      "Strategy-supplied stop prices are gap-adjusted: a long stopped at S fills at min(S, open). Generic "
      "strategies (EMA, RSI, Donchian, Lorentzian) run one independent Rs 1,00,000 sleeve per instrument and put "
      "all sleeve cash into each entry, like the platform engine; index sleeves (NIFTY, BANKNIFTY, SENSEX) are "
      "priced as unleveraged index-futures exposure. VCP and MCX Trend Rider use their own risk-based sizing in "
      "one shared account (as their paper traders do). Option strategies use their runners' compounding lot sizing.")
    w("")
    w("### 2.1 Hidden execution costs - assumptions")
    w("")
    w("Charged on every fill and reported separately per strategy:")
    w("")
    w("| Segment | Commission + statutory levies | Half spread per side | Slippage per side |")
    w("|---|---|---|---|")
    for seg, comm, spr, slip in costs.describe():
        w(f"| {seg} | {comm} | {spr} | {slip} |")
    w("")
    w("*Commission* = broker + exchange + government charges at the rates in force on each trade date. "
      "*Spread* = crossing half the bid/ask on entry and exit. *Slippage* = adverse move between the bar close "
      "that generated the signal and a market-order fill. Every cost table also shows net PnL with spread and "
      "slippage doubled, as a robustness check.")
    w("")
    w("### 2.2 Metric definitions")
    w("")
    w("- **Net PnL**: sum of trade PnL after all costs. **Return / CAGR** are on the capital shown for that run.")
    w("- **Win rate**: share of trades with net PnL > 0.")
    w("- **Expectancy**: average net PnL per trade (in rupees, and as % of the trade's entry notional).")
    w("- **Profit factor (net)**: gross winning trades / gross losing trades, both after costs.")
    w("- **Max drawdown**: largest peak-to-trough fall of the mark-to-market equity curve, as % of the peak.")
    w("")
    w("### 2.3 Limitations")
    w("")
    w("- **MCX data is a proxy.** Trend signals track MCX closely (same underlying, INR-converted), but MCX-specific "
      "basis, contract rolls and exchange hours differ. Treat MCX results as indicative.")
    w("- **Index OI Momentum cannot be backtested** - it needs tick-level historical open interest, which no "
      "configured provider serves.")
    w("- **Cash-equity shorts** (EMA, RSI, Donchian, Lorentzian) cannot be carried overnight in India; each "
      "strategy's long/short split is shown so the long-only result can be read directly.")
    w("- **Option margins** (No Brainer) are a proxy; broker margins for expired contracts are not recoverable.")
    w("")
    # ---- classification & flags ------------------------------------------------------------
    w("## 3. Classification and flags")
    w("")
    w("**Built for** is the design horizon (`platform_config/strategy_flags.yaml`): **Intraday** = opened and "
      "closed in the same session; **Short term** = swing / positional, days to ~2 months; **Long term** = held "
      "for months. **Observed** is what the backtest actually did (average holding period of the run shown).")
    w("")
    w("| Strategy | Built for | Observed | Segment | Instrument | Direction | Bias | Hedging | Style | Status |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    horizons: dict[str, str] = {}
    for sid in ORDER:
        f, s = FLAGS[sid], summary[sid]
        built = ", ".join(f["horizon"])
        horizons[sid] = f["horizon"][0]
        if s["primary"]:
            m = runs[sid][s["primary"]]["metrics"]
            observed = f"{horizon(m)} ({m['avg_hold_days']:.1f} d avg on {s['primary']})"
        else:
            observed = "-"
        status = STATUS_BY_VERDICT.get(s["verdict"], "experimental")
        w(f"| {INFO[sid]['name']} | {built} | {observed} | {', '.join(f['segment'])} | "
          f"{', '.join(f['instrument'])} | {', '.join(f['direction'])} | {', '.join(f['bias'])} | "
          f"{', '.join(f['hedging'])} | {', '.join(f['style'])} | {status} |")
    w("")
    for hz in ("Intraday", "Short term", "Long term"):
        names = [INFO[s]["name"] for s in ORDER if horizons[s] == hz]
        w(f"- **{hz}:** {', '.join(names) if names else 'none'}")
    w("")
    # ---- per strategy ------------------------------------------------------------------------
    w("## 4. Strategy detail")
    w("")
    audit_json: dict[str, Any] = {}
    for n, sid in enumerate(ORDER, 1):
        info, s, rs = INFO[sid], summary[sid], runs[sid]
        w(f"### 4.{n} {info['name']} - {s['verdict']}")
        w("")
        w(f"*Code:* `{info['code']}`  ")
        w(f"*Timeframe:* {info['tf_policy']}  ")
        w(f"*Universe:* {info['universe']}")
        w("")
        w("**Strategy rules (as implemented)**")
        w("")
        for r in info["rules"]:
            w(f"- {r}")
        w("")
        if info["findings"]:
            w("**Implementation findings**")
            w("")
            for r in info["findings"]:
                w(f"- {r}")
            w("")
        val_path = D.AUDIT_DATA_DIR / "mcx_proxy_validation.json"
        if sid == "mcx_trend_rider" and val_path.exists():
            val = json.loads(val_path.read_text(encoding="utf-8"))
            w("**Proxy check against real MCX bars (Angel One, live contracts)**")
            w("")
            w("| Proxy | Real contract | Overlap | Daily-return correlation | Real move | Proxy move | Price ratio real/proxy |")
            w("|---|---|---|---|---|---|---|")
            for name, v in val.items():
                if "daily_return_correlation" in v:
                    w(f"| {name} | {v['contract']} | {v['overlap_days']} d ({v['from']} to {v['to']}) | "
                      f"{v['daily_return_correlation']:.2f} | {pct(v['real_period_return'])} | "
                      f"{pct(v['proxy_period_return'])} | {v['price_ratio_mean']:.3f} +/- {v['price_ratio_std']:.3f} |")
                else:
                    w(f"| {name} | {v['contract']} | {v['overlap_days']} d | {v.get('note', '')} | | | |")
            w("")
        if not rs:
            w(f"**Backtest:** {s['why']}.")
            w("")
            audit_json[sid] = {"verdict": s["verdict"], "why": s["why"]}
            continue
        w("**Backtest results**")
        w("")
        w(METRIC_HEAD)
        for tf, r in rs.items():
            w(metric_row(tf + (" (shown)" if tf == s["primary"] and len(rs) > 1 else ""), r["metrics"]))
        w("")
        w("**Hidden execution costs**")
        w("")
        w(COST_HEAD)
        for tf, r in rs.items():
            w(cost_row(tf, r["metrics"]))
        w("")
        if sid in MULTI_TF or sid in ("mcx_trend_rider",):
            w("Long vs short (net): " + "; ".join(
                f"{tf}: long {inr(r['metrics']['long_net'])} ({r['metrics']['long_trades']} trades), "
                f"short {inr(r['metrics']['short_net'])} ({r['metrics']['short_trades']})" for tf, r in rs.items()) + ".")
            w("")
        primary = rs[s["primary"]]
        if primary.get("per_symbol") and len(primary["per_symbol"]) > 1:
            w(f"**Per instrument ({s['primary']})**")
            w("")
            w("| Instrument | Trades | Win rate | Net PF | Net PnL | Return | Max DD |")
            w("|---|---|---|---|---|---|---|")
            for sym, m in primary["per_symbol"].items():
                w(f"| {sym} | {m['trades']} | {pct(m['win_rate'])} | {fmt_pf(m['profit_factor'])} | "
                  f"{inr(m['net_pnl'])} | {pct(m['return_pct'])} | {pct(m['max_dd_pct'])} |")
            w("")
        elif primary["book"].trades and sid in ("mcx_trend_rider", "equity_swing_vcp"):
            w(f"**Per instrument ({s['primary']})**")
            w("")
            w("| Instrument | Trades | Win rate | Net PnL | Avg net / trade |")
            w("|---|---|---|---|---|")
            groups: dict[str, list] = {}
            for t in primary["book"].trades:
                groups.setdefault(t.instrument, []).append(t)
            for sym, ts in sorted(groups.items()):
                net = sum(t.net_pnl for t in ts)
                w(f"| {sym} | {len(ts)} | {pct(sum(t.net_pnl > 0 for t in ts) / len(ts))} | {inr(net)} | "
                  f"{inr(net / len(ts))} |")
            w("")
        if sid in MULTI_TF:
            ws, we = primary["window"]
            bh = buy_and_hold([sym for sym in primary["per_symbol"]], ws, we)
            if bh:
                avg = sum(bh.values()) / len(bh)
                w(f"Benchmark: equal-weight buy-and-hold of the same {len(bh)} instruments over the window "
                  f"returned {pct(avg)} before costs.")
                w("")
        w("**Backtest trade report**")
        w("")
        links = []
        for tf, r in rs.items():
            links.append(f"[{tf}]({write_trades_csv(f'{sid}_{tf}', r['book'].trades)})")
        w(f"Full trade logs (CSV, every trade with its cost breakdown): {', '.join(links)}.")
        w("")
        for line in trade_summary(primary["book"]):
            w(line)
        w("")
        if sid == "nifty_no_brainer" and nnb and nnb["skipped"]:
            w("Months not traded: " + ", ".join(f"{m['month']} ({m['decision']})" for m in nnb["skipped"]) + ".")
            w("")
        if sid == "four_indicator_system" and fi and fi["open_at_end"]:
            w(f"{len(fi['open_at_end'])} trade(s) still open at the window end are excluded.")
            w("")
        w(f"**Verdict: {s['verdict']}** - {s['why']}.")
        w("")
        m = primary["metrics"]
        audit_json[sid] = {
            "verdict": s["verdict"], "why": s["why"], "run": s["primary"], "horizon": horizons[sid],
            "window": [primary["window"][0].date().isoformat(), primary["window"][1].date().isoformat()],
            "trades": m["trades"], "win_rate": round(m["win_rate"], 4),
            "profit_factor": None if m["profit_factor"] == float("inf") else round(m["profit_factor"], 3),
            "expectancy": round(m["expectancy"], 2), "net_pnl": round(m["net_pnl"], 2),
            "return_pct": round(m["return_pct"], 4), "max_dd_pct": round(m["max_dd_pct"], 4),
            "costs": round(m["costs"], 2), "capital": m["capital"],
        }
    # ---- recommendations ----------------------------------------------------------------------
    w("## 5. Recommendations")
    w("")
    for v in ("KEEP", "WATCH", "EXPERIMENTAL", "DEPRECATE", "PENDING"):
        names = [INFO[s]["name"] for s in ORDER if summary[s]["verdict"] == v]
        if names:
            w(f"- **{v}:** {', '.join(names)}")
    w("")
    w("**What is worth salvaging / next steps**")
    w("")
    sma = runs["mcx_trend_rider"].get("1d_cap10L_sma200")
    if sma:
        m = sma["metrics"]
        w(f"- **MCX Trend Rider** is the only strategy with an edge above the hurdle, and only when run with "
          f"its own SMA(200) regime filter switched on and at least Rs 10 lakh (so one lot is survivable): net PF "
          f"{fmt_pf(m['profit_factor'])}, CAGR {pct(m['cagr'])}, max drawdown {pct(m['max_dd_pct'])} over "
          f"{m['trades']} trades - almost all of it from gold/silver longs; crude and every short lost. This is on "
          "proxy data: re-test on real MCX continuous futures before reviving it, and fix the sizing so the "
          "1% risk rule is respected instead of forcing one lot.")
    fi_m = runs["four_indicator_system"].get("5m")
    if fi_m:
        m = fi_m["metrics"]
        w(f"- **Four Indicator System** is profitable before costs (gross {inr(m['gross_pnl'])}) but costs "
          f"{inr(m['costs'])} over {m['trades']} trades. Only a much lower trade frequency (e.g. a daily-trend "
          "or higher-timeframe filter) could make the edge survive execution costs - re-audit before any revival.")
    w("- **Index OI Momentum** stays in paper trading. Breeze's 1-minute F&O history carries open interest, "
      "which could support an approximate (1-minute rather than tick) backtest; until then judge it on at "
      "least 3 months of forward paper results, net of the same costs.")
    w("- **Generic strategies** (EMA, RSI, Donchian, Lorentzian): costs grow with trade frequency, so every "
      "intraday timeframe loses far more than daily/weekly. The EMA and RSI implementations also have "
      "correctness bugs (see findings) and should not be revived as-is.")
    w("")
    w("The dashboard now carries these results: every strategy card has segment / instrument / direction / "
      "hedging / style / horizon flags you can filter on, and deprecated strategies are hidden unless "
      "'Show deprecated' is ticked (`platform_config/strategy_flags.yaml`, `platform_config/strategy_audit.json`).")
    w("")
    AUDIT_JSON.write_text(json.dumps({"generated": GENERATED, "strategies": audit_json}, indent=2), encoding="utf-8")
    (REPORT_DIR / "STRATEGY_AUDIT_REPORT.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return str(REPORT_DIR / "STRATEGY_AUDIT_REPORT.md")


if __name__ == "__main__":
    print(build())
