"""Own implementation of the exchange SPAN margin for NIFTY option portfolios.

Brokers' margin calculators (Breeze ``margin_calculator``, Zerodha, ...) all sit
on NSE Clearing's SPAN risk-parameter files.  NSE publishes those files daily
(public archive), so the margin can be reproduced - for **any past date** -
instead of guessing:

    https://nsearchives.nseindia.com/archives/nsccl/span/nsccl.<YYYYMMDD>.<i1..i5|s>.zip

Each zip holds one ``.spn`` (SPAN XML).  For every NIFTY option NSE publishes a
*risk array*: the loss per unit of a LONG position under 16 scenarios (price
moves of 0, +-1/3, +-2/3, +-1 of the price-scan range against volatility up /
down, plus two extreme moves already weighted at 35%).  For a portfolio:

    scenario loss_i  = sum(signed_qty x RA_i)          (short qty is negative)
    scanning risk    = max_i loss_i, floored at 0
    SPAN requirement = scanning risk        (NIFTY short-option-minimum rate is 0
                                             and one expiry means no calendar
                                             spread charge)
    exposure margin  = rate x underlying x short units  (see ``exposure_mode``)

Intraday files (i1..i5) are re-issued through the day; ``SpanFileSource`` picks
the latest one published at/before the requested time using the HTTP
``Last-Modified`` header, so no file is downloaded just to inspect its age.
"""
from __future__ import annotations

import io
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, BinaryIO, Iterable, Mapping, Sequence

ARCHIVE_URL = "https://nsearchives.nseindia.com/archives/nsccl/span/nsccl.{day:%Y%m%d}.{tag}.zip"
INTRADAY_TAGS = ("i1", "i2", "i3", "i4", "i5", "i6")
SCENARIOS = 16


class SpanError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpanOption:
    premium: float
    delta: float
    risk_array: tuple[float, ...]       # loss per unit for a LONG position, 16 scenarios


@dataclass
class SpanSnapshot:
    underlying: str
    created: str
    underlying_price: float
    price_scan: float
    vol_scan: float
    options: dict[tuple[date, str, float], SpanOption] = field(default_factory=dict)
    source: str = ""


def _f(elem: ET.Element | None, default: float = 0.0) -> float:
    return float(elem.text) if elem is not None and elem.text else default


def parse_span_xml(stream: BinaryIO, underlying: str = "NIFTY") -> SpanSnapshot:
    """Stream-parse a SPAN file, keeping only ``underlying``'s spot and option series."""
    snap = SpanSnapshot(underlying, "", 0.0, 0.0, 0.0)
    in_phy = in_oop = False
    code: str | None = None
    for event, el in ET.iterparse(stream, events=("start", "end")):
        tag = el.tag
        if event == "start":
            if tag == "phyPf":
                in_phy, code = True, None
            elif tag == "oopPf":
                in_oop, code = True, None
            continue
        if tag == "created" and not snap.created:
            snap.created = (el.text or "").strip()
        elif tag == "pfCode" and code is None and (in_phy or in_oop):
            code = (el.text or "").strip()
        elif tag == "phy" and in_phy and code == underlying:
            snap.underlying_price = _f(el.find("p"))
            rate = el.find("scanRate")
            if rate is not None:
                snap.price_scan, snap.vol_scan = _f(rate.find("priceScan")), _f(rate.find("volScan"))
        elif tag == "series" and in_oop:
            if code == underlying:
                expiry = datetime.strptime(el.findtext("pe", "").strip(), "%Y%m%d").date()
                for opt in el.findall("opt"):
                    ra = opt.find("ra")
                    values = tuple(float(a.text) for a in ra.findall("a")) if ra is not None else ()
                    if len(values) != SCENARIOS:
                        continue
                    key = (expiry, opt.findtext("o", "").strip().upper(), float(opt.findtext("k")))
                    snap.options[key] = SpanOption(_f(opt.find("p")), _f(opt.find("d")), values)
            el.clear()
        elif tag == "phyPf":
            in_phy = False
            el.clear()
        elif tag == "oopPf":
            in_oop = False
            el.clear()
        elif tag in ("futPf", "intercommSpread", "dSpread"):
            el.clear()
    if not snap.options:
        raise SpanError(f"no {underlying} option series found in SPAN file")
    return snap


# --------------------------------------------------------------------------- margin


@dataclass(frozen=True)
class Position:
    expiry: date
    right: str          # "C" | "P"
    strike: float
    quantity: int       # signed units: + long, - short


@dataclass(frozen=True)
class MarginBreakdown:
    scanning_risk: float
    exposure: float
    total: float
    worst_scenario: int
    scenario_losses: tuple[float, ...]


