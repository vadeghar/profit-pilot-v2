"""Broker margin for the NIFTY No Brainer 1:-2:1 call structure (ICICI Breeze).

``Breeze.margin_calculator`` prices *live* contracts only, so it cannot be asked
about a contract that has already expired.  Two uses:

* live / paper: ``structure_margin`` for the exact legs about to be traded;
* backtest: ``calibrate_margin`` prices the structure the rules would pick
  *today* on the live monthly expiry and returns that broker figure as a
  fixed per-set margin to apply to every historical month (recorded in the
  report as an estimate - historical broker margin is not recoverable).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from backtest.nifty_no_brainer_breeze import build_breeze_option_request
from market_data.trading_days import TradingCalendar
from strategies.nifty_no_brainer_reference import monthly_expiry_from_calendar, select_strikes
from utils.timezone import IST


class MarginError(RuntimeError):
    pass


@dataclass(frozen=True)
class MarginQuote:
    margin: float            # rupees for one 1:-2:1 set
    method: str
    as_of: str
    structure: dict[str, Any]


def _leg(expiry: date, strike: int, action: str, quantity: int) -> dict[str, str]:
    req = build_breeze_option_request(expiry, strike)
    return {"strike_price": req["strike_price"], "quantity": str(quantity), "right": "call",
            "product": "options", "action": action, "price": "0",
            "expiry_date": req["expiry_date"], "stock_code": req["stock_code"],
            "cover_order_flag": "N", "fresh_order_type": "", "cover_limit_rate": "0",
            "cover_sltp_price": "0", "fresh_limit_rate": "0", "open_quantity": "0"}


def structure_margin(client: Any, expiry: date, near_buy: int, sell: int, hedge: int,
                     lot_size: int, sets: int = 1) -> MarginQuote:
    """Broker SPAN margin for buy 1 / sell 2 / buy 1 (quantities in units)."""
    legs = [_leg(expiry, near_buy, "buy", lot_size * sets),
            _leg(expiry, sell, "sell", 2 * lot_size * sets),
            _leg(expiry, hedge, "buy", lot_size * sets)]
    resp = client.margin_calculator(legs, "NFO")
    if not isinstance(resp, dict) or resp.get("Status") != 200 or not resp.get("Success"):
        err = resp.get("Error") if isinstance(resp, dict) else resp
        raise MarginError(f"Breeze margin_calculator failed: {err}")
    body = resp["Success"]
    margin = float(body["span_margin_required"]) + float(body.get("non_span_margin_required") or 0)
    if margin <= 0:
        raise MarginError(f"Breeze margin_calculator returned non-positive margin: {body}")
    return MarginQuote(
        margin / sets, "breeze_margin_calculator",
        datetime.now(IST).isoformat(timespec="seconds"),
        {"expiry": expiry.isoformat(), "near_buy": near_buy, "sell": sell, "hedge": hedge,
         "lot_size": lot_size, "span_margin_required": float(body["span_margin_required"]),
         "non_span_margin_required": float(body.get("non_span_margin_required") or 0)})


def calibrate_margin(provider: Any, lot_size: int = 65, today: date | None = None,
                     calendar: TradingCalendar | None = None) -> MarginQuote:
    """Price today's rule-selected structure on the live monthly expiry."""
    from market_data.trading_days import TradingDayFetcher
    calendar = calendar or TradingCalendar()
    today = today or datetime.now(IST).date()
    fetcher = TradingDayFetcher(provider, calendar)
    spot = last_day = None
    day = today
    for _ in range(5):  # latest available NIFTY bar (today's session may still be open)
        day = calendar.previous_trading_day(day)
        rows = fetcher.fetch("NSE:NIFTY", "1m", datetime.combine(day, time(9, 15), IST),
                             datetime.combine(day, time(15, 30), IST))
        if rows:
            spot, last_day = float(rows[-1].close), day
            break
        day -= timedelta(days=1)
    if spot is None:
        raise MarginError("no recent NIFTY spot bars to select the calibration structure")
    sel = select_strikes(spot)
    # The strategy always trades NEXT month's monthly expiry, never the series
    # about to expire (whose margin is inflated), so calibrate on that contract.
    nxt = date(today.year + (today.month == 12), 1 if today.month == 12 else today.month + 1, 1)
    expiry = monthly_expiry_from_calendar(nxt.year, nxt.month, calendar.holidays)
    last_error: Exception | None = None
    for candidate in (expiry, calendar.previous_trading_day(expiry - timedelta(days=1))):
        try:
            quote = structure_margin(provider.client, candidate, sel.near_buy, sel.sell, sel.hedge, lot_size)
        except MarginError as exc:
            last_error = exc
            continue
        return MarginQuote(quote.margin, "breeze_margin_calculator_calibrated_live_structure",
                           quote.as_of, {**quote.structure, "spot": spot, "calibrated_on": last_day.isoformat()})
    raise MarginError(str(last_error))


SPAN_EXPOSURE_RATE = 0.0      # set after calibrating against Breeze (see tests / calibrate script)
SPAN_EXPOSURE_MODE = "none"
PROXY_METHOD = "proxy: (700 points + debit) x lot size; NOT broker margin"


def margin_settings(provider: Any, mode: str = "calibrated", lot_size: int = 65) -> dict[str, Any]:
    """Resolve ``--margin`` / ``margin_mode`` into run_backtest kwargs.

    ``calibrated`` (default) asks Breeze; if the broker call fails the run falls
    back to the proxy *and says so* in ``margin_method``.  A number is used as a
    manual rupee margin per set.  ``proxy`` forces the old 700-point formula.
    """
    mode = str(mode or "calibrated").strip().lower()
    if mode == "proxy":
        return {"fixed_margin": None, "margin_method": PROXY_METHOD}
    if mode.startswith("span"):
        # own SPAN implementation on NSE's published risk files (brokers/span_margin.py)
        from brokers.span_margin import SpanMarginModel
        model = SpanMarginModel(exposure_rate=SPAN_EXPOSURE_RATE, exposure_mode=SPAN_EXPOSURE_MODE)
        return {"margin_fn": model.per_set, "margin_method":
                f"own SPAN from NSE risk-parameter files (exposure: {SPAN_EXPOSURE_MODE} @ {SPAN_EXPOSURE_RATE:.0%})"}
    if mode == "calibrated":
        try:
            q = calibrate_margin(provider, lot_size)
        except Exception as exc:  # noqa: BLE001 - reported, never silent
            return {"fixed_margin": None, "margin_method": f"{PROXY_METHOD} [FALLBACK: {exc}]"}
        st = q.structure
        return {"fixed_margin": q.margin,
                "margin_method": f"{q.method} (Rs {q.margin:,.2f}/set, structure {st['near_buy']}/{st['sell']}/"
                                 f"{st['hedge']} exp {st['expiry']} priced {q.as_of}; applied to all months)"}
    try:
        value = float(mode)
    except ValueError as exc:
        raise ValueError(f"margin mode must be proxy, calibrated or a rupee amount, got {mode!r}") from exc
    return {"fixed_margin": value, "margin_method": f"manual: Rs {value:,.2f} per set"}
