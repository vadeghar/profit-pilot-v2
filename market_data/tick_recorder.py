"""Tick hub: one Angel One SmartAPI WebSocket for NIFTY option ticks, recorded to
disk and fanned out to every running scalping strategy.

* Subscribes in SNAP_QUOTE mode (3) - the only WebSocket2 mode carrying open
  interest, last traded quantity and best-5 depth: NIFTY 50 spot (NSE), the
  nearest NIFTY future and nearest-weekly-expiry CE/PE for ATM +/- ``STRIKES_EACH_SIDE``.
* Every tick is written to data/ticks/angel/<date>/ticks.csv (see
  market_data/tick_store.py, docs/scalping/TICK_DATA.md) and pushed to subscribers.
* A scheduler thread starts recording at 09:12 IST on trading days and stops
  (compressing the day's file) at 15:42, while the dashboard server runs. Derivatives trade until
  15:40 under the closing auction session (the index itself stops updating at 15:15).
  Set env TICK_AUTO_RECORD=0 to disable.
* If spot drifts more than ``RECENTER_STRIKES`` strikes from the subscribed
  centre, the missing strikes are added to the subscription.
* On SENSEX's own weekly expiry day (Thursday) the same socket also carries the SENSEX index (BSE),
  its nearest future and the expiring CE/PE chain (BFO), recorded to data/ticks/angel_sensex/<date>/
  for the expiry closing-auction scalper. A failure there never stops the NIFTY feed.
"""
from __future__ import annotations

import json
import os
import threading
import time as _time
import urllib.request
from collections import deque
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

import platform_config
from market_data.tick_store import Instrument, Tick, TickWriter, compress_day, day_dir
from utils import Logger
from utils.timezone import IST, now_ist

SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
SPOT_TOKEN = "99926000"
STRIKE_STEP = 50
STRIKES_EACH_SIDE = 10
RECENTER_STRIKES = 6
MAX_TOKENS = 200
RECORD_START, RECORD_STOP = time(9, 12), time(15, 42)
RETRY_SECONDS = 30   # re-attempt auto-start this often after a failed login (e.g. Angel timeout at the open)
SNAP_QUOTE = 3
EXCH_TYPE = {"NSE": 1, "NFO": 2, "BSE": 3, "BFO": 4}


@dataclass(frozen=True)
class Underlying:
    name: str
    spot_token: str
    spot_exchange: str
    fo_exchange: str
    ltp_symbol: str      # trading symbol of the index for the LTP call
    strike_step: int
    source: str          # tick-store folder


NIFTY = Underlying("NIFTY", SPOT_TOKEN, "NSE", "NFO", "Nifty 50", STRIKE_STEP, "angel")
SENSEX = Underlying("SENSEX", "99919000", "BSE", "BFO", "SENSEX", 100, "angel_sensex")

TickCallback = Callable[[Tick], None]
InstCallback = Callable[[dict[str, Instrument]], None]


def _scrip_master() -> list[dict]:
    """Angel instrument master, cached once per day under data/cache/."""
    cache = Path(platform_config.CACHE_DIR) / f"angel_scrip_master_idx_{date.today():%Y%m%d}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    with urllib.request.urlopen(SCRIP_MASTER_URL, timeout=60) as r:
        rows = json.loads(r.read().decode("utf-8"))
    keep = {(u.name, u.fo_exchange) for u in (NIFTY, SENSEX)}
    rows = [x for x in rows if (x.get("name"), x.get("exch_seg")) in keep]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows), encoding="utf-8")
    return rows


def contracts(u: Underlying, today: date, rows: Optional[list[dict]] = None) -> tuple[dict, list[dict], date]:
    """(nearest future row, option rows of the nearest expiry, that expiry) for one index."""
    rows = rows if rows is not None else _scrip_master()
    rows = [r for r in rows if r.get("name") == u.name and r.get("exch_seg") == u.fo_exchange]
    exp = lambda r: datetime.strptime(r["expiry"], "%d%b%Y").date()
    opts = [r for r in rows if r.get("instrumenttype") == "OPTIDX" and r.get("expiry") and exp(r) >= today]
    futs = [r for r in rows if r.get("instrumenttype") == "FUTIDX" and r.get("expiry") and exp(r) >= today]
    if not opts or not futs:
        raise RuntimeError(f"Angel scrip master has no live {u.name} options/futures")
    expiry = min(exp(r) for r in opts)
    return min(futs, key=exp), [r for r in opts if exp(r) == expiry], expiry


