"""Resumable REAL-data loader for the NIFTY No Brainer backtest.

Only Breeze is used. Contract parameters are produced by the existing
``market_data.option_symbol.get_option_symbol`` utility. CSV is used instead
of Parquet because this environment does not guarantee a parquet engine.
"""
from __future__ import annotations

import csv
import hashlib
import json
import time as sleep_time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from market_data.option_symbol import get_option_symbol
from market_data.breeze_data_provider import BreezeHistoricalDataProvider
from strategies.nifty_no_brainer_reference import select_strikes
from utils.timezone import IST, ensure_ist, parse_broker_timestamp


INTERVAL = "1minute"
SAFE_CANDLES = 900
SAFE_CHUNK_DAYS = 2
DEFAULT_RATE_SECONDS = 0.65


@dataclass
class ManifestEntry:
    key: str
    kind: str
    request: dict[str, Any]
    path: str
    row_count: int = 0
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    sha256: str | None = None
    fetched_at: str | None = None
    status: str = "PENDING"
    error: str | None = None


class BreezeNiftyLoader:
    def __init__(self, client: Any = None, root: str | Path = "data/breeze_nifty_options",
                 rate_seconds: float = DEFAULT_RATE_SECONDS, retries: int = 3):
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.normalized_dir = self.root / "normalized"
        self.manifest_path = self.root / "manifest.json"
        for path in (self.raw_dir, self.normalized_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.provider = BreezeHistoricalDataProvider(client=client)
        self.rate_seconds = rate_seconds
        self.retries = retries
        self.calls = 0
        self.rate_limit_incidents = 0
        self.manifest = self._read_manifest()

    def _read_manifest(self) -> dict[str, Any]:
        if self.manifest_path.exists():
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return {"version": 1, "source": "ICICI Breeze", "entries": {}, "calls": 0,
                "rate_limit_incidents": 0}

    def _save_manifest(self) -> None:
        self.manifest["calls"] = self.calls + int(self.manifest.get("calls", 0))
        self.manifest["rate_limit_incidents"] = self.rate_limit_incidents + int(self.manifest.get("rate_limit_incidents", 0))
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2, sort_keys=True), encoding="utf-8")
        self.calls = 0
        self.rate_limit_incidents = 0

    @staticmethod
    def _key(kind: str, request: Mapping[str, Any]) -> str:
        body = json.dumps({"kind": kind, **dict(request)}, sort_keys=True)
        return hashlib.sha256(body.encode()).hexdigest()[:24]

    @staticmethod
    def _date_string(dt: datetime) -> str:
        return ensure_ist(dt).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    @staticmethod
    def _rows(response: Any) -> list[dict[str, Any]]:
        if not isinstance(response, dict):
            raise ValueError(f"unexpected Breeze response type: {type(response).__name__}")
        if response.get("Status") != 200:
            raise RuntimeError(f"Breeze status={response.get('Status')} error={response.get('Error')}")
        rows = response.get("Success") or []
        if isinstance(rows, dict):
            rows = rows.get("candles", rows.get("data", []))
        if not isinstance(rows, list):
            raise ValueError("Breeze Success is not a list")
        return [dict(row) for row in rows if isinstance(row, dict)]

    def _call(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                self.calls += 1
                response = self.provider.client.get_historical_data_v2(**request)
                rows = self._rows(response)
                sleep_time.sleep(self.rate_seconds)
                return rows
            except Exception as exc:
                last = exc
                text = str(exc).lower()
                if "rate" in text or "429" in text or "limit" in text:
                    self.rate_limit_incidents += 1
                if attempt + 1 < self.retries:
                    sleep_time.sleep(self.rate_seconds * (2 ** attempt))
        raise RuntimeError(str(last)) from last

    def _write_rows(self, entry: ManifestEntry, rows: Sequence[dict[str, Any]]) -> ManifestEntry:
        path = self.root / entry.path
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = sorted({key for row in rows for key in row}) or ["datetime"]
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        timestamps = [str(row.get("datetime")) for row in rows if row.get("datetime")]
        entry.row_count = len(rows)
        entry.first_timestamp = min(timestamps) if timestamps else None
        entry.last_timestamp = max(timestamps) if timestamps else None
        entry.sha256 = digest
        entry.fetched_at = datetime.now(IST).isoformat()
        entry.status = "READY" if rows else "EMPTY"
        return entry

    def fetch_contract(self, request: dict[str, Any], start: datetime, end: datetime,
                       kind: str = "option") -> ManifestEntry:
        request = dict(request)
        key = self._key(kind, {**request, "start": start.isoformat(), "end": end.isoformat()})
        path = f"normalized/{key}.csv"
        existing = self.manifest["entries"].get(key)
        if existing and existing.get("status") in {"READY", "EMPTY"} and (self.root / existing["path"]).exists():
            return ManifestEntry(**existing)
        entry = ManifestEntry(key, kind, {**request, "start": start.isoformat(), "end": end.isoformat()}, path)
        rows: list[dict[str, Any]] = []
        cursor = ensure_ist(start)
        while cursor < ensure_ist(end):
            chunk_end = min(cursor + timedelta(days=SAFE_CHUNK_DAYS), ensure_ist(end))
            call = {**request, "interval": INTERVAL, "from_date": self._date_string(cursor),
                    "to_date": self._date_string(chunk_end)}
            try:
                rows.extend(self._call(call))
            except Exception as exc:
                entry.status, entry.error = "ERROR", str(exc)[:500]
                self.manifest["entries"][key] = asdict(entry)
                self._save_manifest()
                raise
            cursor = chunk_end + timedelta(seconds=1)
        unique = {str(row.get("datetime")): row for row in rows if row.get("datetime")}
        entry = self._write_rows(entry, list(unique.values()))
        self.manifest["entries"][key] = asdict(entry)
        self._save_manifest()
        return entry

    def fetch_spot(self, start: datetime, end: datetime) -> ManifestEntry:
        return self.fetch_contract({"stock_code": "NIFTY", "exchange_code": "NSE",
                                    "product_type": "cash"}, start, end, "spot")

    def fetch_option(self, expiry: str, strike: int, start: datetime, end: datetime,
                     right: str = "CE") -> ManifestEntry:
        contract = get_option_symbol("breeze", "NIFTY", expiry, strike, right)
        return self.fetch_contract({**contract, "exchange_code": "NFO", "product_type": "options"},
                                   start, end, "option")

    def authenticate(self) -> None:
        self.provider.ensure_authenticated()


def entry_day_last_friday(year: int, month: int, holidays: Iterable[date]) -> date:
    last = date(year + (month == 12), 1 if month == 12 else month + 1, 1) - timedelta(days=1)
    while last.weekday() != 4:
        last -= timedelta(days=1)
    holiday_set = set(holidays)
    while last.weekday() >= 5 or last in holiday_set:
        last -= timedelta(days=1)
    return last


def estimate_api_calls(months: int, stage_a_contracts: int,
                       stage_b_contracts: int, hold_days: int = 19) -> int:
    stage_a = months * (1 + stage_a_contracts)  # spot + candidates, one entry-day request
    stage_b = months * (1 + stage_b_contracts) * ((hold_days + SAFE_CHUNK_DAYS - 1) // SAFE_CHUNK_DAYS)
    return stage_a + stage_b