def scenario_losses(snapshot: SpanSnapshot, positions: Iterable[Position]) -> tuple[float, ...]:
    losses = [0.0] * SCENARIOS
    for pos in positions:
        opt = snapshot.options.get((pos.expiry, pos.right.upper(), float(pos.strike)))
        if opt is None:
            raise SpanError(f"{pos.expiry} {pos.strike:g}{pos.right} is not in the SPAN file ({snapshot.source})")
        for i, ra in enumerate(opt.risk_array):
            losses[i] += pos.quantity * ra
    return tuple(losses)


def span_margin(snapshot: SpanSnapshot, positions: Sequence[Position],
                exposure_rate: float = 0.0, exposure_mode: str = "none") -> MarginBreakdown:
    """SPAN requirement (+ optional exposure margin) for an option portfolio.

    ``exposure_mode``: ``none`` | ``net_short`` (short units net of long units,
    floored at 0) | ``gross_short`` (all short units).
    """
    losses = scenario_losses(snapshot, positions)
    risk = max(max(losses), 0.0)
    short = sum(-p.quantity for p in positions if p.quantity < 0)
    long_ = sum(p.quantity for p in positions if p.quantity > 0)
    units = {"none": 0, "gross_short": short, "net_short": max(short - long_, 0)}[exposure_mode]
    exposure = exposure_rate * snapshot.underlying_price * units
    return MarginBreakdown(risk, exposure, risk + exposure, losses.index(max(losses)), losses)


def structure_positions(expiry: date, near_buy: int, sell: int, hedge: int, quantity: int) -> list[Position]:
    """Buy 1 / sell 2 / buy 1 call structure; ``quantity`` is units per lot-set."""
    return [Position(expiry, "C", near_buy, quantity),
            Position(expiry, "C", sell, -2 * quantity),
            Position(expiry, "C", hedge, quantity)]


# --------------------------------------------------------------------------- files


class SpanFileSource:
    """Download + parse NSE SPAN files on demand; snapshots are memoized in memory."""

    def __init__(self, underlying: str = "NIFTY", timeout: float = 60.0,
                 opener=urllib.request.build_opener()):
        self.underlying, self.timeout, self._open = underlying, timeout, opener
        self._memo: dict[str, SpanSnapshot] = {}
        self.downloads = 0

    def _request(self, url: str, method: str = "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": "Mozilla/5.0"})
        return self._open.open(req, timeout=self.timeout)

    def published_at(self, day: date, tag: str) -> datetime | None:
        """Publish time from the HTTP header (HEAD request - no file download)."""
        try:
            with self._request(ARCHIVE_URL.format(day=day, tag=tag), "HEAD") as resp:
                stamp = resp.headers.get("Last-Modified")
        except Exception:  # noqa: BLE001 - a missing tag (404) simply is not a candidate
            return None
        return parsedate_to_datetime(stamp).astimezone(timezone.utc) if stamp else None

    def latest_tag_before(self, day: date, at: datetime) -> str | None:
        """Newest intraday file of ``day`` published at/before ``at`` (tz-aware)."""
        best: tuple[datetime, str] | None = None
        for tag in INTRADAY_TAGS:
            stamp = self.published_at(day, tag)
            if stamp and stamp <= at.astimezone(timezone.utc) and (best is None or stamp > best[0]):
                best = (stamp, tag)
        return best[1] if best else None

    def snapshot(self, day: date, at: datetime) -> SpanSnapshot:
        tag = self.latest_tag_before(day, at)
        if tag is None:
            raise SpanError(f"no NSE SPAN file for {day} published before {at.isoformat()}")
        url = ARCHIVE_URL.format(day=day, tag=tag)
        if url not in self._memo:
            with self._request(url) as resp:
                blob = resp.read()
            self.downloads += 1
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                name = next(n for n in zf.namelist() if n.lower().endswith(".spn"))
                with zf.open(name) as fh:
                    snap = parse_span_xml(fh, self.underlying)
            snap.source = url
            self._memo[url] = snap
        return self._memo[url]


class SpanMarginModel:
    """Per-set margin of the 1:-2:1 structure from the SPAN file valid at entry."""

    def __init__(self, source: SpanFileSource | None = None, exposure_rate: float = 0.0,
                 exposure_mode: str = "none"):
        self.source = source or SpanFileSource()
        self.exposure_rate, self.exposure_mode = exposure_rate, exposure_mode
        self.last: MarginBreakdown | None = None

    def per_set(self, entry_at: datetime, expiry: date, near_buy: int, sell: int, hedge: int,
                lot_size: int) -> float:
        snap = self.source.snapshot(entry_at.date(), entry_at)
        breakdown = span_margin(snap, structure_positions(expiry, near_buy, sell, hedge, lot_size),
                                self.exposure_rate, self.exposure_mode)
        self.last = breakdown
        return breakdown.total
