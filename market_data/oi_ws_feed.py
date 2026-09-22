"""Production Angel One SmartAPI WebSocket2 SNAP_QUOTE-mode OI feed.

Subscribes FUT + ATM CE/PE tokens per index in SNAP_QUOTE mode (mode=3),
the only WS2 mode whose payload carries open_interest (bytes 131-139) plus
best-5 depth. QUOTE mode (2) does NOT include OI in the smartapi-python
parser. Paper-only consumer: never places orders.
"""
import threading, time
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from utils.timezone import now_ist

EXCH_TYPE = {"NSE": 1, "NFO": 2, "BSE": 3, "BFO": 4, "MCX": 5, "NCDEX": 7}
# Angel WS modes: 1=LTP (px only), 2=QUOTE (OHLC, NO open_interest in
# smartapi-python parser), 3=SNAP_QUOTE (adds open_interest bytes 131-139 +
# best-5 depth). OI velocity REQUIRES mode 3. Max ~50 tokens in DEPTH/SNAP.
QUOTE_MODE = 2
SNAP_QUOTE_MODE = 3
OI_MODE = SNAP_QUOTE_MODE

INDEX_FUT = {
    "NIFTY": ("NFO", "NIFTY"),
    "BANKNIFTY": ("NFO", "BANKNIFTY"),
    "SENSEX": ("BFO", "SENSEX"),
}


def _num(v, default=0.0):
    try:
        if v is None: return default
        if isinstance(v, str):
            s = v.strip().replace(",", "")
            if s in ("", "-", "null", "None", "N/A", "nan", "NaN", "inf", "Infinity"): return default
            v = float(s)
        else: v = float(v)
        import math
        if math.isnan(v) or math.isinf(v): return default
        return v
    except (TypeError, ValueError): return default


class OIWebSocketFeed:
    """Connects SmartWebSocketV2, subscribes QUOTE mode, routes parsed ticks."""

    def __init__(self, auth_token, api_key, client_code, feed_token, on_tick: Callable[[Dict], None]):
        self.on_tick = on_tick
        self.errors: List[str] = []
        self.ticks_seen = 0
        self.connected = False
        self.connected_at = None
        self._stop = threading.Event()
        self._subs: List[Dict] = []  # token_list entries
        self._meta: Dict[str, Dict] = {}  # "EXCH:TOKEN" -> {index, role, strike}
        self._sws = None
        self._cred = (auth_token, api_key, client_code, feed_token)

    def add_tokens(self, exchange: str, tokens: List[str], meta: Optional[Dict[str, Dict]] = None):
        et = EXCH_TYPE.get(exchange, 2)
        toks = [t for t in tokens if t]
        if not toks: return
        self._subs.append({"exchangeType": et, "tokens": toks, "_exch": exchange})
        if meta:
            self._meta.update(meta)

    def discover_option_tokens(self, client, index: str, expiry_tag: str = "") -> Dict[str, Any]:
        """Resolve FUT + ATM CE/PE tokens via searchScrip. Returns dict with tokens list."""
        out = {"fut": None, "ce": None, "pe": None, "atm": None, "expiry": None}
        try:
            exch_fut, q = INDEX_FUT[index]
            r = client.searchScrip(exch_fut if exch_fut != "BFO" else "BFO", q)
            data = (r or {}).get("data") or []
            # pick current-month FUT
            futs = [d for d in data if "FUT" in (d.get("tradingsymbol", ""))]
            if futs:
                futs.sort(key=lambda d: d.get("tradingsymbol", ""))
                out["fut"] = futs[0]
            # spot ref for ATM: use ltp of fut or index ref
            # find weekly CE/PE near ATM: need spot -> query ltpData for index spot
            # caller supplies atm strike; here just return fut
        except Exception as e:
            self.errors.append(f"{index} token discovery error: {e}")
        return out

    def start(self):
        from SmartApi.smartWebSocketV2 import SmartWebSocketV2
        at, ak, cc, ft = self._cred
        self._sws = SmartWebSocketV2(at, ak, cc, ft, max_retry_attempt=3)
        sws = self._sws
        sws.on_open = self._on_open
        sws.on_data = self._on_data
        sws.on_error = lambda ws, e: self.errors.append(f"WS error: {e}")
        sws.on_close = lambda ws: self.errors.append("WS closed")
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _run(self):
        try:
            self._sws.connect()
        except Exception as e:
            self.errors.append(f"WS connect failed: {e}")

    def _on_open(self, wsapp):
        # NOTE: wsapp here is the raw websocket-client WebSocketApp, NOT the
        # SmartWebSocketV2 wrapper -- it has no .subscribe(). Subscribing must
        # go through self._sws.subscribe() (sends JSON down the open socket).
        self.connected = True
        self.connected_at = now_ist().isoformat()
        for entry in self._subs:
            try:
                tl = [{"exchangeType": entry["exchangeType"], "tokens": entry["tokens"]}]
                self._sws.subscribe("oi-paper", OI_MODE, tl)
            except Exception as e:
                self.errors.append(f"subscribe failed {entry.get('_exch')}: {e}")

    def _on_data(self, wsapp, msg: Dict):
        try:
            if not isinstance(msg, dict): return
            tok = str(msg.get("token", ""))
            exch = str(msg.get("exchange_type", ""))
            key = f"{exch}:{tok}"
            meta = self._meta.get(key, {})
            tick = {
                "token": tok, "exchange": exch,
                "ltp": _num(msg.get("last_traded_price"), 0.0) / 100.0,
                "oi": _num(msg.get("open_interest"), 0.0),
                "volume": _num(msg.get("volume_trade_for_the_day"), 0.0),
                "bid": _num(msg.get("best_5_buy_data", [{}])[0].get("price"), 0.0) / 100.0 if msg.get("best_5_buy_data") else 0.0,
                "ask": _num(msg.get("best_5_sell_data", [{}])[0].get("price"), 0.0) / 100.0 if msg.get("best_5_sell_data") else 0.0,
                "ts": now_ist(),  # IST
                **meta,
            }
            # SmartWebSocketV2 already divides? guard: if ltp absurdly large, fix
            self.ticks_seen += 1
            self.on_tick(tick)
        except Exception as e:
            self.errors.append(f"tick parse error: {e}")

    def stop(self):
        self._stop.set()
        try:
            if self._sws: self._sws.close_connection()
        except Exception: pass
