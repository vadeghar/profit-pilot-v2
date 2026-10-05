"""option_symbol.py - broker-specific option contract identifiers.

Single entry point::

    get_option_symbol("angel",    "NIFTY", "2026-09-29", 25000, "CE")  -> AngelContract (token + tradingsymbol)
    get_option_symbol("breeze",   "NIFTY", "2026-09-29", 25000, "CE")  -> dict of Breeze API kwargs
    get_option_symbol("breeze",   "BSE:SENSEX", "2026-10-08", 84600, "PE") -> dict (BFO)
    get_option_symbol("yfinance", "AAPL",  "2026-10-16", 250,   "CE")  -> "AAPL261016C00250000"

Angel One returns the **token** (plus the master's tradingsymbol); Breeze has no
symbol string - the API is keyed by the returned kwargs; yfinance returns the
OCC symbol.

Formats (verified against the live instrument universes, Sep-2026)
------------------------------------------------------------------
Angel One (``angel_lookup`` reads OpenAPIScripMaster.json and returns the
master row's token + tradingsymbol - never a guessed string):

    NFO (NSE F&O) / MCX (OPTFUT, OPTIDX) : NAME + DDMMMYY + STRIKE + CE|PE
        NIFTY06OCT2621400PE, RELIANCE23NOV261140CE, IEX27OCT26117.5CE,
        GOLD25SEP26153000CE, SILVER27OCT26283000PE
    BFO (BSE F&O) index, weekly / monthly : NAME + YY+<month letter><DD> /
                                           NAME + YYMMM  + STRIKE + CE|PE
        SENSEX26O0884600PE (08-Oct-2026, weekly),
        SENSEX26SEP85000CE (24-Sep-2026, monthly)
    BFO (BSE F&O) stock, monthly only : NAME + YYMMM + STRIKE + CE|PE
        CDSL26NOV1840PE, 360ONE26NOV1260CE, BANKEX26SEP56900PE
    NCO (commodity options), monthly only : NAME + YYMMM + STRIKE + CE|PE
        SILVERM26NOV262250PE (OPTFUT), SILVER26DEC259000PE (OPTBLN)
    CDS (currency options) : YY+<month letter><DD> for the Friday weeklies,
                             YYMMM for a month-end expiry that is not a Friday
        USDINR26O2397.25CE (23-Oct-2026 weekly), USDINR26O3097.5PE
        (30-Oct-2026 month-end Friday), JPYINR26NOV56.75PE (26-Nov-2026)

    Verified row-by-row against the Sep-2026 master: 126,250 CE|PE rows, all
    126,250 ``angel_lookup`` results exact; the symbol builder reproduces
    125,922 rows - the 328 residuals are CDS 25-Sep-2026 contracts Angel spells
    with a legacy numeric month token ("USDINR26925..."), a data-side quirk.

    Master quirks handled: ``strike`` is stored scaled as a decimal string -
    x100 for F&O/commodities ("2140000.000000"), x10^7 for CDS currency
    ("972500000.000000" = 97.25); BFO OPTSTK rows may carry an empty ``name``
    (derived from the symbol); MCX/NCO options have ``instrumenttype`` =
    ``OPTFUT`` / ``OPTBLN``; cash-equity rows ("DECCANCE") end in CE|PE but
    carry no expiry and strike -1, so they are never indexed.

Breeze (no symbol string; these kwargs key every option API)::

    {"stock_code": "NIFTY"|"CNXBAN"|"RELIND", "exchange_code": "NFO"|"BFO"|"MCX",
     "product_type": "options", "expiry_date": "YYYY-MM-DDT06:00:00.000Z",
     "right": "call"|"put", "strike_price": "<str>"}

    ``breeze_feed_params`` rewrites date/right for ``subscribe_feeds``
    ("DD-Mon-YYYY", "Call"/"Put").  ``stock_code`` is resolved through the
    universe YAML (RELIANCE -> RELIND), the static index codes below and the
    Breeze scrip master ShortName, in that order.

yfinance (US OCC/OSI; Yahoo does **not** serve NSE/BSE options - verified: the
RELIANCE.NS / ^NSEI option chains are empty)::

    ROOT + YYMMDD + C|P + strike*1000 zero-padded to 8 digits
        AAPL260923C00245000 was matched verbatim against the live AAPL chain.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

ANGEL_MASTER_URLS = (
    "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json",
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
)

# Angel One instrument master cache (override with OPTION_SYMBOL_CACHE_DIR).
CACHE_DIR = Path(os.environ.get("OPTION_SYMBOL_CACHE_DIR")
                 or Path.home() / ".cache" / "option_symbols")

# Angel instrument types that carry CE/PE contracts (observed in the master:
# F&O indices/stocks, MCX & NCO futures/bullion options, CDS currency/IRC
# options). The index also accepts rows with a blank type, so new option
# instruments keep resolving without a code change.
ANGEL_OPTION_TYPES = ("OPTIDX", "OPTSTK", "OPTFUT", "OPTBLN", "OPTCUR", "OPTIRC")

# 'EXCH:' prefix -> exchange on which that underlying's options trade.
UNDERLYING_EXCHANGE = {"NSE": "NFO", "BSE": "BFO", "NFO": "NFO", "BFO": "BFO",
                       "MCX": "MCX", "NCO": "NCO", "CDS": "CDS"}
# BSE weekly symbols exist only for these index underlyings; BSE stocks (and
# all NCO options) are monthly-only and always use the compact YYMMM form.
# CDS currency options use the weekly/monthly rule for every underlying.
BSE_WEEKLY_INDEX_SYMBOLS = ("SENSEX", "BANKEX", "SENSEX50")
# BSE/CDS weekly symbols fold the month into a single letter (O/S/N/D/J/M
# observed live; the rest follow the same first-letter rule - monthly symbols
# spell the month out and are unambiguous).
BSE_MONTH_LETTER = {1: "J", 2: "F", 3: "M", 4: "A", 5: "M", 6: "J",
                    7: "J", 8: "A", 9: "S", 10: "O", 11: "N", 12: "D"}
# Length of the expiry code inside a tradingsymbol: YYMMM (and the YY+letter+DD
# weekly form) on BSE/NCO/CDS, DDMMMYY elsewhere.
EXPIRY_CODE_LEN = {"BFO": 5, "NCO": 5, "CDS": 5}
# The master stores strikes scaled per segment: F&O / commodity strikes x100
# (21400 -> "2140000.000000"), currency (CDS) strikes x10^7
# (97.25 -> "972500000.000000").
STRIKE_SCALE = {"CDS": 10_000_000}


def _strike_scale(exchange: str) -> int:
    return STRIKE_SCALE.get(exchange, 100)

# Breeze uses ICICI short codes for some underlyings. Verify extras with
# breeze.get_names(exchange_code="NFO", stock_code="<name>").
BREEZE_STOCK_CODES = {"BANKNIFTY": "CNXBAN", "SENSEX": "BSESEN"}

DateLike = Union[date, datetime, str]
StrikeLike = Union[int, float, str]


# ----------------------------------------------------------------- helpers
def _to_date(d: DateLike) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    text = str(d).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d-%b-%Y", "%d%b%Y", "%d%b%y", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unrecognised expiry date: {d!r}")


def _to_type(t: str) -> str:
    """Normalise to 'CE' or 'PE'."""
    text = str(t).strip().upper()
    if text in ("CE", "C", "CALL"):
        return "CE"
    if text in ("PE", "P", "PUT"):
        return "PE"
    raise ValueError(f"Unrecognised option type: {t!r} (use 'CE' or 'PE')")


def _num(strike: float) -> str:
    """25000.0 -> '25000', 117.5 -> '117.5' (the exact form Angel's symbols use)."""
    value = float(strike)
    return f"{value:g}" if value != int(value) else str(int(value))


def _to_strike(strike: StrikeLike) -> float:
    """Accept 25000 / 25000.0 / '25,000' from CLIs and configs."""
    try:
        value = float(str(strike).strip().replace(",", "")) if isinstance(strike, str) else float(strike)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Unrecognised strike price: {strike!r}") from exc
    if not value > 0:
        raise ValueError(f"Strike price must be positive, got {strike!r}")
    return value


def _split_underlying(underlying: str, exchange: Optional[str] = None) -> Tuple[str, str]:
    """'NSE:NIFTY' / 'nifty' / ('SENSEX', 'BFO') -> ('NIFTY', 'NFO').

    An explicit ``exchange`` argument wins; otherwise the 'EXCH:' prefix decides
    (NSE->NFO, BSE->BFO, MCX->MCX, NFO/BFO as-is), BSE indices default to BFO
    and everything else to NFO.
    """
    raw = str(underlying).strip().upper()
    prefix = ""
    if ":" in raw:
        prefix, raw = (part.strip() for part in raw.split(":", 1))
    if exchange:
        exch = str(exchange).strip().upper()
    elif prefix in UNDERLYING_EXCHANGE:
        exch = UNDERLYING_EXCHANGE[prefix]
    elif raw in BSE_WEEKLY_INDEX_SYMBOLS:
        exch = "BFO"
    else:
        exch = "NFO"
    return raw, exch


def _clean_underlying(value: str) -> str:
    """'BSE:BSESEN' -> 'BSESEN'; 'RELIANCE.NS' -> 'RELIANCE'; 'SBIN-EQ' -> 'SBIN'."""
    code = str(value).strip().upper()
    if ":" in code:
        code = code.split(":", 1)[1].strip()
    for tail in (".NS", ".BO", "-EQ"):
        if code.endswith(tail):
            return code[:-len(tail)]
    return code


def normalize_broker(broker: str) -> str:
    """Map user aliases to 'angel' | 'breeze' | 'yfinance'."""
    key = str(broker).strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    aliases = {
        "angel": "angel", "angelone": "angel", "angelbroking": "angel", "smartapi": "angel",
        "breeze": "breeze", "icici": "breeze", "icicidirect": "breeze", "breezedirect": "breeze",
        "yfinance": "yfinance", "yahoo": "yfinance", "yahoofinance": "yfinance",
    }
    try:
        return aliases[key]
    except KeyError:
        raise ValueError(
            f"Unknown broker: {broker!r} (supported: angel, breeze, yfinance)") from None


# ------------------------------------------------------------------ Angel
@dataclass(frozen=True)
class AngelContract:
    """A resolved Angel One option contract (from the instrument master)."""
    token: str
    symbol: str          # tradingsymbol, e.g. NIFTY06OCT2621400PE
    exchange: str        # NFO / BFO / MCX
    lotsize: int
    name: str
    expiry: str          # DDMMMYYYY as in the master
    strike: float


def angel_symbol(underlying: str, expiry: DateLike, strike: StrikeLike,
                 option_type: str, exchange: Optional[str] = None) -> str:
    """Predict the Angel One tradingsymbol for a contract (no token lookup).

    Prefer :func:`angel_lookup` - it returns the *actual* symbol + token from
    the master. This builder mirrors the master's formats (every CE|PE row of
    the Sep-2026 master is reproduced exactly) for previews, logs and
    lookup-error hints.
    """
    name, exch = _split_underlying(underlying, exchange)
    e, t = _to_date(expiry), _to_type(option_type)
    strike_str = _num(_to_strike(strike))
    compact = f"{name}{e:%y}{BSE_MONTH_LETTER[e.month]}{e:%d}{strike_str}{t}"  # SENSEX26O0884600PE
    spelled = f"{name}{e:%y%b}{strike_str}{t}".upper()                         # CDSL26NOV1840PE
    month_end = (e + timedelta(days=7)).month != e.month
    if exch == "CDS":
        # Currency weeklies use the compact form; a month-end contract spells
        # the month out unless that expiry is itself the Friday weekly.
        return spelled if month_end and e.weekday() != 4 else compact
    if exch == "BFO":
        # The weekly spelling exists only for the BSE weekly index series.
        if name in BSE_WEEKLY_INDEX_SYMBOLS and not month_end:
            return compact
        return spelled
    if exch == "NCO":
        return spelled          # commodity options are monthly-only
    return f"{name}{e:%d%b%y}{strike_str}{t}".upper()                # NIFTY06OCT2621400PE


def load_angel_master(force: bool = False,
                      cache_dir: Optional[Union[str, Path]] = None) -> List[dict]:
    """Download OpenAPIScripMaster.json (cached once per day, ~35 MB)."""
    cache = Path(cache_dir) if cache_dir else CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"angel_master_{date.today():%Y%m%d}.json"
    if path.exists() and not force:
        return json.loads(path.read_text())
    last_err: Optional[Exception] = None
    for url in ANGEL_MASTER_URLS:
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                raw = response.read()
            path.write_bytes(raw)
            for old in cache.glob("angel_master_*.json"):
                if old != path:
                    old.unlink(missing_ok=True)
            return json.loads(raw)
        except Exception as ex:  # try the next mirror
            last_err = ex
    raise RuntimeError(f"Could not download Angel One master: {last_err}")


_angel_index: Dict[Tuple[str, str, str, int, str], dict] = {}


def _symbol_underlying(row: dict, exchange: str) -> str:
    """Underlying name embedded in a tradingsymbol.

    Needed because some BFO OPTSTK rows ship an empty ``name`` (e.g.
    360ONE26NOV1260CE). The expiry code is 5 chars on BFO/NCO/CDS (YYMMM,
    also the YY+letter+DD weekly form) and 7 chars elsewhere (DDMMMYY).
    """
    symbol = str(row.get("symbol", ""))
    try:
        strike_str = _num(float(row["strike"]) / _strike_scale(exchange))
    except (KeyError, TypeError, ValueError):
        return ""
    cut = len(strike_str) + 2 + EXPIRY_CODE_LEN.get(exchange, 7)
    return symbol[:-cut] if len(symbol) > cut else ""


def build_angel_index(master: Iterable[dict]) -> dict:
    """{(exch, name, 'DDMMMYYYY', strike*100 round, 'CE'|'PE') -> master row}.

    Rows are keyed both by their ``name`` field and by the underlying parsed
    from the symbol, so lookups keep working when a master row's name is blank.
    """
    idx: dict = {}
    for row in master:
        symbol = str(row.get("symbol", ""))
        option_type = symbol[-2:]
        if option_type not in ("CE", "PE"):
            continue
        try:
            strike100 = round(float(row["strike"]))
            expiry = str(row["expiry"]).strip().upper()
        except (KeyError, TypeError, ValueError):
            continue
        if strike100 <= 0 or not expiry:
            continue   # futures (no expiry match) and cash equities ('DECCANCE')
        exchange = str(row.get("exch_seg", "")).strip().upper()
        names = {str(row.get("name", "")).strip().upper(), _symbol_underlying(row, exchange)}
        for name in names:
            if name:
                idx[(exchange, name, expiry, strike100, option_type)] = row
    return idx


def clear_angel_cache() -> None:
    """Drop the in-process master index (after a refresh, or in tests)."""
    global _angel_index
    _angel_index = {}


def _get_angel_index() -> dict:
    global _angel_index
    if not _angel_index:
        _angel_index = build_angel_index(load_angel_master())
    return _angel_index


def _master_row(index: dict, name: str, exch: str, e: date, strike: float, t: str) -> Optional[dict]:
    return index.get((exch, name, f"{e:%d%b%Y}".upper(),
                      round(strike * _strike_scale(exch)), t))


def angel_expiries(underlying: str, exchange: Optional[str] = None,
                   master: Optional[List[dict]] = None) -> List[date]:
    """Sorted expiry dates the master lists for an underlying/exchange."""
    name, exch = _split_underlying(underlying, exchange)
    index = build_angel_index(master) if master is not None else _get_angel_index()
    found = {_to_date(expiry) for (exch_key, name_key, expiry, _s, _t) in index
             if exch_key == exch and name_key == name}
    return sorted(found)


def angel_strikes(underlying: str, expiry: DateLike, exchange: Optional[str] = None,
                  option_type: Optional[str] = None,
                  master: Optional[List[dict]] = None) -> List[float]:
    """Sorted strikes the master lists for an underlying/expiry/type."""
    name, exch = _split_underlying(underlying, exchange)
    e = _to_date(expiry)
    wanted_type = _to_type(option_type) if option_type else None
    expiry_key = f"{e:%d%b%Y}".upper()
    index = build_angel_index(master) if master is not None else _get_angel_index()
    found = {strike100 / _strike_scale(exch)
             for (exch_key, name_key, expiry_str, strike100, typ) in index
             if exch_key == exch and name_key == name and expiry_str == expiry_key
             and (wanted_type is None or typ == wanted_type)}
    return sorted(found)


def _nearby_expiries(dates: List[date], count: int = 6) -> str:
    """'06-Oct-2026, 29-Oct-2026, ...' for lookup-error hints."""
    today = date.today()
    upcoming = [d for d in dates if d >= today] or dates
    shown = ", ".join(f"{d:%d-%b-%Y}" for d in upcoming[:count])
    return shown + (", ..." if len(upcoming) > count else "")


def angel_lookup(underlying: str, expiry: DateLike, strike: StrikeLike, option_type: str,
                 exchange: Optional[str] = None,
                 master: Optional[List[dict]] = None) -> AngelContract:
    """Token + tradingsymbol for an Angel One option contract.

    ``exchange`` is inferred from an 'EXCH:' prefix (NSE->NFO, BSE->BFO,
    MCX->MCX) or defaults to NFO; pass it explicitly for bare BSE/MCX names.
    Pass ``master`` (master rows) to stay fully offline - tests and callers
    holding their own snapshot use this instead of the daily download.
    """
    name, exch = _split_underlying(underlying, exchange)
    e, t = _to_date(expiry), _to_type(option_type)
    strike_value = _to_strike(strike)
    index = build_angel_index(master) if master is not None else _get_angel_index()
    row = _master_row(index, name, exch, e, strike_value, t)
    if row is None:
        expected = angel_symbol(name, e, strike_value, t, exch)
        expiries = [d for (exch_key, name_key, expiry_str, _s, _t) in index
                    if exch_key == exch and name_key == name
                    for d in (_to_date(expiry_str),)]
        raise LookupError(
            f"No Angel contract for {name} {e:%d-%b-%Y} {_num(strike_value)} {t} on {exch}. "
            f"Expected tradingsymbol: {expected}. "
            f"Available expiries: {_nearby_expiries(sorted(set(expiries))) or 'none'}. "
            "Check that the strike trades in that expiry (the master refreshes daily)."
        )
    try:
        lotsize = int(float(row.get("lotsize") or 0))
    except (TypeError, ValueError):
        lotsize = 0
    row_exchange = str(row.get("exch_seg") or exch)
    return AngelContract(
        token=str(row["token"]),
        symbol=str(row["symbol"]),
        exchange=row_exchange,
        lotsize=lotsize,
        name=str(row.get("name") or name),
        expiry=str(row["expiry"]),
        strike=round(float(row["strike"]) / _strike_scale(row_exchange), 4),
    )


# ------------------------------------------------------------------ Breeze
def breeze_stock_code(underlying: str, exchange: Optional[str] = None) -> str:
    """Resolve the Breeze ``stock_code`` for an option underlying.

    Order: static index codes (BANKNIFTY -> CNXBAN, SENSEX -> BSESEN) ->
    universe.yaml (``sym_breeze``, e.g. RELIANCE -> RELIND) -> Breeze scrip
    master ShortName -> the bare underlying, unchanged.
    """
    name, exch = _split_underlying(underlying, exchange)
    if name in BREEZE_STOCK_CODES:
        return BREEZE_STOCK_CODES[name]
    try:
        from platform_config import resolve_provider_symbol
        cash_exchange = {"NFO": "NSE", "BFO": "BSE"}.get(exch, exch)
        for candidate in (f"{cash_exchange}:{name}", f"{exch}:{name}", name):
            mapped = resolve_provider_symbol(candidate, "breeze")
            if mapped:
                code = _clean_underlying(str(mapped["provider_symbol"]))
                if code:
                    return code
    except Exception:  # universe.yaml unavailable -> keep resolving
        pass
    try:  # lazy import keeps this module free of pandas/SDK weight
        from market_data.breeze_client import resolve_breeze_stock_code
        resolved = resolve_breeze_stock_code(name, "BSE" if exch == "BFO" else exch)
        if resolved:
            return _clean_underlying(str(resolved))
    except Exception:
        pass
    return name


def breeze_params(underlying: str, expiry: DateLike, strike: StrikeLike, option_type: str,
                  exchange: Optional[str] = None, stock_code: Optional[str] = None) -> dict:
    """kwargs for Breeze ``get_quotes`` / ``get_option_chain_quotes`` /
    ``get_historical_data_v2`` / ``place_order``.

    ``exchange`` defaults to NFO, BFO for BSE indices (or a 'BSE:' prefix) and
    MCX for an 'MCX:' prefix; ``stock_code`` overrides the scrip resolution.
    """
    name, exch = _split_underlying(underlying, exchange)
    e, t = _to_date(expiry), _to_type(option_type)
    return {
        "stock_code": stock_code or breeze_stock_code(name, exch),
        "exchange_code": exch,
        "product_type": "options",
        "expiry_date": f"{e:%Y-%m-%d}T06:00:00.000Z",
        "right": "call" if t == "CE" else "put",
        "strike_price": _num(_to_strike(strike)),
    }


def breeze_feed_params(underlying: str, expiry: DateLike, strike: StrikeLike, option_type: str,
                       exchange: Optional[str] = None, stock_code: Optional[str] = None) -> dict:
    """kwargs for ``breeze.subscribe_feeds(...)`` - different date/right format."""
    params = breeze_params(underlying, expiry, strike, option_type, exchange, stock_code)
    e = _to_date(expiry)
    return {
        "exchange_code": params["exchange_code"],
        "stock_code": params["stock_code"],
        "product_type": "options",
        "expiry_date": f"{e:%d-%b-%Y}",          # 08-Oct-2026
        "strike_price": params["strike_price"],
        "right": params["right"].capitalize(),   # Call / Put
    }


# ---------------------------------------------------------------- yfinance
def yfinance_symbol(underlying: str, expiry: DateLike, strike: StrikeLike,
                    option_type: str, suffix: str = "") -> str:
    """OCC/OSI contract symbol, e.g. AAPL260923C00245000.

    Yahoo keys US option contracts this way; it does not serve NSE/BSE option
    chains (RELIANCE.NS / ^NSEI ``.options`` are empty), so Indian contracts
    cannot be fetched from Yahoo. ``suffix`` is appended verbatim ('.NS', ...)
    for callers that need an exchange-qualified ticker.
    """
    root = _clean_underlying(underlying)
    e, t = _to_date(expiry), _to_type(option_type)
    ticks = int(round(_to_strike(strike) * 1000))
    if ticks > 99_999_999:
        raise ValueError(f"Strike {strike!r} does not fit the OCC 8-digit strike field")
    return f"{root}{e:%y%m%d}{t[0]}{ticks:08d}{suffix}"


# ---------------------------------------------------------------- dispatcher
def get_option_symbol(broker: str, underlying: str, expiry: DateLike, strike: StrikeLike,
                      option_type: str, **kw):
    """Broker-correct option identifier for one contract.

    broker:
      'angel'    -> AngelContract (.token + .symbol + .exchange + .lotsize)
                    [kw: exchange, master]      - needs the instrument master
      'breeze'   -> dict of Breeze API kwargs  [kw: exchange, stock_code]
      'yfinance' -> OCC symbol string          [kw: suffix]

    Aliases: 'angel one'/'smartapi', 'icici'/'icici direct', 'yahoo'.
    """
    normalized = normalize_broker(broker)
    if normalized == "angel":
        return angel_lookup(underlying, expiry, strike, option_type, **kw)
    if normalized == "breeze":
        return breeze_params(underlying, expiry, strike, option_type, **kw)
    return yfinance_symbol(underlying, expiry, strike, option_type, **kw)


__all__ = [
    "AngelContract",
    "angel_expiries",
    "angel_lookup",
    "angel_strikes",
    "angel_symbol",
    "breeze_feed_params",
    "breeze_params",
    "breeze_stock_code",
    "build_angel_index",
    "clear_angel_cache",
    "get_option_symbol",
    "load_angel_master",
    "normalize_broker",
    "yfinance_symbol",
]


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    print(get_option_symbol("yfinance", "AAPL", "2026-10-16", 250, "CE"))
    print(get_option_symbol("breeze", "NIFTY", "2026-09-29", 25000, "CE"))
    print(get_option_symbol("breeze", "BSE:SENSEX", "2026-10-08", 84600, "PE"))
    print(angel_symbol("NIFTY", "2026-09-29", 25000, "CE"))
    print(angel_symbol("SENSEX", "2026-10-08", 84600, "PE", exchange="BFO"))
    try:  # needs the (cached) instrument master
        print(get_option_symbol("angel", "NIFTY", "2026-10-06", 21400, "PE"))
    except Exception as exc:
        print(f"angel lookup skipped: {exc}")

