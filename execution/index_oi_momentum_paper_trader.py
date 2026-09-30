"""Index OI Momentum: live tick-based PAPER-ONLY trading runner.

Extracted from web_app.py (previously ``OIPaperSession`` defined inline) as
part of the per-strategy structural retrofit - see
``execution/four_indicator_paper_trader.py`` for the template this follows.
web_app.py keeps the HTTP endpoints and the ``OI_PAPER_SESSIONS`` registry;
this module owns the runner itself.

Backtesting this strategy against *real* historical data is not possible:
OI-velocity + price-momentum decisions need tick-level open-interest history,
and no configured provider (Breeze/Angel/yfinance) serves that for any
meaningful lookback - this is a genuine data-availability gap, not something
fixable in code. ``backtest/oi_momentum_backtest.py``'s synthetic tape
remains the documented, acknowledged workaround for pre-deployment sanity
checks; it is not a historical backtest.

Persistence note (scope boundary): every paper-trade event (entry, exit,
stop) is snapshotted to disk under ``STATE_DIR`` so a crashed process never
silently loses its trade log or open-position bookkeeping - operators can
inspect ``list_persisted_sessions()`` / ``load_snapshot()`` after a restart.
This is crash-safe *record keeping*, not automatic resume: a session found
still marked "running" after a restart is not silently reattached to a live
WebSocket feed (that would risk double-subscribing or drifting from the
strategy's own internal per-day counters, which are not part of this
snapshot). Restarting a genuinely live session after a crash is a deliberate
operator action via the normal start endpoint.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import platform_config
from utils.timezone import IST, ensure_ist, now_ist

STATE_DIR = Path(platform_config.FORWARD_TEST_DIR) / "oi_momentum_paper"

# Indian market timezone (IST = UTC+05:30) and the daily square-off time
MARKET_CLOSE_IST_HOUR = 15
MARKET_CLOSE_IST_MINUTE = 30


def _paper_safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        if isinstance(v, str):
            s = v.strip().replace(',', '')
            if s in ('', '-', 'null', 'None', 'N/A', 'nan', 'NaN'):
                return default
            return float(s)
        return float(v)
    except (TypeError, ValueError):
        return default


def _next_ist_close(now: Optional[datetime] = None) -> datetime:
    """Return the next 15:30 IST market-close boundary (aware IST).

    A paper session started during market hours auto-stops at 15:30 IST the
    same day; one started after the close is bounded to the next day's 15:30
    IST, so a session can never run indefinitely without a manual stop.
    """
    now = ensure_ist(now) if now else now_ist()
    ist_now = now
    close = ist_now.replace(hour=MARKET_CLOSE_IST_HOUR, minute=MARKET_CLOSE_IST_MINUTE,
                            second=0, microsecond=0)
    if ist_now >= close:
        close = close + timedelta(days=1)
    return close


def _ist_str(dt: Optional[datetime]) -> Optional[str]:
    """Format an aware datetime in IST, e.g. '2026-09-17 15:30:00 IST'."""
    return ensure_ist(dt).strftime("%Y-%m-%d %H:%M:%S IST") if dt else None


def _paper_load_env() -> Dict[str, str]:
    env: Dict[str, str] = {}
    for p in (str(platform_config.ENV_FILE),
              str(platform_config.PROJECT_ROOT / ".env")):
        try:
            if os.path.exists(p):
                with open(p) as f:
                    for line in f:
                        line = line.strip().rstrip('\\')
                        if line and not line.startswith('#') and '=' in line:
                            k, v = line.split('=', 1)
                            env.setdefault(k.strip(), v.strip())
        except Exception:
            pass
    return env


class OIPaperSession:
    """PAPER-ONLY live monitor: Angel WebSocket2 QUOTE-mode (mode=2) real OI ticks
    on FUT + ATM CE/PE tokens per index, fed to IndexOIMomentumStrategy.
    NEVER places real orders (LIVE_TRADING_ENABLED=False hard lock)."""
    SPOT = {"NIFTY": [("NSE", "99926000", "Nifty 50")],
            "BANKNIFTY": [("NSE", "99926009", "Nifty Bank")],
            "SENSEX": [("BSE", "99919012", "SENSEX"), ("BSE", "99926012", "SENSEX"),
                        ("NSE", "99926012", "SENSEX")]}
    LIVE_TRADING_ENABLED = False  # hard lock: paper only

    def __init__(self, indices: List[str], modes: List[str], capital: float, params: Dict[str, Any]):
        from strategies.index_oi_momentum import IndexOIMomentumStrategy, is_expiry_day
        self.id = f"PAPER-{int(now_ist().timestamp())}"
        self.indices = indices
        self.modes = modes  # e.g. ["base", "expiry"]
        self.capital = capital
        self.params = params or {}
        self.created_at = now_ist().isoformat()
        self.stop_at: Optional[datetime] = None
        self.stop_reason: Optional[str] = None
        self.running = True
        self.ticks_seen = 0
        self.paper_trades: List[Dict[str, Any]] = []
        self.errors: List[str] = []
        self.expiry_flags = {i: bool(is_expiry_day(i, now_ist())) for i in indices}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._strats: Dict[str, Any] = {}
        for m in modes:
            s = IndexOIMomentumStrategy("index_oi_momentum", "Index OI Momentum",
                                        {**self.params, "capital": capital, "mode_override": m})
            s.initialize()
            self._strats[m] = s
        self._broker = None
        try:
            from brokers.angel_one import AngelOneBroker
            env = _paper_load_env()
            self._broker = AngelOneBroker({
                'api_key': env.get('ANGEL_API_KEY'), 'client_id': env.get('ANGEL_CLIENT_CODE'),
                'password': env.get('ANGEL_PASSWORD_OR_MPIN'), 'totp_secret': env.get('ANGEL_TOTP_SECRET')})
            if not self._broker.authenticate():
                self.errors.append("Angel authentication failed; running in quote-retry mode.")
        except Exception as e:
            self.errors.append(f"Angel init failed: {e}")
        # open paper position tracker per (mode, index)
        self._open: Dict[str, Dict[str, Any]] = {}
        self._live: Dict[str, Dict[str, Any]] = {}
        # human-readable WS subscription ledger per index (INFO, not errors)
        self.subscriptions: Dict[str, Dict[str, Any]] = {}
        # WS health pulse: last tick time per index + last error per index
        self.ws_health: Dict[str, Dict[str, Any]] = {}
        self.ws_connected_at: Optional[str] = None
        self.last_tick_at: Optional[str] = None
        self._feed = None
        self._feed_mode: str = "init"
        self._persist()

    # -- crash-safe persistence (record-keeping only; see module docstring) ----
    def _state_path(self) -> Path:
        return STATE_DIR / f"{self.id}.json"

    def _persist(self) -> None:
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            snapshot = {
                "session_id": self.id, "indices": self.indices, "modes": self.modes,
                "capital": self.capital, "params": self.params, "created_at": self.created_at,
                "running": self.running, "stop_at": self.stop_at.isoformat() if self.stop_at else None,
                "stop_reason": self.stop_reason, "ticks_seen": self.ticks_seen,
                "paper_trades": self.paper_trades, "open_positions": self._open,
                "errors": self.errors[-50:], "saved_at": now_ist().isoformat(),
            }
            tmp = self._state_path().with_suffix(".json.tmp")
            tmp.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
            tmp.replace(self._state_path())
        except Exception as e:
            self.errors.append(f"State persist failed: {e}")

    @classmethod
    def list_persisted_sessions(cls) -> List[Dict[str, Any]]:
        """Summaries of every session snapshot on disk (this process or a prior one)."""
        if not STATE_DIR.exists():
            return []
        out = []
        for f in sorted(STATE_DIR.glob("PAPER-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                out.append({
                    "session_id": data.get("session_id"), "indices": data.get("indices"),
                    "modes": data.get("modes"), "running": data.get("running"),
                    "stop_reason": data.get("stop_reason"),
                    "paper_trades_count": len(data.get("paper_trades") or []),
                    "open_positions_count": len(data.get("open_positions") or {}),
                    "saved_at": data.get("saved_at"), "created_at": data.get("created_at"),
                })
            except Exception:
                continue
        return out

    @classmethod
    def load_snapshot(cls, session_id: str) -> Optional[Dict[str, Any]]:
        path = STATE_DIR / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def start(self):
        # Auto square-off at market close (15:30 IST) unless stopped manually first
        self.stop_at = _next_ist_close()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self, reason: str = "manual"):
        if self.stop_reason is None:
            self.stop_reason = reason
        self.running = False
        self._stop.set()
        try:
            if self._feed:
                self._feed.stop()
        except Exception:
            pass
        self._persist()

    # ---- live state fed by WebSocket SNAP_QUOTE ticks (Angel One WS2, mode=3) ----
    def _on_ws_tick(self, wt: Dict[str, Any]):
        """Route one real SNAP_QUOTE-mode tick (FUT or CE/PE, with live OI) into strategy state."""
        from core.models import Tick as _Tick
        try:
            idx = (wt.get("index") or "").upper()
            if idx not in self.indices:
                return
            role = wt.get("role", "FUT")  # FUT | CE | PE
            ltp = _paper_safe_float(wt.get("ltp"), 0.0)
            oi = _paper_safe_float(wt.get("oi"), 0.0)
            if ltp <= 0:
                return
            st = self._live.setdefault(idx, {"fut_px": 0.0, "fut_oi": 0.0,
                                              "ce_px": 0.0, "ce_oi": 0.0, "ce_bid": 0.0, "ce_ask": 0.0,
                                              "pe_px": 0.0, "pe_oi": 0.0, "pe_bid": 0.0, "pe_ask": 0.0})
            _now = now_ist().isoformat()
            self.last_tick_at = _now
            _hh = self.ws_health.setdefault(idx, {})
            _hh["last_tick_at"] = _now
            _hh["ticks"] = int(_hh.get("ticks", 0)) + 1
            _hh["last_ltp"] = round(ltp, 2)
            _hh["last_oi"] = oi
            if role == "FUT":
                st["fut_px"] = ltp
                if oi > 0: st["fut_oi"] = oi
            elif role == "CE":
                st["ce_px"] = ltp
                if oi > 0: st["ce_oi"] = oi
                st["ce_bid"] = _paper_safe_float(wt.get("bid"), ltp * 0.999)
                st["ce_ask"] = _paper_safe_float(wt.get("ask"), ltp * 1.001)
            elif role == "PE":
                st["pe_px"] = ltp
                if oi > 0: st["pe_oi"] = oi
                st["pe_bid"] = _paper_safe_float(wt.get("bid"), ltp * 0.999)
                st["pe_ask"] = _paper_safe_float(wt.get("ask"), ltp * 1.001)
            # need FUT + at least one leg before feeding strategy
            if st["fut_px"] <= 0 or (st["ce_px"] <= 0 and st["pe_px"] <= 0):
                return
            state_changed = False
            for m, strat in self._strats.items():
                # feed CE leg tick and PE leg tick (each with true OI)
                for leg, pxk, oik, bk, ak in (("CE", "ce_px", "ce_oi", "ce_bid", "ce_ask"),
                                                ("PE", "pe_px", "pe_oi", "pe_bid", "pe_ask")):
                    px = st[pxk]
                    if px <= 0:
                        continue
                    tick = _Tick(instrument=f"{idx}_{leg}", last_price=px,
                                 bid_price=st[bk] or px * 0.999, ask_price=st[ak] or px * 1.001,
                                 last_quantity=0, volume=int(_paper_safe_float(wt.get("volume"), 0)),
                                 timestamp=now_ist())  # IST
                    tick.metadata = {"index": idx, "side_hint": leg,
                                     "underlying_price": st["fut_px"],
                                     "underlying_oi": st["fut_oi"],
                                     "strike_oi": st[oik],
                                     "opp_strike_oi": st["pe_oi"] if leg == "CE" else st["ce_oi"],
                                     "volume": _paper_safe_float(wt.get("volume"), 0.0),
                                     "bid": st[bk] or px * 0.999, "ask": st[ak] or px * 1.001,
                                     "depth_qty": 1e9, "wall_px": None, "wall_oi": 0.0,
                                     "live_oi": True, "feed": "angel_ws_snap_quote"}
                    try:
                        sig = strat.on_tick(tick)
                    except Exception as e:
                        self.errors.append(f"{idx}/{m}/{leg} tick parse error: {e}")
                        continue
                    self.ticks_seen += 1
                    if sig is None:
                        continue
                    r = (sig.metadata or {}).get("reason", "")
                    key = f"{m}:{idx}"
                    if r == "oi_momentum_entry":
                        self._open[key] = {"entry": _paper_safe_float(sig.metadata.get("entry")),
                                           "qty": int(sig.quantity or 0), "mode": m,
                                           "side": str(sig.metadata.get("side") or ""),
                                           "time": now_ist().isoformat()}
                        state_changed = True
                    elif r.startswith("exit_") and key in self._open:
                        op = self._open.pop(key)
                        exit_px = _paper_safe_float(sig.metadata.get("exit"))
                        net = (exit_px - op["entry"]) * op["qty"]  # paper PnL
                        self.paper_trades.append({
                            "paper_id": f"{self.id}-{len(self.paper_trades)+1:03d}",
                            "index": idx, "variant": m, "qty": op["qty"],
                            "entry": round(op["entry"], 2), "exit": round(exit_px, 2),
                            "paper_pnl": round(net, 2), "reason": r,
                            "entry_time": op["time"],
                            "exit_time": now_ist().isoformat(),
                            "live_order_placed": False})
                        state_changed = True
            if state_changed:
                self._persist()
        except Exception as e:
            self.errors.append(f"WS tick route error: {e}")

    def _loop(self):
        """Start WS feed. ALL legs (FUT + CE + PE) stream via WS SNAP_QUOTE ticks;
        the strategy evaluates every tick (OI velocity needs 60-90s tick windows).
        No REST polling feeds the strategy -- this loop is keep-alive only."""
        if not self._start_ws_feed():
            self.errors.append("WebSocket feed unavailable; cannot run paper session without live ticks.")
            self.stop("feed_unavailable")
            return
        while not self._stop.is_set():
            if self.stop_at is not None:
                # both aware IST after the fix; ensure_ist guards legacy values
                remaining = (ensure_ist(self.stop_at) - now_ist()).total_seconds()
                if remaining <= 0:
                    # Hard square-off: never let a paper session run past 15:30 IST
                    self.stop("market_close_15:30_IST")
                    break
                self._stop.wait(min(20.0, remaining))
            else:
                self._stop.wait(20.0)
        self.running = False

    def _start_ws_feed(self) -> bool:
        """Authenticate, discover FUT + ATM CE/PE tokens, subscribe SNAP_QUOTE mode (3).
        Spot is fetched ONCE at startup for ATM-strike selection only."""
        try:
            from market_data.oi_ws_feed import OIWebSocketFeed, EXCH_TYPE, QUOTE_MODE
            from strategies.index_oi_momentum import INDEX_SPECS
            if not (self._broker and getattr(self._broker, 'client', None)):
                if not (self._broker and self._broker.authenticate()):
                    self.errors.append("Angel auth failed; cannot start WS feed.")
                    return False
            client = self._broker.client
            auth = self._broker.jwt_token
            feed = OIWebSocketFeed(auth, self._broker.api_key, self._broker.client_id,
                                   self._broker.feed_token, on_tick=self._on_ws_tick)
            FUT_EXCH = {"NIFTY": "NFO", "BANKNIFTY": "NFO", "SENSEX": "BFO"}
            OPT_EXCH = {"NIFTY": "NFO", "BANKNIFTY": "NFO", "SENSEX": "BFO"}
            ok_any = False
            for idx in self.indices:
                try:
                    spec = INDEX_SPECS[idx]
                    interval = int(spec.get("strike_interval", 50))
                    # 1) FUT token FIRST -- futures LTP is the live underlier.
                    #    Never trust the index-spot token blindly: the pinned SENSEX
                    #    token (99919012) prints a stale 63k vs live FUT ~75k.
                    fut_tok, fut_sym, fut_ex = None, None, FUT_EXCH[idx]
                    fut_px = 0.0
                    try:
                        r = client.searchScrip(fut_ex, idx)
                        _futs = [d for d in ((r or {}).get("data") or [])
                                if self._is_well_formed_fut_row(d, idx)]
                        _futs.sort(key=lambda d: str(d.get("tradingsymbol", "")))
                        if not _futs:
                            # searchScrip is fuzzy (a bare "NIFTY" query pulls in
                            # NIFTYNXT50/FINNIFTY too and can return malformed rows
                            # during rollover) -- fall back to the scrip master, same
                            # pattern as the CE/PE fallback below.
                            try:
                                _sm = self._scrip_master(fut_ex)
                                _r = self._pick_fut_from_master(_sm, idx)
                                if _r:
                                    fut_tok, fut_sym = _r
                                    self.errors.append(f"{idx}: FUT resolved via scrip-master fallback.")
                            except Exception as _sme:
                                self.errors.append(f"{idx} FUT scrip-master fallback: {_sme}")
                        else:
                            fut_tok, fut_sym = str(_futs[0]["symboltoken"]), str(_futs[0].get("tradingsymbol", ""))
                        if fut_tok:
                            try:
                                _q = client.ltpData(fut_ex, fut_sym, fut_tok)
                                fut_px = _paper_safe_float(((_q or {}).get("data") or {}).get("ltp"), 0.0)
                            except Exception as _fqe:
                                self.errors.append(f"{idx} FUT quote: {_fqe}")
                        else:
                            self.errors.append(f"{idx} FUT discovery: no well-formed FUT row found (search + scrip-master).")
                    except Exception as e:
                        self.errors.append(f"{idx} FUT discovery: {e}")
                    # 2) ATM basis: live FUT px wins; index-spot token only if sane
                    #    (within 15% of FUT, guarding stale-token drift like SENSEX 63k).
                    spot = self._fetch_spot(idx)
                    basis, basis_src = fut_px, f"FUT {fut_sym or fut_tok}"
                    if not (basis > 0):
                        basis, basis_src = spot, "index-spot token"
                    elif spot > 0 and abs(spot - basis) / basis > 0.15:
                        self.errors.append(
                            f"{idx}: spot token {spot:.0f} deviates >15% from FUT {basis:.0f}; using FUT as ATM basis.")
                    elif spot <= 0:
                        self.errors.append(f"{idx}: spot quote failed; using FUT {basis:.0f} as ATM basis.")
                    if not (basis > 0):
                        basis = float(spec.get("spot_ref", 25000.0))
                        basis_src = "static spot_ref"
                        self.errors.append(f"{idx}: no live basis; ATM ref {basis}.")
                    atm = int(round(basis / interval) * interval)
                    # 3) ATM CE/PE tokens: searchScrip first, then official scrip-master
                    #    fallback (searchScrip is fuzzy; BFO/SENSEX often returns nothing
                    #    for strike queries). Scrip master gives exact tradingsymbol match.
                    ce_tok = pe_tok = None
                    ce_sym = pe_sym = None
                    try:
                        rows: List[Dict] = []
                        for q in (f"{idx} {atm}", f"{idx}", str(atm)):
                            try:
                                r = client.searchScrip(OPT_EXCH[idx], q)
                                rows = ((r or {}).get("data") or [])
                                if rows:
                                    break
                            except Exception:
                                continue

                        def _pick(rows: List[Dict], right: str):
                            c = [d for d in rows
                                 if str(atm) in str(d.get("tradingsymbol", ""))
                                 and str(d.get("tradingsymbol", "")).strip().endswith(right)]
                            if not c:
                                return None, None
                            c.sort(key=lambda d: str(d.get("tradingsymbol", "")))
                            return str(c[0]["symboltoken"]), str(c[0].get("tradingsymbol", ""))
                        ce_tok, ce_sym = _pick(rows, "CE")
                        pe_tok, pe_sym = _pick(rows, "PE")
                        if (not ce_tok or not pe_tok):
                            # fallback: Angel scrip master JSON (snaps to nearest listed strike)
                            try:
                                _sm = self._scrip_master(OPT_EXCH[idx])
                                if not ce_tok:
                                    _r = self._pick_from_master(_sm, idx, atm, "CE")
                                    if _r:
                                        ce_tok, ce_sym = _r[0], _r[1]
                                        if len(_r) > 2:
                                            atm = int(_r[2])
                                if not pe_tok:
                                    _r = self._pick_from_master(_sm, idx, atm, "PE")
                                    if _r:
                                        pe_tok, pe_sym = _r[0], _r[1]
                                        if len(_r) > 2:
                                            atm = int(_r[2])
                                if ce_tok or pe_tok:
                                    self.errors.append(f"{idx}: option tokens resolved via scrip-master (strike {atm}).")
                            except Exception as _sme:
                                self.errors.append(f"{idx} scrip-master fallback: {_sme}")
                        if not ce_tok:
                            self.errors.append(f"{idx} CE discovery: no {atm}CE found (search + scrip-master).")
                        if not pe_tok:
                            self.errors.append(f"{idx} PE discovery: no {atm}PE found (search + scrip-master).")
                    except Exception as e:
                        self.errors.append(f"{idx} option discovery: {e}")
                    # 4) subscribe SNAP_QUOTE mode (3) = only WS2 mode carrying OI
                    toks, meta = [], {}
                    fx = fut_ex
                    if fut_tok:
                        toks.append(fut_tok)
                        meta[f"{EXCH_TYPE.get(fx, 2)}:{fut_tok}"] = {"index": idx, "role": "FUT", "symbol": fut_sym}
                    ox = OPT_EXCH[idx]
                    if ce_tok:
                        toks.append(ce_tok)
                        meta[f"{EXCH_TYPE.get(ox, 2)}:{ce_tok}"] = {"index": idx, "role": "CE", "strike": atm}
                    if pe_tok:
                        toks.append(pe_tok)
                        meta[f"{EXCH_TYPE.get(ox, 2)}:{pe_tok}"] = {"index": idx, "role": "PE", "strike": atm}
                    # FUT and OPT may live on different exchangeTypes; group by exchange
                    groups: Dict[str, List[str]] = {}
                    if fut_tok: groups.setdefault(fx, []).append(fut_tok)
                    if ce_tok or pe_tok:
                        groups.setdefault(ox, []).extend([t for t in (ce_tok, pe_tok) if t])
                    for ex, tl in groups.items():
                        m2 = {k: v for k, v in meta.items()}
                        feed.add_tokens(ex, tl, meta=m2)
                    # record subscription ledger (INFO channel, not errors)
                    self.subscriptions[idx] = {
                        "index": idx, "exchange": OPT_EXCH[idx], "fut_exchange": fut_ex,
                        "fut_symbol": fut_sym, "fut_token": fut_tok,
                        "atm_strike": atm, "spot_ref": round(basis, 2),
                        "atm_basis": basis_src,
                        "ce_symbol": ce_sym, "ce_token": ce_tok,
                        "pe_symbol": pe_sym, "pe_token": pe_tok,
                        "ws_mode": "SNAP_QUOTE(3)", "feed": "Angel One WebSocket2",
                    }
                    if toks:
                        ok_any = True
                    else:
                        self.errors.append(f"{idx}: no tokens discovered; skipped.")
                except Exception as e:
                    self.errors.append(f"{idx} WS setup error: {e}")
            if ok_any:
                feed.start()
                self._feed = feed
                self._feed_mode = "angel_ws_snap_quote"
                import time as _wt
                for _ in range(30):
                    _wt.sleep(1.0)
                    if getattr(feed, 'connected', False) or getattr(feed, 'ticks_seen', 0) > 0:
                        break
                for _fe in getattr(feed, 'errors', []) or []:
                    self.errors.append(f"WS: {_fe}")
                if getattr(feed, 'connected', False):
                    self.ws_connected_at = getattr(feed, 'connected_at', None) or now_ist().isoformat()
                else:
                    self.errors.append("WS handshake not confirmed within 30s (connecting…).")
                return True
            self._feed_mode = "quote_fallback"
            return False
        except Exception as e:
            self.errors.append(f"WS feed init failed: {e}")
            self._feed_mode = "quote_fallback"
            return False

    @staticmethod
    def _is_well_formed_fut_row(d: Dict, idx: str) -> bool:
        """Guard against malformed searchScrip rows (seen in practice: a bare
        index-name query like "NIFTY" is fuzzy-matched by Angel's backend and
        can return a row with two expiries jammed into one tradingsymbol and
        two symboltokens joined by a space, e.g. tradingsymbol
        "NIFTY23NOV2629DEC26FUT" / symboltoken "61471 58875"). A real FUT row's
        symboltoken is purely numeric and its tradingsymbol is short and
        idx-prefixed."""
        sym = str(d.get("tradingsymbol", "")).strip()
        tok = str(d.get("symboltoken", "")).strip()
        return bool(
            tok.isdigit()
            and sym.endswith("FUT")
            and sym.startswith(idx)
            and " " not in sym
            and len(sym) <= len(idx) + 10  # idx + DDMONYY + "FUT", generous
        )

    @staticmethod
    def _pick_fut_from_master(rows: List[Dict], idx: str):
        """Nearest (non-expired) FUT contract for an index from the scrip
        master, mirroring _pick_from_master's option-side fallback."""
        from datetime import datetime as _mdt
        _pool = [d for d in rows
                 if idx in (str(d.get("symbol", "")) + str(d.get("name", ""))).replace(" ", "").upper()
                 and str(d.get("instrumenttype", "")).upper() == "FUTIDX"]
        if not _pool:
            return None

        def _exp(d):
            try:
                return _mdt.strptime(str(d.get("expiry", "")), "%d%b%Y").date().toordinal()
            except Exception:
                return 99999999
        _today = _mdt.now().date().toordinal()
        _pool.sort(key=lambda d: (_exp(d) < _today, _exp(d), str(d.get("symbol", ""))))
        best = _pool[0]
        return str(best.get("token", "")), str(best.get("symbol", ""))

    _SCRIP_MASTER_CACHE: Dict[str, Any] = {}

    def _scrip_master(self, exchange: str) -> List[Dict]:
        """Fetch Angel scrip-master JSON for an exchange (cached per process)."""
        import json as _js
        import urllib.request as _url
        if exchange in OIPaperSession._SCRIP_MASTER_CACHE:
            return OIPaperSession._SCRIP_MASTER_CACHE[exchange]
        _URLS = {"NFO": "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
                 "BFO": "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
                 "NSE": "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"}
        with _url.urlopen(_URLS.get(exchange, _URLS["NFO"]), timeout=30) as _r:
            _all = _js.loads(_r.read().decode("utf-8"))
        _SEG = {"NFO": "NFO", "BFO": "BFO", "NSE": "NSE"}
        _rows = [d for d in _all if str(d.get("exch_seg", "")).upper() == _SEG.get(exchange, exchange)]
        OIPaperSession._SCRIP_MASTER_CACHE[exchange] = _rows
        return _rows

    @staticmethod
    def _abs_strike_static(d: Dict):
        try:
            return float(str(d.get("strike", "0")).replace(",", "") or 0) / 100.0
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _pick_from_master(rows: List[Dict], idx: str, atm: int, right: str):
        """Nearest-expiry option for index + strike + CE/PE from scrip master.
        Master schema: symbol='SENSEX5026SEP2663400CE', token='…', strike='6340000.000000'
        (paise x100), expiry='26NOV2026', instrumenttype='OPTIDX'. Match trailing
        {strike}{CE|PE} on symbol, or numeric strike/100 == atm as fallback."""
        from datetime import datetime as _mdt
        _tail = f"{atm}{right}".upper()
        _abs_strike = OIPaperSession._abs_strike_static
        _pool = [d for d in rows
                 if idx in (str(d.get("symbol", "")) + str(d.get("name", ""))).replace(" ", "").upper()
                 and str(d.get("instrumenttype", "")).upper().startswith("OPT")]
        _c = [d for d in _pool
              if (str(d.get("symbol", "")).strip().upper().endswith(_tail)
                  or (_abs_strike(d) is not None and abs(_abs_strike(d) - atm) < 0.01
                      and str(d.get("symbol", "")).strip().upper().endswith(right)))]
        # strike grid may not list our exact ATM (e.g. SENSEX 100-pt grid vs 63426 spot):
        # snap to nearest listed strike for this index instead of giving up.
        _snapped = atm
        if not _c and _pool:
            def _strike_of(d):
                return _abs_strike(d)
            _with = [(abs(s - atm), s) for s in (_strike_of(d) for d in _pool) if s is not None]
            if _with:
                _with.sort()
                _snapped = _with[0][1]
                _c = [d for d in _pool
                      if _abs_strike(d) is not None and abs(_abs_strike(d) - _snapped) < 0.01
                      and str(d.get("symbol", "")).strip().upper().endswith(right)]
        if not _c:
            return None, None
        def _exp(d):
            try:
                return _mdt.strptime(str(d.get("expiry", "")), "%d%b%Y").date().toordinal()
            except Exception:
                return 99999999
        _today = _mdt.now().date().toordinal()
        _c.sort(key=lambda d: (_exp(d) < _today, _exp(d), str(d.get("symbol", ""))))
        _tok, _sym = str(_c[0].get("token", "")), str(_c[0].get("symbol", ""))
        return (_tok, _sym, int(_snapped)) if _snapped != atm else (_tok, _sym)

    def _fetch_spot(self, idx: str) -> float:
        if not (self._broker and getattr(self._broker, 'client', None)):
            return 0.0
        for exch, token, sym in self.SPOT[idx]:
            try:
                res = self._broker.client.ltpData(exch, sym, token)
                if isinstance(res, dict):
                    if res.get('status') and res.get('data'):
                        px = _paper_safe_float((res['data'] or {}).get('ltp'), 0.0)
                        if px > 0:
                            # pin working candidate first for next polls
                            cands = self.SPOT[idx]
                            used = (exch, token, sym)
                            if cands[0] != used:
                                cands.remove(used)
                                cands.insert(0, used)
                            return px
                    else:
                        self.errors.append(f"{idx} quote rejected on {exch}/{token}: {(res.get('message') or res.get('errorcode'))}")
            except Exception as e:
                self.errors.append(f"{idx} quote parse error on {exch}/{token}: {e}")
        return 0.0

    def live_prices(self) -> Dict[str, Dict[str, Any]]:
        """Latest WS CMP per index leg (FUT/CE/PE) + tick age. No REST polling."""
        from datetime import datetime as _pdt
        from utils.timezone import ensure_ist as _eist3, now_ist as _now_ist3
        out: Dict[str, Dict[str, Any]] = {}
        for idx in self.indices:
            st = self._live.get(idx, {})
            hh = (getattr(self, 'ws_health', {}) or {}).get(idx, {})
            try:
                _age = max(0, int((_now_ist3() - _eist3(_pdt.fromisoformat(str(hh.get('last_tick_at'))))).total_seconds())) if hh.get('last_tick_at') else None
            except Exception:
                _age = None
            out[idx] = {"fut": round(float(st.get('fut_px', 0.0) or 0.0), 2),
                        "ce": round(float(st.get('ce_px', 0.0) or 0.0), 2),
                        "pe": round(float(st.get('pe_px', 0.0) or 0.0), 2),
                        "fut_oi": st.get('fut_oi', 0.0), "ce_oi": st.get('ce_oi', 0.0), "pe_oi": st.get('pe_oi', 0.0),
                        "ticks": int(hh.get('ticks', 0)), "tick_age_s": _age}
        return out

    def _open_position_details(self, live_px: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """Snapshot of in-progress paper positions: entry, qty, option side, entry time
        plus mark-to-market (unrealized) PnL from the latest WS tick prices."""
        lp = live_px if live_px is not None else self.live_prices()
        out: List[Dict[str, Any]] = []
        for key, pos in self._open.items():
            if ":" in key:
                variant, idx = key.split(":", 1)
            else:
                variant, idx = "base", key
            side = str(pos.get("side") or "").upper()
            entry = _paper_safe_float(pos.get("entry"))
            qty = int(pos.get("qty") or 0)
            mark = None
            try:
                leg = (lp.get(idx) or {}).get("ce" if side == "CE" else "pe") if side in ("CE", "PE") else None
                if leg:
                    mark = round(float(leg), 2)
            except Exception:
                mark = None
            unreal = round((mark - entry) * qty, 2) if (mark and entry) else None
            out.append({"key": key, "index": idx, "variant": variant or "base", "side": side,
                        "entry": entry, "qty": qty, "entry_time": pos.get("time"),
                        "mark": mark, "unrealized_pnl": unreal})
        return out

    def status(self) -> Dict[str, Any]:
        feed_ticks = getattr(self._feed, 'ticks_seen', 0) if self._feed else 0
        for _fe in (getattr(self._feed, 'errors', []) or [])[-5:]:
            _m = f"WS: {_fe}"
            if _m not in self.errors:
                self.errors.append(_m)
        if getattr(self._feed, 'connected_at', None) and not getattr(self, 'ws_connected_at', None):
            self.ws_connected_at = self._feed.connected_at
        live_px = self.live_prices()  # single pass; reused for the payload + open-position marking
        return {"session_id": self.id, "running": self.running,
                "strategy_id": "index_oi_momentum",
                "feed_source": "Angel One WebSocket2 (SNAP_QUOTE mode=3)",
                "feed_mode": getattr(self, '_feed_mode', 'init'),
                "subscriptions": getattr(self, 'subscriptions', {}),
                "ws_health": getattr(self, 'ws_health', {}),
                "ws_connected_at": getattr(self, 'ws_connected_at', None),
                "last_tick_at": getattr(self, 'last_tick_at', None),
                "ws_ticks": feed_ticks,
                "live_prices": live_px,
                "indices": self.indices, "variants": self.modes,
                "expiry_today": self.expiry_flags, "capital": self.capital,
                "ticks_seen": self.ticks_seen, "paper_trades": self.paper_trades[-50:],
                "paper_trades_count": len(self.paper_trades),
                "open_paper_positions": list(self._open.keys()),
                "open_paper_position_details": self._open_position_details(live_px),
                "live_trading": False, "live_order_placed": False,
                "errors": self.errors[-10:], "created_at": self.created_at,
                "stop_at_ist": _ist_str(self.stop_at), "stop_reason": self.stop_reason,
                "market_close_ist": "15:30"}


__all__ = ["OIPaperSession", "_paper_safe_float", "_next_ist_close", "_ist_str",
          "_paper_load_env", "STATE_DIR"]
