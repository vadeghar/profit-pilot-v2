"""Itemised execution-cost model for the strategy audit.

Every fill is charged three separately-reported cost buckets:
  * commission - brokerage + statutory levies (STT/CTT, exchange, SEBI, stamp,
                 GST, DP) at the rates in force on the trade date;
  * spread     - half the bid/ask spread, paid on every side (you buy the ask,
                 sell the bid);
  * slippage   - adverse movement between the signal price (bar close) and the
                 actual fill for a market order.
Rates are per rupee of turnover unless noted. Option legs reuse
backtest/charges.py (the platform's own option charge table).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

BROKERAGE_PER_ORDER = 20.0
GST = 0.18
SEBI = 0.000001
DP_CHARGE_PER_SELL = 15.93  # CDSL + depository participant, per scrip per delivery sell day


def _futures_stt(on: date) -> float:
    if on >= date(2026, 4, 1):
        return 0.0005   # Union Budget 2026
    if on >= date(2024, 10, 1):
        return 0.0002   # Union Budget 2024
    return 0.000125


@dataclass(frozen=True)
class FrictionSpec:
    segment: str            # eq | index_fut | mcx_fut
    half_spread_pct: float  # fraction of price, per side
    slippage_pct: float     # fraction of price, per side


FRICTION = {
    "eq": FrictionSpec("eq", 0.0001, 0.0003),
    "index_fut": FrictionSpec("index_fut", 0.00002, 0.0001),
    "mcx_fut": FrictionSpec("mcx_fut", 0.0001, 0.0003),
}

# Option friction (fraction of premium / points per leg), used by the report
# builder for the two option strategies.
OPTION_HALF_SPREAD_PCT = 0.0010
OPTION_SLIPPAGE_PCT = 0.0025
NNB_HALF_SPREAD_POINTS = 0.50
NNB_SLIPPAGE_POINTS = 0.25


def commission(segment: str, side: str, turnover: float, on: date, *, intraday: bool = False) -> float:
    """Brokerage + statutory charges for one fill."""
    buy = side == "BUY"
    if segment == "eq":
        if intraday:
            stt = 0.0 if buy else 0.00025 * turnover
            stamp = 0.00003 * turnover if buy else 0.0
            dp = 0.0
        else:
            stt = 0.001 * turnover
            stamp = 0.00015 * turnover if buy else 0.0
            dp = 0.0 if buy else DP_CHARGE_PER_SELL
        exch = 0.0000297 * turnover
    elif segment == "index_fut":
        stt = 0.0 if buy else _futures_stt(on) * turnover
        stamp = 0.00002 * turnover if buy else 0.0
        exch = 0.0000173 * turnover
        dp = 0.0
    elif segment == "mcx_fut":
        stt = 0.0 if buy else 0.0001 * turnover  # CTT, non-agri
        stamp = 0.00002 * turnover if buy else 0.0
        exch = 0.000021 * turnover
        dp = 0.0
    else:
        raise ValueError(segment)
    sebi = SEBI * turnover
    gst = GST * (BROKERAGE_PER_ORDER + exch + sebi)
    return BROKERAGE_PER_ORDER + stt + stamp + exch + sebi + gst + dp


def friction(segment: str, turnover: float) -> tuple[float, float]:
    """(spread_cost, slippage_cost) for one fill."""
    f = FRICTION[segment]
    return f.half_spread_pct * turnover, f.slippage_pct * turnover


def describe() -> list[tuple[str, str, str, str]]:
    """(segment, commission & levies, half-spread/side, slippage/side) rows for the report."""
    pct = lambda x: f"{x * 100:.4g}%"
    return [
        ("NSE cash equity", "Rs 20/order; STT 0.1% both sides (delivery) or 0.025% sell (intraday); "
         "exch 0.00297%; SEBI 0.0001%; stamp 0.015% buy (delivery) / 0.003% (intraday); "
         "GST 18%; DP Rs 15.93 per delivery sell", pct(FRICTION['eq'].half_spread_pct), pct(FRICTION['eq'].slippage_pct)),
        ("NSE index futures", "Rs 20/order; STT sell 0.0125% (<Oct-24) / 0.02% (<Apr-26) / 0.05%; "
         "exch 0.00173%; stamp 0.002% buy; GST 18%", pct(FRICTION['index_fut'].half_spread_pct),
         pct(FRICTION['index_fut'].slippage_pct)),
        ("MCX futures", "Rs 20/order; CTT 0.01% sell; exch 0.0021%; stamp 0.002% buy; GST 18%",
         pct(FRICTION['mcx_fut'].half_spread_pct), pct(FRICTION['mcx_fut'].slippage_pct)),
        ("NSE index options", "Rs 20/order; STT 0.1% sell (0.15% from Apr-26); exch 0.03503%; "
         "stamp 0.003% buy; GST 18% (backtest/charges.py)", pct(OPTION_HALF_SPREAD_PCT) + " of premium",
         pct(OPTION_SLIPPAGE_PCT) + " of premium"),
        ("NIFTY No Brainer legs", "as NSE index options", f"{NNB_HALF_SPREAD_POINTS} pt per leg",
         f"{NNB_SLIPPAGE_POINTS} pt per leg"),
    ]
