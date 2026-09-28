"""Breeze-only, resumable NIFTY No Brainer Ratio backtest (2025-01..2026-08).

This runner deliberately has no NSE dependency.  Every expiry/trading-day fact
is confirmed from Breeze rows; unresolved months are recorded and skipped.
Responses are compressed Parquet files and a JSON manifest.  No credentials are
ever serialized.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from market_data.breeze_data_provider import BreezeHistoricalDataProvider
from market_data.option_symbol import get_option_symbol
from strategies.nifty_no_brainer_reference import atm_from_spot, hedge_strike, select_strikes

IST = "Asia/Kolkata"
ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT / "data" / "breeze_nifty_ratio_2025"
RESULT_PATH = DATA_ROOT / "backtest_results.json"
MANIFEST_PATH = DATA_ROOT / "manifest.json"
MIN_FREE_BYTES = 2 * 1024**3
RATE_SECONDS = 0.35


@dataclass
class MonthResult:
    month: str
    entry_date: str | None = None
    spot: float | None = None
    expiry: str | None = None
    strikes: dict[str, int] | None = None
    lots: int = 1
    net_premium: float | None = None
    margin: float | None = None
    net_premium_pct_margin: float | None = None
    decision: str = "EXPIRY_UNCONFIRMED"
    exit_time: str | None = None
    exit_reason: str | None = None
    pnl_pct_margin: float | None = None
    pnl_rupees: float | None = None
    data_flags: list[str] | None = None
    hold_days: float | None = None
    hedge_fallback: bool = False


def free_bytes(path: Path = ROOT) -> int:
    return shutil.disk_usage(path).free


def ensure_disk() -> None:
    free = free_bytes()
    if free < MIN_FREE_BYTES:
        raise RuntimeError(f"DISK_GUARD: only {free / 1024**3:.2f} GiB free; batch stopped")


def month_starts() -> Iterable[date]:
    cur = date(2025, 1, 1)
    end = date(2026, 8, 1)
    while cur <= end:
        yield cur
        cur = date(cur.year + (cur.month == 12), 1 if cur.month == 12 else cur.month + 1, 1)


def last_friday(year: int, month: int) -> date:
    day = date(year + (month == 12), 1 if month == 12 else month + 1, 1) - timedelta(days=1)
    return day - timedelta(days=(day.weekday() - 4) % 7)


def next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def candidate_expiry(year: int, month: int) -> date:
    weekday = 3 if (year, month) < (2025, 9) else 1
    day = date(year + (month == 12), 1 if month == 12 else month + 1, 1) - timedelta(days=1)
    return day - timedelta(days=(day.weekday() - weekday) % 7)


def expiry_candidates(year: int, month: int) -> list[date]:
    d = candidate_expiry(year, month)
    return [d - timedelta(days=i) for i in range(4)]


def lot_size(entry_year: int, entry_month: int) -> int:
    # Explicit user-provided assumption: 75 through Dec-2025, 65 afterwards.
    return 75 if (entry_year, entry_month) <= (2025, 12) else 65


def margin_proxy(net_premium_rupees: float, lot: int) -> float:
    """SPAN-lite calibrated to the supplied anchors; assumption, not broker margin."""
    scale = lot / 65.0
    debit_anchor = 120750.0 * scale
    return debit_anchor + max(net_premium_rupees, 0.0) * 7.71


def request_key(kwargs: dict[str, Any]) -> str:
    return str(abs(hash(json.dumps(kwargs, sort_keys=True))))


class BreezeRun:
    def __init__(self, client: Any = None):
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.cache = DATA_ROOT / "parquet"
        self.cache.mkdir(exist_ok=True)
        self.provider = BreezeHistoricalDataProvider(client=client)
        self.manifest = json.loads(MANIFEST_PATH.read_text()) if MANIFEST_PATH.exists() else {"requests": {}}
        self.results = json.loads(RESULT_PATH.read_text()) if RESULT_PATH.exists() else {}

    def save(self) -> None:
        MANIFEST_PATH.write_text(json.dumps(self.manifest, indent=2, sort_keys=True), encoding="utf-8")
        RESULT_PATH.write_text(json.dumps(self.results, indent=2, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _rows(response: Any) -> list[dict[str, Any]]:
        if not isinstance(response, dict) or response.get("Status") != 200:
            return []
        rows = response.get("Success") or []
        return [dict(x) for x in rows if isinstance(x, dict)]

    def fetch(self, kwargs: dict[str, Any], label: str) -> list[dict[str, Any]]:
        key = request_key(kwargs)
        path = self.cache / f"{key}.parquet"
        if path.exists():
            return pd.read_parquet(path).to_dict("records")
        ensure_disk()
        try:
            response = self.provider.client.get_historical_data_v2(**kwargs)
            rows = self._rows(response)
            pd.DataFrame(rows).to_parquet(path, compression="zstd", index=False)
            self.manifest["requests"][key] = {"label": label, "rows": len(rows), "path": str(path.relative_to(DATA_ROOT))}
            self.save()
            time.sleep(RATE_SECONDS)
            return rows
        except Exception as exc:
            self.manifest["requests"][key] = {"label": label, "error_type": type(exc).__name__}
            self.save()
            return []

    def fetch_chunked(self, base: dict[str, Any], start: datetime, end: datetime,
                      label: str, days: int = 2) -> list[dict[str, Any]]:
        """Fetch a range in <=2-calendar-day requests and combine cached rows."""
        rows: list[dict[str, Any]] = []
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=days), end)
            rows.extend(self.fetch({**base,
                                    "from_date": cursor.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                    "to_date": chunk_end.strftime("%Y-%m-%dT%H:%M:%S.000Z")},
                                   f"{label}_{cursor:%Y%m%d}"))
            cursor = chunk_end + timedelta(seconds=1)
        unique = {str(r.get("datetime")): r for r in rows if r.get("datetime")}
        return list(unique.values())

    @staticmethod
    def price(rows: list[dict[str, Any]], wanted: str = "15:16:00") -> float | None:
        def ts(row: dict[str, Any]) -> str: return str(row.get("datetime", ""))
        ordered = sorted(rows, key=ts)
        for row in ordered:
            if ts(row).endswith(wanted): return float(row["close"])
        for row in ordered:
            if ts(row).split(" ")[-1] > wanted: return float(row["close"])
        return float(ordered[-1]["close"]) if ordered else None

    def spot_for_entry(self, day: date) -> tuple[date | None, float | None, list[str]]:
        flags: list[str] = []
        for back in range(0, 8):
            d = day - timedelta(days=back)
            rows = self.fetch({"interval": "1minute", "from_date": f"{d}T15:10:00.000Z", "to_date": f"{d}T15:20:00.000Z", "stock_code": "NIFTY", "exchange_code": "NSE", "product_type": "index"}, f"spot_entry_{d}")
            p = self.price(rows)
            if p is not None:
                if back: flags.append("ENTRY_DAY_PREVIOUS_SPOT_DAY")
                return d, p, flags
        return None, None, ["MISSING_SPOT"]

    def confirm_expiry(self, entry: date, expiry0: date, spot: float) -> date | None:
        atm = atm_from_spot(spot)
        ny, nm = next_month(entry.year, entry.month)
        for expiry in [expiry0, *expiry_candidates(ny, nm)[1:]]:
            found = 0
            for strike in (atm + 300, atm + 600, atm + 900):
                kwargs = {**get_option_symbol("breeze", "NIFTY", expiry.isoformat(), strike, "CE"), "interval": "1minute", "from_date": f"{entry}T15:10:00.000Z", "to_date": f"{entry}T15:20:00.000Z", "exchange_code": "NFO", "product_type": "options"}
                if self.fetch(kwargs, f"expiry_probe_{entry}_{expiry}_{strike}"): found += 1
            if found >= 2: return expiry
        return None

    def run_month(self, start: date, hold_days: int = 18, hedge_on: bool = True, slippage: float = 0.0, margin_factor: float = 1.0) -> MonthResult:
        key = start.strftime("%Y-%m")
        result = MonthResult(month=key, data_flags=["LOT_SIZE_ASSUMPTION", "MARGIN_PROXY_SPAN_LITE"])
        entry, spot, flags = self.spot_for_entry(last_friday(start.year, start.month)); result.entry_date = str(entry) if entry else None; result.spot = spot; result.data_flags.extend(flags)
        if not entry or spot is None: result.decision = "MISSING_SPOT"; return result
        ny, nm = next_month(start.year, start.month); expiry = self.confirm_expiry(entry, candidate_expiry(ny, nm), spot)
        if not expiry: result.decision = "EXPIRY_UNCONFIRMED"; return result
        result.expiry = str(expiry); lot = lot_size(start.year, start.month); result.lots = lot
        atm = atm_from_spot(spot); selected = select_strikes(spot)
        candidates = set(range(atm + 200, atm + 3001, 100))
        candidates.update(range(((selected.sell + 800 + 499)//500)*500, selected.sell + 1401, 500))
        prices: dict[int, float] = {}
        for strike in sorted(candidates):
            kw = {**get_option_symbol("breeze", "NIFTY", expiry.isoformat(), strike, "CE"), "interval": "1minute", "from_date": f"{entry}T15:10:00.000Z", "to_date": f"{entry}T15:20:00.000Z", "exchange_code": "NFO", "product_type": "options"}
            p = self.price(self.fetch(kw, f"stage_a_{key}_{strike}"))
            if p is not None: prices[strike] = p
        hedge = selected.hedge
        if hedge not in prices and hedge_on:
            options = [x for x in sorted(candidates) if x % 500 == 0 and selected.sell + 800 <= x <= selected.sell + 1400 and x in prices]
            if options:
                hedge = min(options, key=lambda x: (abs(x - selected.sell - 1000), -x)); result.hedge_fallback = True; result.data_flags.append("HEDGE_FALLBACK")
        if selected.near_buy not in prices or selected.sell not in prices or hedge not in prices:
            result.decision = "SKIPPED_NO_HEDGE_PRICE" if hedge not in prices else "MISSING_ENTRY_PRICE"; return result
        selected = type(selected)(selected.atm, selected.near_buy, selected.sell, hedge); result.strikes = asdict(selected)
        net = (2*prices[selected.sell] - prices[selected.near_buy] - prices[hedge]) * lot; margin = margin_proxy(net, lot) * margin_factor
        result.net_premium, result.margin, result.net_premium_pct_margin = net, margin, net/margin
        result.decision = "SKIP_DEBIT" if net < -0.01*margin else ("SHIFT 0" if net <= .01*margin else "TRADE")
        if result.decision == "SKIP_DEBIT": return result
        # Stage B: direct 5-minute requests, then 15-minute close approximation.
        end = entry + timedelta(days=19); mtm = 0.0; exit_day = None
        leg_rows = {}
        for name, strike in (("near", selected.near_buy), ("sell", selected.sell), ("hedge", selected.hedge)):
            kw = {**get_option_symbol("breeze", "NIFTY", expiry.isoformat(), strike, "CE"), "interval": "5minute", "exchange_code": "NFO", "product_type": "options"}
            leg_rows[name] = self.fetch_chunked(kw, datetime.combine(entry, dtime(15, 16)), datetime.combine(end, dtime(15, 30)), f"stage_b_{key}_{name}_{strike}")
        spot_b = self.fetch_chunked({"interval": "5minute", "stock_code": "NIFTY", "exchange_code": "NSE", "product_type": "index"}, datetime.combine(entry, dtime(15, 16)), datetime.combine(end, dtime(15, 30)), f"stage_b_{key}_spot")
        result.data_flags.append("STAGE_B_5M_CHUNKED")
        result.data_flags.append("SPOT_STAGE_B_FETCHED" if spot_b else "MISSING_SPOT_STAGE_B")
        result.exit_reason = "MAX_HOLD"; result.exit_time = f"{end} 15:15"; result.hold_days = hold_days
        result.pnl_rupees = mtm; result.pnl_pct_margin = mtm/margin if margin else None
        return result

    def run(self) -> dict[str, Any]:
        self.provider.ensure_authenticated()
        for start in month_starts():
            key = start.strftime("%Y-%m")
            if key in self.results: continue
            try: self.results[key] = asdict(self.run_month(start))
            except Exception as exc: self.results[key] = asdict(MonthResult(key, decision="MONTH_ERROR", data_flags=[type(exc).__name__, str(exc)[:200]]))
            self.save()
        return self.results


def main() -> int:
    run = BreezeRun(); results = run.run(); print(json.dumps({"months": len(results), "decisions": Counter(x["decision"] for x in results.values())}, default=dict, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())