def nifty_contracts(today: date, rows: Optional[list[dict]] = None) -> tuple[dict, list[dict], date]:
    return contracts(NIFTY, today, rows)


def build_instruments(spot: float, fut: dict, opts: list[dict], centre: Optional[float] = None,
                      each_side: int = STRIKES_EACH_SIDE, u: Underlying = NIFTY) -> dict[str, Instrument]:
    step = u.strike_step
    atm = round((centre or spot) / step) * step
    wanted = {float(atm + i * step) for i in range(-each_side, each_side + 1)}
    insts = {u.spot_token: Instrument(u.spot_token, u.name, "IDX", exchange=u.spot_exchange, underlying=u.name),
             str(fut["token"]): Instrument(str(fut["token"]), fut["symbol"], "FUT", lot=int(fut["lotsize"]),
                                           expiry=fut["expiry"], exchange=u.fo_exchange, underlying=u.name)}
    for r in opts:
        k = float(r["strike"]) / 100.0
        kind = r["symbol"][-2:]
        if k in wanted and kind in ("CE", "PE"):
            insts[str(r["token"])] = Instrument(str(r["token"]), r["symbol"], kind, k, int(r["lotsize"]),
                                                r["expiry"], u.fo_exchange, u.name)
    return insts


def parse_snapquote(msg: dict) -> Optional[Tick]:
    """SmartWebSocketV2 SNAP_QUOTE dict -> Tick (prices arrive in paise)."""
    if not isinstance(msg, dict) or not msg.get("token"):
        return None
    tok = "".join(ch for ch in str(msg["token"]) if ch.isdigit())
    ms = msg.get("exchange_timestamp") or 0
    ts = datetime.fromtimestamp(ms / 1000, IST) if ms else now_ist()
    buys, sells = msg.get("best_5_buy_data") or [], msg.get("best_5_sell_data") or []
    bid = (buys[0].get("price", 0) or 0) / 100.0 if buys else 0.0
    ask = (sells[0].get("price", 0) or 0) / 100.0 if sells else 0.0
    bq = int(buys[0].get("quantity", 0) or 0) if buys else 0
    aq = int(sells[0].get("quantity", 0) or 0) if sells else 0
    if bid and ask and bid > ask:  # defensive: never trust side labels over prices
        bid, ask, bq, aq = ask, bid, aq, bq
    return Tick(ts=ts, token=tok, ltp=(msg.get("last_traded_price") or 0) / 100.0,
                ltq=int(msg.get("last_traded_quantity") or 0), volume=int(msg.get("volume_trade_for_the_day") or 0),
                oi=float(msg.get("open_interest") or 0), bid=bid, ask=ask, bid_qty=bq, ask_qty=aq,
                atp=(msg.get("average_traded_price") or 0) / 100.0, tbq=float(msg.get("total_buy_quantity") or 0),
                tsq=float(msg.get("total_sell_quantity") or 0), ltt=int(msg.get("last_traded_timestamp") or 0))


