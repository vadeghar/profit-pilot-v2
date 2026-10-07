"""Self-Aware Trend: the trade plan around the SATS signals, and the simulator that walks it over candles.

A signal is a trend flip of the indicator (indicator.py). The plan decides what is done with it:

  entry    at the close of the signal bar, long on a flip up, short on a flip down.
  stop     the indicator's: beyond the last confirmed pivot, between ``sl_atr_mult`` and ``sl_max_dist``
           ATRs from the entry. Risk R = |entry - stop|.
  targets  ``Plan.targets`` as (R-multiple, share of the position) pairs; ``None`` keeps the script's
           TP1/TP2/TP3 with a third each. Whatever the targets leave over is a runner.
  exits    stop, the last target, an opposite signal ("flip"), ``timeout_bars`` signal bars, and - when
           ``intraday`` - the clock at ``exit_time``.

``SCRIPT`` reproduces the indicator's own book-keeping (the numbers on its dashboard): positions carry
overnight and a stop always fills at the stop price. ``INTRADAY`` is the same plan made tradable inside one
session: entries between ``first_entry`` and ``last_entry``, flat at ``exit_time``, stops that gap fill at
the open. Within a bar the stop is always assumed to come before any target, as in the script.

All prices are index points. backtest.py turns the trades into option premiums and rupees.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

SESSION_OPEN_MINUTE = 9 * 60 + 15
EPS = 1e-9


@dataclass(frozen=True)
class Plan:
    intraday: bool = True
    first_entry: time = time(9, 20)       # earliest signal-bar close that may open a trade
    last_entry: time = time(14, 30)       # latest
    exit_time: time = time(15, 10)        # flat by here (the spot index freezes at 15:15)
    targets: Optional[Tuple[Tuple[float, float], ...]] = None   # ((R, share), ...); None = script's three thirds
    breakeven_after_tp1: bool = False     # once the first target fills, the stop moves to the entry price
    timeout_bars: int = 100               # signal bars
    gap_aware: bool = True                # a bar that opens beyond the stop fills at its open, not at the stop
    min_tqi: float = 0.0                  # entry filters
    min_score: float = 0.0
    htf_align: bool = False               # trade only with the SATS trend of a higher timeframe
    vix_min: float = 0.0                  # previous India VIX close needed
    join_time: Optional[time] = None      # if flat at this time, enter with the standing trend (no flip needed)
    sides: Tuple[int, ...] = (1, -1)


SCRIPT = Plan(intraday=False, gap_aware=False)
INTRADAY = Plan()


def _minute(clock: time) -> int:
    return clock.hour * 60 + clock.minute


def simulate(sig: pd.DataFrame, ex: pd.DataFrame, ex_of_sig: np.ndarray, plan: Plan = INTRADAY, exec_minutes: int = 5,
             htf_trend: Optional[np.ndarray] = None, vix: Optional[np.ndarray] = None) -> List[dict]:
    """Walk ``plan`` over the execution bars ``ex`` (t, o, h, l, c), taking signals from ``sig``.

    ``sig`` is the indicator output on the signal timeframe; ``ex_of_sig[k]`` is the index of the execution
    bar on whose close signal bar ``k`` closes (``sig`` is ``ex`` itself, and this ``arange``, when both are
    the same bars). ``htf_trend`` and ``vix`` are per signal bar. Returns one dict per closed trade."""
    n = len(ex)
    sig_at = np.full(n, -1)
    sig_at[ex_of_sig] = np.arange(len(sig))
    o, h, l, c = (ex[k].to_numpy(float) for k in ("o", "h", "l", "c"))
    t = pd.to_datetime(ex["t"])
    day = t.dt.date.to_numpy()
    start_min = (t.dt.hour * 60 + t.dt.minute).to_numpy()
    end_min = start_min + exec_minutes
    last_of_day = np.r_[day[1:] != day[:-1], True]
    signal, trend = sig["signal"].to_numpy(), sig["trend"].to_numpy()
    tqi, score, atr = sig["tqi"].to_numpy(), sig["score"].to_numpy(), sig["atr"].to_numpy()
    stops = {1: sig["sl_long"].to_numpy(), -1: sig["sl_short"].to_numpy()}
    tp_r = [sig[k].to_numpy() for k in ("tp1_r", "tp2_r", "tp3_r")]
    first_min, last_min, exit_min = _minute(plan.first_entry), _minute(plan.last_entry), _minute(plan.exit_time)
    join_min = _minute(plan.join_time) if plan.join_time else None
    warm_from = int(np.argmax(signal != 0)) if (signal != 0).any() else len(signal)   # first signal = warm-up over

    trades: List[dict] = []
    pos: Optional[dict] = None

    def fill(j: int, price: float, share: float, reason: str, intrabar: bool) -> None:
        minute = (start_min[j] + exec_minutes / 2 if intrabar else end_min[j]) - SESSION_OPEN_MINUTE
        pos["fills"].append({"day": day[j], "minute": float(minute), "price": float(price), "share": share, "reason": reason})
        pos["left"] -= share

    def allowed(d: int, j: int, k: int) -> bool:
        if d not in plan.sides or (plan.intraday and not first_min <= end_min[j] <= last_min):
            return False
        if tqi[k] < plan.min_tqi or score[k] < plan.min_score:
            return False
        if plan.htf_align and (htf_trend is None or htf_trend[k] != d):
            return False
        if plan.vix_min > 0 and (vix is None or not vix[k] >= plan.vix_min):
            return False
        return (c[j] - stops[d][k]) * d > EPS

    for j in range(n):
        k = sig_at[j]
        s = int(signal[k]) if k >= 0 else 0
        if pos is not None:
            d, stop = pos["dir"], pos["stop"]
            if k >= 0:
                pos["age"] += 1
            if (l[j] <= stop) if d > 0 else (h[j] >= stop):
                gapped = plan.gap_aware and ((o[j] < stop) if d > 0 else (o[j] > stop))
                fill(j, o[j] if gapped else stop, pos["left"], "breakeven" if pos["at_breakeven"] else "stop", not gapped)
            elif s == -d:
                fill(j, c[j], pos["left"], "flip", False)
            else:
                for number, (price, share) in enumerate(pos["targets"], start=1):
                    if number > pos["targets_hit"] and ((h[j] >= price) if d > 0 else (l[j] <= price)):
                        fill(j, price, share, f"tp{number}", True)
                        pos["targets_hit"] = number
                        if plan.breakeven_after_tp1:
                            pos["stop"], pos["at_breakeven"] = pos["entry"], True
                if pos["left"] > EPS:
                    if plan.intraday and (end_min[j] >= exit_min or last_of_day[j]):
                        fill(j, c[j], pos["left"], "time", False)
                    elif k >= 0 and pos["age"] >= plan.timeout_bars:
                        fill(j, c[j], pos["left"], "timeout", False)
            if pos["left"] <= EPS:
                points = sum(f["share"] * d * (f["price"] - pos["entry"]) for f in pos["fills"])
                last = pos["fills"][-1]
                trades.append({
                    "date": pos["day"].isoformat(), "dir": d, "entry_minute": pos["minute"], "entry": pos["entry"],
                    "stop": pos["initial_stop"], "risk": pos["risk"], "tqi": pos["tqi"], "score": pos["score"],
                    "kind": pos["kind"], "exit_date": last["day"].isoformat(), "exit_minute": last["minute"],
                    "reason": last["reason"], "targets_hit": pos["targets_hit"], "points": points,
                    "r": points / pos["risk"],
                    "fills": [{**f, "day": f["day"].isoformat()} for f in pos["fills"]]})
                pos = None

        if pos is None and k >= 0:
            d, kind = s, "flip"
            if d == 0 and join_min is not None and end_min[j] == join_min and k >= warm_from:
                d, kind = int(trend[k]), "join"
            if d and allowed(d, j, k):
                entry, stop = c[j], float(stops[d][k])
                risk = abs(entry - stop)
                plan_targets = plan.targets if plan.targets is not None else tuple((r[k], 1.0 / 3.0) for r in tp_r)
                pos = {"dir": d, "entry": entry, "stop": stop, "initial_stop": stop, "risk": risk, "left": 1.0,
                       "targets": [(entry + d * risk * r, share) for r, share in plan_targets], "targets_hit": 0,
                       "at_breakeven": False, "age": 0, "fills": [], "day": day[j],
                       "minute": float(end_min[j] - SESSION_OPEN_MINUTE), "tqi": float(tqi[k]), "score": float(score[k]),
                       "kind": kind}
    return trades
