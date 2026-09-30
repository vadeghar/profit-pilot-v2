"""On-disk tick store for the scalping strategies (record once, backtest forever).

Layout (one folder per source and trading day):

    data/ticks/<source>/<YYYY-MM-DD>/instruments.json
    data/ticks/<source>/<YYYY-MM-DD>/ticks.csv        (while recording)
    data/ticks/<source>/<YYYY-MM-DD>/ticks.csv.gz     (after end-of-day compaction)

``source`` is ``angel`` (live Angel One SmartAPI WebSocket SnapQuote ticks) or
``breeze_1s`` (ICICI Breeze 1-second bars imported as pseudo-ticks). Every
row, whatever the source, has the columns in ``COLUMNS`` - see
docs/scalping/TICK_DATA.md for field semantics.
"""
from __future__ import annotations

import csv
import gzip
import json
import shutil
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional

import platform_config
from utils.timezone import IST

TICKS_DIR = Path(platform_config.DATA_DIR) / "ticks"
COLUMNS = ["ts", "token", "ltp", "ltq", "volume", "oi", "bid", "ask", "bid_qty", "ask_qty", "atp", "tbq", "tsq", "ltt"]
SOURCES = ("angel", "breeze_1s")


@dataclass
class Instrument:
    token: str
    symbol: str
    kind: str              # IDX | FUT | CE | PE
    strike: float = 0.0
    lot: int = 0
    expiry: str = ""
    exchange: str = "NFO"  # NSE (index) | NFO


@dataclass
class Tick:
    ts: datetime           # exchange timestamp, IST-aware
    token: str
    ltp: float
    ltq: int = 0           # last traded quantity (breeze_1s: that second's volume)
    volume: int = 0        # cumulative traded volume for the day
    oi: float = 0.0        # open interest (0 for the index)
    bid: float = 0.0       # best bid (0 when the source has no quotes)
    ask: float = 0.0       # best ask
    bid_qty: int = 0
    ask_qty: int = 0
    atp: float = 0.0       # average traded price for the day
    tbq: float = 0.0       # total pending buy quantity
    tsq: float = 0.0       # total pending sell quantity
    ltt: int = 0           # last traded time (epoch seconds, as sent by the exchange)

    def row(self) -> list:
        return [self.ts.isoformat(timespec="milliseconds"), self.token, self.ltp, self.ltq, self.volume, self.oi,
                self.bid, self.ask, self.bid_qty, self.ask_qty, self.atp, self.tbq, self.tsq, self.ltt]


def day_dir(source: str, day: date, root: Optional[Path] = None) -> Path:
    return Path(root or TICKS_DIR) / source / day.isoformat()


class TickWriter:
    """Append-only, thread-safe CSV writer for one source/day (survives restarts: appends)."""

    def __init__(self, source: str, day: date, root: Optional[Path] = None, flush_every: int = 500):
        self.dir = day_dir(source, day, root)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "ticks.csv"
        gz = self.dir / "ticks.csv.gz"
        if gz.exists() and not self.path.exists():  # reopened after compaction: continue in plain CSV
            with gzip.open(gz, "rt", newline="") as src, self.path.open("w", newline="") as dst:
                shutil.copyfileobj(src, dst)
            gz.unlink()
        self._fh = self.path.open("a", newline="")
        self._w = csv.writer(self._fh)
        if self._fh.tell() == 0:
            self._w.writerow(COLUMNS)
        self._lock = threading.Lock()
        self._pending = 0
        self.flush_every = flush_every
        self.rows = 0

    def write_instruments(self, instruments: dict[str, Instrument], meta: dict) -> None:
        path = self.dir / "instruments.json"
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        merged = {**existing.get("instruments", {}), **{t: asdict(i) for t, i in instruments.items()}}
        path.write_text(json.dumps({**existing, **meta, "instruments": merged}, indent=1), encoding="utf-8")

    def write(self, tick: Tick) -> None:
        with self._lock:
            self._w.writerow(tick.row())
            self.rows += 1
            self._pending += 1
            if self._pending >= self.flush_every:
                self._fh.flush()
                self._pending = 0

    def close(self, compress: bool = False) -> None:
        with self._lock:
            self._fh.flush()
            self._fh.close()
        if compress:
            compress_day(self.dir)


def compress_day(folder: Path) -> Optional[Path]:
    src = folder / "ticks.csv"
    if not src.exists():
        return None
    dst = folder / "ticks.csv.gz"
    with src.open("rb") as f_in, gzip.open(dst, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)
    src.unlink()
    return dst


def load_instruments(source: str, day: date, root: Optional[Path] = None) -> tuple[dict[str, Instrument], dict]:
    raw = json.loads((day_dir(source, day, root) / "instruments.json").read_text(encoding="utf-8"))
    insts = {t: Instrument(**v) for t, v in raw.get("instruments", {}).items()}
    return insts, {k: v for k, v in raw.items() if k != "instruments"}


def _num(v: str, cast=float):
    try:
        return cast(float(v)) if v not in ("", None) else cast(0)
    except ValueError:
        return cast(0)


def read_ticks(source: str, day: date, root: Optional[Path] = None) -> Iterator[Tick]:
    """Ticks for one day in timestamp order (rows are written in arrival order, so re-sort)."""
    folder = day_dir(source, day, root)
    path = folder / "ticks.csv"
    opener = (lambda: path.open(newline="")) if path.exists() else \
        (lambda: gzip.open(folder / "ticks.csv.gz", "rt", newline=""))
    rows = []
    with opener() as fh:
        for r in csv.DictReader(fh):
            try:
                ts = datetime.fromisoformat(r["ts"])
            except (KeyError, ValueError):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=IST)
            rows.append(Tick(ts, r["token"], _num(r["ltp"]), _num(r["ltq"], int), _num(r["volume"], int),
                             _num(r["oi"]), _num(r["bid"]), _num(r["ask"]), _num(r.get("bid_qty"), int),
                             _num(r.get("ask_qty"), int), _num(r.get("atp")), _num(r.get("tbq")),
                             _num(r.get("tsq")), _num(r.get("ltt"), int)))
    rows.sort(key=lambda t: t.ts)
    return iter(rows)


def list_days(root: Optional[Path] = None) -> list[dict]:
    """Every recorded/imported day: [{date, source, size_bytes, instruments, compressed}]."""
    base = Path(root or TICKS_DIR)
    out = []
    for source in SOURCES:
        for folder in sorted((base / source).glob("????-??-??")) if (base / source).exists() else []:
            files = [f for f in (folder / "ticks.csv", folder / "ticks.csv.gz") if f.exists()]
            if not files or not (folder / "instruments.json").exists():
                continue
            try:
                n_inst = len(json.loads((folder / "instruments.json").read_text(encoding="utf-8")).get("instruments", {}))
            except ValueError:
                n_inst = 0
            out.append({"date": folder.name, "source": source, "size_bytes": sum(f.stat().st_size for f in files),
                        "instruments": n_inst, "compressed": files[0].suffix == ".gz"})
    return out


def best_source(day: date, root: Optional[Path] = None) -> Optional[str]:
    """Prefer real Angel ticks over Breeze 1-second pseudo-ticks for a day."""
    for source in SOURCES:
        folder = day_dir(source, day, root)
        if (folder / "instruments.json").exists() and \
                ((folder / "ticks.csv").exists() or (folder / "ticks.csv.gz").exists()):
            return source
    return None
