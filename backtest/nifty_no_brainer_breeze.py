"""On-demand, no-candle-cache Breeze runner for the NIFTY No Brainer ratio."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping

from market_data.breeze_data_provider import BreezeHistoricalDataProvider
from market_data.normalize import NormalizedCandle, ensure_normalized_candles
from market_data.option_symbol import get_option_symbol
from strategies.nifty_no_brainer_reference import entry_decision, exit_evaluator, select_strikes
from utils.timezone import IST, ensure_ist


@dataclass
class MonthResult:
    month: str
    entry_date: str | None = None
    spot: float | None = None
    atm: int | None = None
    expiry: str | None = None
    lot_size: int | None = None
    strikes: dict[str, int] = field(default_factory=dict)
    net_premium_pct_margin: float | None = None
    decision: str = "UNTESTED"
    entry_time: str | None = None
    entry_prices: dict[str, float] = field(default_factory=dict)
    exit_time: str | None = None
    exit_reason: str | None = None
    pnl_pct_margin: float | None = None
    pnl_rupees: float | None = None
    flags: list[str] = field(default_factory=list)


def load_expiry_metadata(path: str | Path = "nifty_expiries.json") -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data.get("monthly_expiries"), dict):
        raise ValueError("expiry JSON must contain monthly_expiries")
    return data


def resolve_unconfirmed_expiries(provider: Any, path: str | Path = "nifty_expiries.json") -> dict[str, Any]:
    """Probe each unconfirmed monthly contract on its entry day and update JSON."""
    target = Path(path)
    data = load_expiry_metadata(target)
    for month, item in data["monthly_expiries"].items():
        if not str(item.get("status", "")).startswith("UNCONFIRMED"):
            continue
        year, mon = map(int, month.split("-"))
        previous = date(year - (mon == 1), 12 if mon == 1 else mon - 1, 1)
        entry_day = _last_friday(previous)
        expiry = date.fromisoformat(item["expiry"])
        probe = get_option_symbol("breeze", "NIFTY", expiry, 25000, "CE")
        try:
            rows = ensure_normalized_candles(provider.get_historical_candles(
                probe, "1m", datetime.combine(entry_day, time(15, 16), IST),
                datetime.combine(entry_day, time(15, 20), IST)), f"expiry probe {month}")
        except RuntimeError as exc:
            if "No Breeze data returned" not in str(exc):
                raise
            rows = []
        if rows:
            item["status"] = "CONFIRMED_BREEZE_PROBE"
            item["confirmed_by"] = {"probe_strike": 25000, "entry_day": entry_day.isoformat(),
                                    "first_row": rows[0].timestamp.isoformat()}
        else:
            item["status"] = "UNCONFIRMED_NO_ROWS"
            item["confirmed_by"] = {"probe_strike": 25000, "entry_day": entry_day.isoformat()}
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def _months(start: date, end: date):
    cur = date(start.year, start.month, 1)
    while cur <= end:
        yield cur
        cur = date(cur.year + (cur.month == 12), 1 if cur.month == 12 else cur.month + 1, 1)


def _last_friday(month: date) -> date:
    next_month = date(month.year + (month.month == 12), 1 if month.month == 12 else month.month + 1, 1)
    d = next_month - timedelta(days=1)
    while d.weekday() != 4:
        d -= timedelta(days=1)
    return d


def build_breeze_option_request(expiry: date, strike: int) -> dict[str, str]:
    """Build the canonical Breeze option fields used by this runner.

    Keeping this at the runner boundary makes every entry/lifecycle request
    auditable without duplicating the broker-specific serialization rules.
    """
    request = get_option_symbol("breeze", "NIFTY", expiry, strike, "CE")
    if not isinstance(request, dict):  # Defensive: the broker dispatcher contract.
        raise TypeError("Breeze option request builder must return a mapping")
    return request


def _candle_at(candles: list[NormalizedCandle], day: date, start: time, end: time):
    return [c for c in candles if c.timestamp.date() == day and start <= c.timestamp.timetz().replace(tzinfo=None) <= end]


def _price_series(candles: list[NormalizedCandle]):
    return {c.timestamp: float(c.close) for c in candles}


def trace_monthly_option_requests(provider: Any, start: date, end: date,
                                  expiry_path: str | Path = "nifty_expiries.json") -> list[dict[str, Any]]:
    """Return request-preparation evidence without retaining candle payloads.

    This is intentionally separate from the backtest: it reads only the spot
    selection window and entry-price window, then keeps derived values/counts.
    """
    metadata = load_expiry_metadata(expiry_path)
    trace: list[dict[str, Any]] = []
    for month in _months(start, end):
        key = f"{month:%Y-%m}"
        next_key = f"{month.year + (month.month == 12):04d}-{1 if month.month == 12 else month.month + 1:02d}"
        row: dict[str, Any] = {"month": key, "expiry_month_used": next_key}
        meta = metadata["monthly_expiries"].get(next_key)
        if not meta:
            row.update(status="MISSING_EXPIRY_METADATA", assertion_errors=[
                f"No corrected JSON expiry for required month {next_key}",
            ])
            trace.append(row)
            continue

        expiry = date.fromisoformat(meta["expiry"])
        row["confirmed_expiry_date"] = meta["expiry"]
        errors: list[str] = []
        if f"{expiry:%Y-%m}" != next_key:
            errors.append("expiry is not in the entry month plus one")

        entry_day = _last_friday(month)
        window: list[NormalizedCandle] = []
        for back in range(8):
            candidate = entry_day - timedelta(days=back)
            try:
                spot_rows = ensure_normalized_candles(provider.get_historical_candles(
                    "NSE:NIFTY", "1m", datetime.combine(candidate, time(15, 10), IST),
                    datetime.combine(candidate, time(15, 15), IST)), "NIFTY spot")
            except RuntimeError as exc:
                if "No Breeze data returned" not in str(exc):
                    raise
                spot_rows = []
            window = _candle_at(spot_rows, candidate, time(15, 10), time(15, 15))
            if window:
                entry_day = candidate
                break
        if not window:
            row.update(status="NO_SPOT_ROWS", assertion_errors=errors + ["entry day is not a trading day"])
            trace.append(row)
            continue

        spot = float(window[-1].close)
        selected = select_strikes(spot)
        strikes = {"near_buy": selected.near_buy, "sell": selected.sell, "hedge": selected.hedge}
        row.update(entry_date=entry_day.isoformat(), spot=spot, atm=selected.atm, strikes=strikes)
        if any(strike % 100 for strike in strikes.values()):
            errors.append("option strike is not a 100 multiple")
        if strikes["hedge"] % 500:
            errors.append("hedge strike is not a 500 multiple")

        requests = {name: build_breeze_option_request(expiry, strike) for name, strike in strikes.items()}
        row["runner_option_requests"] = requests
        if any(request["strike_price"] != str(strikes[name]) for name, request in requests.items()):
            errors.append("strike_price was not serialized as an integer string")
        if any(request["right"] != "call" for request in requests.values()):
            errors.append("option right is not call")
        if any(not request["expiry_date"].endswith("T06:00:00.000Z") for request in requests.values()):
            errors.append("expiry timestamp is not Breeze Z format")

        counts: dict[str, int] = {}
        present_at_1516: dict[str, bool] = {}
        for name, request in requests.items():
            try:
                rows = ensure_normalized_candles(provider.get_historical_candles(
                    request, "1m", datetime.combine(entry_day, time(15, 16), IST),
                    datetime.combine(entry_day, time(15, 20), IST)), f"{name} entry")
            except RuntimeError as exc:
                if "No Breeze data returned" not in str(exc):
                    raise
                rows = []
            counts[name] = len(rows)
            present_at_1516[name] = any(
                candle.timestamp == datetime.combine(entry_day, time(15, 16), IST) for candle in rows
            )
        row["rows_returned_per_leg"] = counts
        row["candle_1516_present"] = present_at_1516
        if not counts["near_buy"]:
            errors.append("ATM-area contract returned no entry-day rows")
        row["assertion_errors"] = errors
        row["status"] = "PASS" if not errors else "FAIL"
        trace.append(row)
    return trace


def _margin(net_credit: float, lot_size: int, multiplier: float = 1.0) -> float:
    # Existing documented proxy: 700 points plus debit, per lot.
    debit = max(-net_credit, 0.0)
    return (700.0 + debit) * lot_size * multiplier


def run_monthly_backtest(provider: Any, start: date = date(2025, 1, 1), end: date | None = None,
                         capital: float = 1_000_000.0, expiry_path: str | Path = "nifty_expiries.json",
                         max_hold_days: int = 18, margin_multiplier: float = 1.0,
                         slippage_points: float = 0.0, costs_per_trade: float = 0.0,
                         trace_month: str | None = None) -> dict[str, Any]:
    """Fetch each month's candles into memory only, then discard them."""
    end = end or date.today().replace(day=1) - timedelta(days=1)
    metadata = load_expiry_metadata(expiry_path)
    results: list[MonthResult] = []
    for month in _months(start, end):
        key = f"{month:%Y-%m}"
        item = MonthResult(month=key)
        next_key = f"{month.year + (month.month == 12):04d}-{1 if month.month == 12 else month.month + 1:02d}"
        meta = metadata["monthly_expiries"].get(next_key)
        if not meta:
            item.flags.append(f"NO_NEXT_MONTH_EXPIRY_METADATA:{next_key}"); results.append(item); continue
        item.expiry = meta["expiry"]
        if meta.get("status", "").startswith("UNCONFIRMED"):
            item.flags.append(meta["status"])
        lot_size = next((x["lot_size"] for x in reversed(metadata["lot_size_effective"])
                         if x["effective_from"] <= f"{key}-01"), None)
        item.lot_size = lot_size
        entry_day = _last_friday(month)
        # The provider is configured by the caller; no raw payload escapes this scope.
        window = []
        for back in range(0, 8):
            probe_day = entry_day - timedelta(days=back)
            spot_rows = ensure_normalized_candles(provider.get_historical_candles(
                "NSE:NIFTY", "1m", datetime.combine(probe_day, time(15, 10), IST),
                datetime.combine(probe_day, time(15, 15), IST)), "NIFTY spot")
            window = _candle_at(spot_rows, probe_day, time(15, 10), time(15, 15))
            if window:
                if probe_day != entry_day:
                    item.flags.append("ENTRY_DAY_FALLBACK_PREVIOUS_TRADING_DAY")
                entry_day = probe_day
                break
        if not window:
            item.decision = "SKIPPED_NO_SPOT"; results.append(item); continue
        spot = float(window[-1].close); item.entry_date = entry_day.isoformat(); item.spot = spot
        selected = select_strikes(spot); item.atm = selected.atm
        item.strikes = {"near_buy": selected.near_buy, "sell": selected.sell, "hedge": selected.hedge}
        expiry = date.fromisoformat(item.expiry)
        symbols = {name: build_breeze_option_request(expiry, strike)
                   for name, strike in item.strikes.items()}
        entry_prices: dict[str, float] = {}
        streams: dict[str, list[NormalizedCandle]] = {}
        for name, symbol in symbols.items():
            rows = ensure_normalized_candles(provider.get_historical_candles(
                symbol, "1m", datetime.combine(entry_day, time(15, 16), IST),
                datetime.combine(entry_day, time(15, 20), IST)), f"{name} entry")
            if not rows:
                item.decision = "SKIPPED_NO_ENTRY_PRICE"; item.flags.append(f"MISSING_{name.upper()}"); break
            entry_prices[name] = float(rows[0].close) + slippage_points * (1 if name != "sell" else -1)
        if item.decision == "SKIPPED_NO_ENTRY_PRICE": results.append(item); continue
        item.entry_prices = entry_prices
        margin = _margin(2 * entry_prices["sell"] - entry_prices["near_buy"] - entry_prices["hedge"], lot_size, margin_multiplier)
        decision = entry_decision(spot, entry_prices, margin, lot_size=lot_size)
        item.decision, item.net_premium_pct_margin = decision.decision, decision.net_premium_pct
        if decision.decision != "TRADE": results.append(item); continue
        item.entry_time = datetime.combine(entry_day, time(15, 16), IST).isoformat()
        hold_end = min(expiry, entry_day + timedelta(days=max_hold_days))
        for name, symbol in symbols.items():
            streams[name] = ensure_normalized_candles(provider.get_historical_candles(
                symbol, "15m", datetime.combine(entry_day, time(15, 16), IST),
                datetime.combine(hold_end, time(15, 30), IST)), f"{name} lifecycle")
        by_name = {k: _price_series(v) for k, v in streams.items()}
        timestamps = sorted(set().union(*(set(v) for v in by_name.values())))
        last = dict(entry_prices); exit_reason = None; exit_ts = None; pnl = 0.0
        trace = []
        for ts in timestamps:
            if ts <= datetime.combine(entry_day, time(15, 16), IST): continue
            missing = False
            for name, series in by_name.items():
                if ts in series: last[name] = series[ts]
                else: missing = True
            if missing: item.flags.append("CARRIED_LAST_PRICE")
            pnl = ((last["near_buy"] - entry_prices["near_buy"]) + (last["hedge"] - entry_prices["hedge"]) - 2 * (last["sell"] - entry_prices["sell"])) * lot_size - costs_per_trade
            if key == trace_month:
                trace.append({"timestamp": ts.isoformat(), "prices": dict(last),
                              "mtm_rupees": pnl, "mtm_pct_margin": pnl / margin if margin else None})
            decision_exit = exit_evaluator(pnl, margin, datetime.combine(entry_day, time(15, 16), IST), ts, expiry, max_hold_days)
            if decision_exit.reason:
                exit_reason, exit_ts = decision_exit.reason, ts; break
        if exit_reason is None:
            exit_reason, exit_ts = "MAX_HOLD", datetime.combine(hold_end, time(15, 15), IST)
        item.exit_reason, item.exit_time = exit_reason, exit_ts.isoformat()
        item.pnl_rupees, item.pnl_pct_margin = pnl, pnl / margin if margin else None
        if key == trace_month:
            item.flags.append(json.dumps({"bar_trace": trace}, separators=(",", ":")))
        results.append(item)
    trades = [r for r in results if r.decision == "TRADE"]
    pnls = [r.pnl_rupees or 0.0 for r in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit, gross_loss = sum(wins), abs(sum(losses))
    return {"months": [asdict(r) for r in results], "summary": {
        "trades": len(trades), "win_rate": len(wins) / len(trades) if trades else 0.0,
        "expectancy": sum(pnls) / len(pnls) if pnls else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "total_pnl": sum(pnls), "margin_method": "700-point documented proxy",
        "exit_reason_split": {reason: sum(1 for r in trades if r.exit_reason == reason)
                              for reason in sorted({r.exit_reason for r in trades})}
    }}


def run_breeze_monthly_backtest(**kwargs):
    provider = kwargs.pop("provider", None) or BreezeHistoricalDataProvider(persist_cache=False)
    provider.ensure_authenticated()
    return run_monthly_backtest(provider, **kwargs)


def run_variant_matrix(provider: Any, **kwargs) -> dict[str, Any]:
    """Run the requested hold, margin, slippage, and cost sensitivity cases."""
    variants = {}
    for hold in (18, 19):
        for margin in (0.8, 1.0, 1.2):
            for slippage in (0.0, 1.0, 2.0):
                key = f"hold{hold}_margin{margin:g}_slip{slippage:g}"
                variants[key] = run_monthly_backtest(
                    provider, max_hold_days=hold, margin_multiplier=margin,
                    slippage_points=slippage, **kwargs,
                )
    return variants


def jan_2026_gap_up_check(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return the recorded Jan-30 entry and first Feb-3 result, if present."""
    row = next((r for r in report.get("months", []) if r.get("month") == "2026-01"), None)
    return {
        "entry_date": row.get("entry_date") if row else None,
        "exit_date": (row.get("exit_time") or "").split("T")[0] if row else None,
        "pnl_pct_margin": row.get("pnl_pct_margin") if row else None,
        "expected_reference_pct": -0.032,
        "status": "RECORDED_FOR_COMPARISON" if row else "NOT_AVAILABLE",
    }
