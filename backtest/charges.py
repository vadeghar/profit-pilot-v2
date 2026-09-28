"""Brokerage, taxes and exchange charges for NSE index-option round trips.

Rates (per rupee of premium turnover unless noted), checked Sep-2026:
  * brokerage  - flat rupees per executed order; depends on your ICICI Direct
                 plan (Neo plan Rs 20, MoneySaver Rs 45, default Rs 49 per lot,
                 standard up to Rs 95).  **Set ``brokerage_per_order`` to your plan.**
  * STT        - sell side only: 0.10% of premium, raised to 0.15% from 2026-04-01
                 (Union Budget 2026).
  * exchange   - NSE transaction charge 0.03503% of premium (both sides).
  * SEBI fee   - Rs 10 per crore = 0.0001% (both sides).
  * stamp duty - 0.003% of premium, buy side only.
  * GST        - 18% on brokerage + exchange charge + SEBI fee.
Not modelled: NSE IPFT (tiny) and STT on exercise (positions are squared off).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Sequence

STT_CHANGE_DATE = date(2026, 4, 1)


@dataclass(frozen=True)
class ChargeConfig:
    brokerage_per_order: float = 20.0
    stt_sell_rate_before: float = 0.0010
    stt_sell_rate_from_2026_04_01: float = 0.0015
    exchange_txn_rate: float = 0.0003503
    sebi_rate: float = 0.000001
    stamp_buy_rate: float = 0.00003
    gst_rate: float = 0.18

    @classmethod
    def zero(cls) -> "ChargeConfig":
        return cls(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True)
class Fill:
    side: str        # "BUY" | "SELL"
    price: float     # premium per unit
    quantity: int    # units (lots x lot size)


def option_charges(fills: Iterable[Fill], on: date, cfg: ChargeConfig | None = None) -> dict[str, float]:
    """Charges for a set of option fills executed on one day (one order per fill)."""
    cfg = cfg or ChargeConfig()
    fills = list(fills)
    buy = sum(f.price * f.quantity for f in fills if f.side == "BUY")
    sell = sum(f.price * f.quantity for f in fills if f.side == "SELL")
    turnover = buy + sell
    stt_rate = cfg.stt_sell_rate_from_2026_04_01 if on >= STT_CHANGE_DATE else cfg.stt_sell_rate_before
    brokerage = cfg.brokerage_per_order * len(fills)
    exchange = turnover * cfg.exchange_txn_rate
    sebi = turnover * cfg.sebi_rate
    out = {
        "brokerage": brokerage,
        "stt": sell * stt_rate,
        "exchange": exchange,
        "sebi": sebi,
        "stamp": buy * cfg.stamp_buy_rate,
        "gst": (brokerage + exchange + sebi) * cfg.gst_rate,
    }
    out["total"] = sum(out.values())
    return out


def add_charges(*parts: dict[str, float]) -> dict[str, float]:
    keys = ("brokerage", "stt", "exchange", "sebi", "stamp", "gst", "total")
    return {k: sum(p.get(k, 0.0) for p in parts) for k in keys}


def structure_fills(near_buy: float, sell: float, hedge: float, quantity: int, opening: bool) -> Sequence[Fill]:
    """The 1:-2:1 structure: opening buys near/hedge and sells 2x; closing reverses."""
    a, b = ("BUY", "SELL") if opening else ("SELL", "BUY")
    return (Fill(a, near_buy, quantity), Fill(b, sell, 2 * quantity), Fill(a, hedge, quantity))