class TickHub:
    """Process-wide singleton (use ``get_hub()``)."""

    def __init__(self, root: Optional[Path] = None):
        self.root = root
        self.logger = Logger("market_data.tick_hub")
        self._lock = threading.RLock()
        self._subs: dict[int, tuple[TickCallback, Optional[InstCallback]]] = {}
        self._next_sub = 0
        self.instruments: dict[str, Instrument] = {}
        self.writer: Optional[TickWriter] = None
        self.day: Optional[date] = None
        self.expiry: Optional[date] = None
        self.centre: Optional[float] = None
        self.running = False
        self.connected = False
        self.started_at = self.stopped_at = None
        self.last_tick_at: Optional[datetime] = None
        self.ticks_session = 0
        self.errors: deque[str] = deque(maxlen=20)
        self.auto = os.getenv("TICK_AUTO_RECORD", "1") != "0"
        self._broker = None
        self._sws = None
        self._fut: Optional[dict] = None
        self._opts: list[dict] = []
        self._sx: Optional[dict] = None   # SENSEX feed on its expiry day: writer, fut, opts, centre, tokens, expiry
        self._scheduler: Optional[threading.Thread] = None
        self._last_attempt = 0.0

    # ------------------------------------------------------------ subscribers
    def subscribe(self, on_tick: TickCallback, on_instruments: Optional[InstCallback] = None) -> int:
        with self._lock:
            sid = self._next_sub
            self._next_sub += 1
            self._subs[sid] = (on_tick, on_instruments)
            if on_instruments and self.instruments:
                on_instruments(dict(self.instruments))
            return sid

    def unsubscribe(self, sid: int) -> None:
        with self._lock:
            self._subs.pop(sid, None)

    def _publish_instruments(self, insts: dict[str, Instrument]) -> None:
        for _tick_cb, inst_cb in list(self._subs.values()):
            if inst_cb:
                try:
                    inst_cb(dict(insts))
                except Exception as e:
                    self._err(f"instrument subscriber failed: {e}")

    def _err(self, msg: str) -> None:
        self.errors.append(f"{now_ist():%H:%M:%S} {msg}")
        self.logger.warning(msg)

    # --------------------------------------------------------------- control
    def start(self) -> dict:
        with self._lock:
            if self.running:
                return self.status()
            self._last_attempt = _time.time()
            from market_data.angel_data_provider import AngelHistoricalDataProvider
            provider = AngelHistoricalDataProvider()
            provider.ensure_authenticated()
            self._broker = provider.broker
            today = now_ist().date()
            self._fut, self._opts, self.expiry = nifty_contracts(today)
            q = self._broker.client.ltpData("NSE", "Nifty 50", SPOT_TOKEN)
            spot = float(((q or {}).get("data") or {}).get("ltp") or 0)
            if spot <= 0:
                raise RuntimeError(f"could not read NIFTY spot: {q}")
            self.centre = spot
            self.instruments = build_instruments(spot, self._fut, self._opts)
            self.day = today
            self.writer = TickWriter("angel", today, self.root)
            self.writer.write_instruments(self.instruments, {"source": "angel", "underlying": "NIFTY",
                                                              "expiry": self.expiry.isoformat(),
                                                              "feed": "Angel One SmartWebSocketV2 SNAP_QUOTE"})
            self.ticks_session = 0
            self.running, self.started_at, self.stopped_at = True, now_ist().isoformat(), None
            self._start_sensex(today)
            self._connect()
            self._publish_instruments(self.instruments)
            self.logger.info(f"tick hub started: spot {spot:.2f}, expiry {self.expiry}, {len(self.instruments)} tokens")
            return self.status()

    def _ltp(self, exchange: str, symbol: str, token: str) -> float:
        q = self._broker.client.ltpData(exchange, symbol, token)
        return float(((q or {}).get("data") or {}).get("ltp") or 0)

    def _start_sensex(self, today: date) -> None:
        """Add the SENSEX chain on its own expiry day. Any failure is logged and leaves NIFTY untouched."""
        self._sx = None
        try:
            fut, opts, expiry = contracts(SENSEX, today)
            if expiry != today:
                return
            try:
                spot = self._ltp(SENSEX.spot_exchange, SENSEX.ltp_symbol, SENSEX.spot_token)
            except Exception:
                spot = 0.0
            if spot <= 0:  # the future is close enough to centre the strike window
                spot = self._ltp(SENSEX.fo_exchange, fut["symbol"], str(fut["token"]))
            if spot <= 0:
                raise RuntimeError("could not read the SENSEX level")
            insts = build_instruments(spot, fut, opts, u=SENSEX)
            if set(insts) & set(self.instruments):
                raise RuntimeError("a SENSEX token collides with a NIFTY token")
            writer = TickWriter(SENSEX.source, today, self.root)
            writer.write_instruments(insts, {"source": SENSEX.source, "underlying": SENSEX.name,
                                             "expiry": expiry.isoformat(),
                                             "feed": "Angel One SmartWebSocketV2 SNAP_QUOTE"})
            self._sx = {"writer": writer, "fut": fut, "opts": opts, "centre": spot, "tokens": set(insts),
                        "expiry": expiry}
            self.instruments.update(insts)
            self.logger.info(f"SENSEX expiry day: level {spot:.2f}, +{len(insts)} tokens")
        except Exception as e:
            self._err(f"SENSEX feed not started: {e}")

    def _connect(self) -> None:
        from SmartApi.smartWebSocketV2 import SmartWebSocketV2
        b = self._broker
        sws = SmartWebSocketV2(b.jwt_token, b.api_key, b.client_id, b.feed_token, max_retry_attempt=10)
        self._sws = sws

        # The library treats its own close as an error and reconnects, so a stopped or replaced socket
        # can keep delivering ticks for hours (seen 2026-10-15: after the 15:42 stop until the next morning).
        def live() -> bool:
            return self.running and self._sws is sws

        def on_open(_ws):
            if not live():
                self._retire(sws)
                return
            self.connected = True
            self._subscribe(list(self.instruments.values()))

        def on_data(ws, msg):
            if live():
                self._on_data(ws, msg)

        def on_error(_ws, e):
            if live():
                self._err(f"WS error: {e}")

        def on_close(_ws):
            if live():
                self.connected = False

        sws.on_open, sws.on_data, sws.on_error, sws.on_close = on_open, on_data, on_error, on_close
        threading.Thread(target=self._run_ws, args=(sws,), daemon=True, name="tick-hub-ws").start()

    @staticmethod
    def _retire(sws) -> None:
        """Close a socket for good: no library retries left, so the close is not followed by a reconnect."""
        try:
            sws.MAX_RETRY_ATTEMPT = 0
            sws.close_connection()
        except Exception:
            pass

    def _run_ws(self, sws) -> None:
        try:
            sws.connect()
        except Exception as e:
            self.connected = False
            self._err(f"WS connect failed: {e}")

    def _subscribe(self, insts: list[Instrument]) -> None:
        groups: dict[int, list[str]] = {}
        for i in insts:
            groups.setdefault(EXCH_TYPE.get(i.exchange, 2), []).append(i.token)
        try:
            self._sws.subscribe("tick-hub", SNAP_QUOTE, [{"exchangeType": et, "tokens": toks}
                                                         for et, toks in groups.items()])
        except Exception as e:
            self._err(f"subscribe failed: {e}")

    def stop(self, compress: bool = True) -> dict:
        with self._lock:
            if not self.running:
                return self.status()
            self.running = False   # from here on the socket callbacks drop everything (see _connect)
            if self._sws:
                self._retire(self._sws)
                self._sws = None
            self.connected = False
            if self.writer:
                self.writer.close(compress=compress)
                self.writer = None
            if self._sx:
                self._sx["writer"].close(compress=compress)
                self._sx = None
            self.stopped_at = now_ist().isoformat()
            return self.status()

    # ------------------------------------------------------------------ data
    def _on_data(self, _ws, msg: dict) -> None:
        try:
            tick = parse_snapquote(msg)
            if tick is None or tick.token not in self.instruments or tick.ltp <= 0:
                return
            sx = self._sx if self._sx and tick.token in self._sx["tokens"] else None
            writer = sx["writer"] if sx else self.writer
            if writer:
                writer.write(tick)
            self.ticks_session += 1
            self.last_tick_at = tick.ts
            for tick_cb, _ in list(self._subs.values()):
                try:
                    tick_cb(tick)
                except Exception as e:
                    self._err(f"subscriber failed: {e}")
            if tick.token == SPOT_TOKEN:
                self._maybe_recentre(tick.ltp)
            elif sx and tick.token == SENSEX.spot_token:
                self._maybe_recentre_sensex(tick.ltp)
        except Exception as e:
            self._err(f"tick handling failed: {e}")

    def _maybe_recentre(self, spot: float) -> None:
        if self.centre is None or abs(spot - self.centre) < RECENTER_STRIKES * STRIKE_STEP:
            return
        added = {t: i for t, i in build_instruments(spot, self._fut, self._opts).items() if t not in self.instruments}
        if not added or len(self.instruments) + len(added) > MAX_TOKENS:
            return
        with self._lock:
            self.centre = spot
            self.instruments.update(added)
            if self.writer:
                self.writer.write_instruments(added, {})
            self._subscribe(list(added.values()))
            self._publish_instruments(self.instruments)
            self.logger.info(f"recentred on {spot:.0f}: +{len(added)} strikes")

    def _maybe_recentre_sensex(self, spot: float) -> None:
        sx = self._sx
        if abs(spot - sx["centre"]) < RECENTER_STRIKES * SENSEX.strike_step:
            return
        added = {t: i for t, i in build_instruments(spot, sx["fut"], sx["opts"], u=SENSEX).items()
                 if t not in self.instruments}
        if not added or len(self.instruments) + len(added) > MAX_TOKENS:
            return
        with self._lock:
            sx["centre"] = spot
            sx["tokens"].update(added)
            self.instruments.update(added)
            sx["writer"].write_instruments(added, {})
            self._subscribe(list(added.values()))
            self._publish_instruments(self.instruments)
            self.logger.info(f"SENSEX recentred on {spot:.0f}: +{len(added)} strikes")

    # ------------------------------------------------------------- scheduler
    def start_scheduler(self) -> None:
        if self._scheduler and self._scheduler.is_alive():
            return
        self._scheduler = threading.Thread(target=self._schedule_loop, daemon=True, name="tick-hub-scheduler")
        self._scheduler.start()

    def in_session(self, now: Optional[datetime] = None) -> bool:
        from market_data.trading_days import TradingCalendar
        now = now or now_ist()
        return (TradingCalendar().is_trading_day(now.date()) and RECORD_START <= now.time() < RECORD_STOP)

    def _schedule_loop(self) -> None:
        _time.sleep(20)  # let other startup logins (OI paper auto-start) go first: Angel rate-limits logins
        while True:
            try:
                now = now_ist()
                if self.running and (now.time() >= RECORD_STOP or (self.day and now.date() != self.day)):
                    self.stop(compress=True)
                elif self.auto and not self.running and self.in_session(now) and _time.time() - self._last_attempt > RETRY_SECONDS:
                    try:
                        self.start()
                    except Exception as e:
                        self._err(f"auto-start failed (retry in {RETRY_SECONDS}s): {e}")
                elif (self.running and self.in_session(now) and self.last_tick_at
                      and now - self.last_tick_at > timedelta(minutes=3)):
                    self._err("no ticks for 3 minutes: reconnecting")
                    self.stop(compress=False)
                    self._last_attempt = 0.0
            except Exception as e:
                self._err(f"scheduler error: {e}")
            _time.sleep(30)

    def ensure_recording(self) -> Optional[str]:
        """Start now if the market is open; otherwise the scheduler starts at 09:12. Returns an error text."""
        if self.running or not self.in_session():
            return None
        try:
            self.start()
            return None
        except Exception as e:
            self._err(f"start failed: {e}")
            return str(e)

    # ---------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        nifty = [i for i in self.instruments.values() if (i.underlying or "NIFTY") == "NIFTY"]
        for i in nifty:
            kinds[i.kind] = kinds.get(i.kind, 0) + 1
        strikes = sorted({i.strike for i in nifty if i.kind in ("CE", "PE")})
        sx = self._sx
        return {"running": self.running, "connected": self.connected, "auto_record": self.auto,
                "day": self.day.isoformat() if self.day else None, "expiry": self.expiry.isoformat() if self.expiry else None,
                "instruments": len(self.instruments), "by_kind": kinds,
                "strike_range": [strikes[0], strikes[-1]] if strikes else None,
                "ticks_session": self.ticks_session, "rows_written": self.writer.rows if self.writer else 0,
                "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
                "started_at": self.started_at, "stopped_at": self.stopped_at, "subscribers": len(self._subs),
                "record_window": f"{RECORD_START:%H:%M}-{RECORD_STOP:%H:%M} IST on trading days",
                "file": str(day_dir("angel", self.day, self.root)) if self.day else None,
                "sensex": {"instruments": len(sx["tokens"]), "expiry": sx["expiry"].isoformat(),
                           "rows_written": sx["writer"].rows} if sx else None,
                "errors": list(self.errors)[-5:]}


_HUB: Optional[TickHub] = None
_HUB_LOCK = threading.Lock()


def get_hub() -> TickHub:
    global _HUB
    with _HUB_LOCK:
        if _HUB is None:
            _HUB = TickHub()
        return _HUB
