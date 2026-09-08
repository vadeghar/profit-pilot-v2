from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
from typing import Any, Callable

import requests


class NseOptionChainError(RuntimeError):
    """Raised when NSE option-chain fetch/parsing fails."""


@dataclass
class NseOptionLeg:
    ltp: float
    iv: float
    token: str
    symbol: str
    lot_size: int
    volume: int
    oi: int
    oi_change: int
    oi_change_pct: float | None


class NseOptionChainClient:
    _BASE_URL = "https://www.nseindia.com"
    _OPTION_CHAIN_PAGE_URL = "https://www.nseindia.com/option-chain"
    _CHAIN_URL = "https://www.nseindia.com/api/option-chain-v3"
    _CONTRACT_INFO_URL = "https://www.nseindia.com/api/option-chain-contract-info"

    _HTML_HEADERS = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "upgrade-insecure-requests": "1",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    _API_HEADERS = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "referer": "https://www.nseindia.com/option-chain",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }

    @staticmethod
    def _float(value: Any, default: float = 0.0) -> float:
        try:
            if value in ("", None):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _int(value: Any, default: int = 0) -> int:
        try:
            if value in ("", None):
                return default
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(self._HTML_HEADERS)
        return session

    @staticmethod
    def _format_expiry(expiry_date: date) -> str:
        return expiry_date.strftime("%d-%b-%Y")

    def _parse_expiry(self, raw: Any) -> date | None:
        if raw is None:
            return None
        text = str(raw).strip()
        if not text:
            return None
        iso_match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
        if iso_match:
            try:
                return date.fromisoformat(iso_match.group(1))
            except ValueError:
                pass
        text = text.replace("/", "-")
        for fmt in ("%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %b %y", "%d-%B-%Y", "%d %B %Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

    def _extract_cookie_header(self, response: requests.Response, session: requests.Session) -> str:
        cookie_parts: list[str] = []
        cookie_dict = session.cookies.get_dict()
        if "nsit" in cookie_dict:
            cookie_parts.append(f"nsit={cookie_dict['nsit']}")
        if "nseappid" in cookie_dict:
            cookie_parts.append(f"nseappid={cookie_dict['nseappid']}")
        if not cookie_parts:
            raw_headers: list[str] = []
            if hasattr(response.raw, "headers") and hasattr(response.raw.headers, "get_all"):
                raw_headers = response.raw.headers.get_all("Set-Cookie") or []
            elif "Set-Cookie" in response.headers:
                raw_headers = [response.headers["Set-Cookie"]]
            for header in raw_headers:
                for piece in header.split(","):
                    seg = piece.strip()
                    if seg.startswith("nsit=") or seg.startswith("nseappid="):
                        cookie_parts.append(seg.split(";", 1)[0])
        if cookie_parts:
            return "; ".join(cookie_parts)
        if cookie_dict:
            return "; ".join(f"{key}={val}" for key, val in cookie_dict.items())
        return ""

    def _bootstrap_cookies(self, session: requests.Session) -> requests.Response:
        last_error: Exception | None = None
        for url in (self._OPTION_CHAIN_PAGE_URL, self._BASE_URL):
            try:
                resp = session.get(url, timeout=12)
                resp.raise_for_status()
                return resp
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise NseOptionChainError(f"Failed to bootstrap NSE cookies: {last_error}")

    def _request_payload(self, symbol: str, expiry_date: date | None = None) -> dict[str, Any]:
        params = {"type": "Indices", "symbol": symbol.upper()}
        if expiry_date is not None:
            params["expiry"] = self._format_expiry(expiry_date)

        last_error: Exception | None = None
        for _ in range(2):
            session = self._new_session()
            try:
                bootstrap_resp = self._bootstrap_cookies(session)
                cookie_header = self._extract_cookie_header(bootstrap_resp, session)
                headers = dict(self._API_HEADERS)
                if cookie_header:
                    headers["cookie"] = cookie_header
                resp = session.get(self._CHAIN_URL, params=params, headers=headers, timeout=15)
                resp.raise_for_status()
                payload = resp.json()
                if not isinstance(payload, dict):
                    raise NseOptionChainError("Unexpected NSE response payload.")
                return payload
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise NseOptionChainError(f"NSE option-chain request failed: {last_error}")

    def _request_contract_info(self, symbol: str) -> dict[str, Any]:
        params = {"symbol": symbol.upper()}
        last_error: Exception | None = None
        for _ in range(2):
            session = self._new_session()
            try:
                bootstrap_resp = self._bootstrap_cookies(session)
                cookie_header = self._extract_cookie_header(bootstrap_resp, session)
                headers = dict(self._API_HEADERS)
                if cookie_header:
                    headers["cookie"] = cookie_header
                resp = session.get(self._CONTRACT_INFO_URL, params=params, headers=headers, timeout=15)
                resp.raise_for_status()
                payload = resp.json()
                if not isinstance(payload, dict):
                    raise NseOptionChainError("Unexpected NSE contract-info payload.")
                return payload
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise NseOptionChainError(f"NSE contract-info request failed: {last_error}")

    def _extract_rows(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        records = payload.get("records") if isinstance(payload.get("records"), dict) else {}
        rows = records.get("data") if isinstance(records.get("data"), list) else None
        if rows is None:
            rows = payload.get("data") if isinstance(payload.get("data"), list) else []
        if not rows:
            filtered = payload.get("filtered") if isinstance(payload.get("filtered"), dict) else {}
            rows = filtered.get("data") if isinstance(filtered.get("data"), list) else rows
        if not rows and isinstance(payload.get("rows"), list):
            rows = payload.get("rows")
        return [row for row in rows if isinstance(row, dict)]

    def _extract_spot_value(self, payload: dict[str, Any]) -> float:
        records = payload.get("records") if isinstance(payload.get("records"), dict) else {}
        filtered = payload.get("filtered") if isinstance(payload.get("filtered"), dict) else {}
        candidates = [
            payload.get("underlyingValue"),
            payload.get("underlying_value"),
            payload.get("spot"),
            payload.get("indexValue"),
            records.get("underlyingValue"),
            records.get("underlying_value"),
            filtered.get("underlyingValue"),
            filtered.get("underlying_value"),
        ]
        for item in candidates:
            value = self._float(item, 0.0)
            if value > 0:
                return value
        text = str(payload.get("underlying") or records.get("underlying") or filtered.get("underlying") or "")
        if text:
            match = re.search(r"(-?\d+(?:\.\d+)?)", text.replace(",", ""))
            if match:
                value = self._float(match.group(1), 0.0)
                if value > 0:
                    return value
        return 0.0

    def _walk_expiry_candidates(self, node: Any) -> list[date]:
        found: list[date] = []
        if isinstance(node, dict):
            for key, value in node.items():
                lk = str(key).lower()
                if lk in {"expirydates", "expirydates"} and isinstance(value, list):
                    for item in value:
                        parsed = self._parse_expiry(item)
                        if parsed:
                            found.append(parsed)
                elif lk in {"expirydate", "expiry_date", "expiry"}:
                    parsed = self._parse_expiry(value)
                    if parsed:
                        found.append(parsed)
                found.extend(self._walk_expiry_candidates(value))
        elif isinstance(node, list):
            for item in node:
                found.extend(self._walk_expiry_candidates(item))
        return found

    def _extract_option_leg(self, node: Any) -> NseOptionLeg | None:
        if not isinstance(node, dict):
            return None
        ltp = self._float(node.get("lastPrice", node.get("ltp", 0.0)), 0.0)
        iv_raw = self._float(node.get("impliedVolatility", node.get("iv", 0.0)), 0.0)
        iv = iv_raw / 100.0 if iv_raw > 1 else iv_raw
        token = str(node.get("identifier") or node.get("token") or node.get("instrumentToken") or "")
        symbol = str(node.get("symbol") or node.get("tradingSymbol") or token)
        lot_size = self._int(node.get("lotSize", node.get("marketLot", 0)), 0)
        volume = self._int(node.get("totalTradedVolume", node.get("tradedVolume", node.get("volume", 0))), 0)
        oi = self._int(node.get("openInterest", node.get("oi", 0)), 0)
        oi_change = self._int(node.get("changeinOpenInterest", node.get("changeInOpenInterest", node.get("oiChange", 0))), 0)
        oi_change_pct_raw = node.get("pchangeinOpenInterest", node.get("pChangeInOpenInterest", node.get("oiChangePct")))
        oi_change_pct = self._float(oi_change_pct_raw, 0.0) if oi_change_pct_raw not in ("", None) else None
        return NseOptionLeg(
            ltp=ltp,
            iv=iv,
            token=token,
            symbol=symbol,
            lot_size=lot_size,
            volume=volume,
            oi=oi,
            oi_change=oi_change,
            oi_change_pct=oi_change_pct,
        )

    @staticmethod
    def narrow_rows(rows: list[dict[str, Any]], atm: int, num_strikes: int) -> list[dict[str, Any]]:
        rows_sorted = sorted(rows, key=lambda row: row["strike"])
        if num_strikes <= 0 or not rows_sorted:
            return rows_sorted
        center_idx = min(range(len(rows_sorted)), key=lambda idx: abs(rows_sorted[idx]["strike"] - atm))
        start_idx = max(center_idx - num_strikes, 0)
        end_idx = min(center_idx + num_strikes + 1, len(rows_sorted))
        return rows_sorted[start_idx:end_idx]

    def get_expiries(self, symbol: str) -> list[date]:
        parsed: list[date] = []
        contract_payload: dict[str, Any] = {}
        try:
            contract_payload = self._request_contract_info(symbol)
        except Exception:
            contract_payload = {}
        raw_contract_expiries = contract_payload.get("expiryDates") or contract_payload.get("expirydates")
        if isinstance(raw_contract_expiries, list):
            for raw in raw_contract_expiries:
                parsed_date = self._parse_expiry(raw)
                if parsed_date is not None:
                    parsed.append(parsed_date)

        payload = self._request_payload(symbol, None)
        records = payload.get("records") if isinstance(payload.get("records"), dict) else {}
        raw_expiries = records.get("expiryDates") or records.get("expirydates") or payload.get("expiryDates") or payload.get("expirydates")
        if not parsed and isinstance(raw_expiries, list):
            for raw in raw_expiries:
                parsed_date = self._parse_expiry(raw)
                if parsed_date is not None:
                    parsed.append(parsed_date)
        if not parsed:
            for row in self._extract_rows(payload):
                parsed_date = self._parse_expiry(row.get("expiryDate", row.get("expiry_date", row.get("expiry"))))
                if parsed_date is not None:
                    parsed.append(parsed_date)
        if not parsed:
            parsed.extend(self._walk_expiry_candidates(payload))
        return sorted({item for item in parsed})

    def fetch_spot(self, symbol: str) -> float:
        payload = self._request_payload(symbol=symbol, expiry_date=None)
        spot = self._extract_spot_value(payload)
        if spot > 0:
            return spot
        expiries = self.get_expiries(symbol)
        if expiries:
            chain = self.fetch_option_chain(symbol=symbol, expiry_date=expiries[0])
            spot = self._float(chain.get("spot"), 0.0)
        if spot > 0:
            return spot
        raise NseOptionChainError(f"NSE spot missing/invalid for symbol={symbol}.")

    def fetch_option_chain(self, symbol: str, expiry_date: date) -> dict[str, Any]:
        payload = self._request_payload(symbol=symbol, expiry_date=expiry_date)
        rows = self._extract_rows(payload)
        if not rows:
            payload = self._request_payload(symbol=symbol, expiry_date=None)
            rows = self._extract_rows(payload)
            if not rows:
                raise NseOptionChainError("NSE option chain response has no rows.")
        records = payload.get("records") if isinstance(payload.get("records"), dict) else {}
        filtered = payload.get("filtered") if isinstance(payload.get("filtered"), dict) else {}
        spot = self._extract_spot_value(payload)
        timestamp = str(payload.get("timestamp") or records.get("timestamp") or filtered.get("timestamp") or "")

        parsed_rows: list[dict[str, Any]] = []
        total_call_oi = 0
        total_put_oi = 0
        for row in rows:
            row_exp = self._parse_expiry(row.get("expiryDate") or row.get("expiry_date") or row.get("expiry") or row.get("expDate"))
            if row_exp is not None and row_exp != expiry_date:
                continue
            strike = self._int(row.get("strikePrice", row.get("strike_price")), 0)
            if strike <= 0:
                continue
            ce = self._extract_option_leg(row.get("CE", row.get("ce", row.get("call_options"))))
            pe = self._extract_option_leg(row.get("PE", row.get("pe", row.get("put_options"))))
            if ce:
                total_call_oi += ce.oi
            if pe:
                total_put_oi += pe.oi
            parsed_rows.append({"strike": strike, "expiry": (row_exp or expiry_date).isoformat(), "call": ce, "put": pe})
        if not parsed_rows:
            raise NseOptionChainError(f"NSE returned no rows for symbol={symbol} expiry={expiry_date.isoformat()}.")
        parsed_rows.sort(key=lambda row: row["strike"])
        pcr = (total_put_oi / total_call_oi) if total_call_oi > 0 else 0.0
        return {"spot": spot, "timestamp": timestamp, "rows": parsed_rows, "pcr": pcr}

class NseMarketDataError(RuntimeError):
    """Raised when an NSE market snapshot request fails."""


class NseMarketDataClient(NseOptionChainClient):
    """Single NSE HTTP client for option-chain and market analytics data."""

    _NEXT_API_URL = "https://www.nseindia.com/api/NextApi/apiClient"
    _FIFTY_TWO_WEEK_HIGH_URL = "https://www.nseindia.com/api/live-analysis-data-52weekhighstock"
    _FIFTY_TWO_WEEK_LOW_URL = "https://www.nseindia.com/api/live-analysis-data-52weeklowstock"

    def _request_next_api(self, function_name: str, params: dict[str, Any] | None = None) -> Any:
        query = {"functionName": function_name, **(params or {})}
        last_error: Exception | None = None
        for _ in range(2):
            session = self._new_session()
            try:
                bootstrap_resp = self._bootstrap_cookies(session)
                headers = dict(self._API_HEADERS)
                headers["referer"] = "https://www.nseindia.com/"
                cookie_header = self._extract_cookie_header(bootstrap_resp, session)
                if cookie_header:
                    headers["cookie"] = cookie_header
                response = session.get(self._NEXT_API_URL, params=query, headers=headers, timeout=15)
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict) and payload.get("status") is False:
                    raise NseMarketDataError(str(payload.get("message") or "NSE rejected the request"))
                return payload
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise NseMarketDataError(f"NSE {function_name} request failed: {last_error}")

    @staticmethod
    def _data(payload: Any) -> Any:
        return payload.get("data") if isinstance(payload, dict) and "data" in payload else payload

    def fetch_market_snapshot(self, kind: str) -> list[dict[str, Any]]:
        value = self._data(self._request_next_api("getMarketSnapshot", {"type": kind}))
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if not isinstance(value, dict):
            return []
        keys = {"G": "topGainers", "L": "topLoosers", "MAVA": "mostActiveValue", "MAVO": "mostActiveVolume", "EW": "etfWatchValue"}
        rows = value.get(keys.get(kind, kind), [])
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def fetch_market_statistics(self) -> dict[str, Any]:
        value = self._data(self._request_next_api("getMarketStatistics"))
        return value if isinstance(value, dict) else {}

    def fetch_indices(self) -> list[dict[str, Any]]:
        value = self._data(self._request_next_api("getIndexData", {"type": "All"}))
        return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []

    def fetch_active_contracts(self) -> dict[str, Any]:
        value = self._data(self._request_next_api("getActiveContracts"))
        return value if isinstance(value, dict) else {}

    def fetch_marquee(self) -> list[dict[str, Any]]:
        value = self._data(self._request_next_api("getMarqueData"))
        return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []

    def _request_direct_api(self, url: str, referer: str, retries: int = 2, timeout: int = 15) -> Any:
        last_error: Exception | None = None
        for _ in range(retries):
            session = self._new_session()
            try:
                bootstrap_resp = self._bootstrap_cookies(session)
                headers = dict(self._API_HEADERS)
                headers["referer"] = referer
                cookie_header = self._extract_cookie_header(bootstrap_resp, session)
                if cookie_header:
                    headers["cookie"] = cookie_header
                response = session.get(url, headers=headers, timeout=timeout)
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise NseMarketDataError(f"NSE direct API request failed: {last_error}")

    def _fetch_52_week(self, url: str, referer: str) -> list[dict[str, Any]]:
        value = self._data(self._request_direct_api(url, referer))
        if isinstance(value, dict):
            for key in ("data", "stocks", "records", "equityStocks"):
                if isinstance(value.get(key), list):
                    value = value[key]
                    break
            else:
                count_key = "high" if "high" in value else "low" if "low" in value else None
                if count_key and isinstance(value[count_key], (int, float)):
                    return [{"count": int(value[count_key])}]
        return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []

    def fetch_52_week_highs(self) -> list[dict[str, Any]]:
        return self._fetch_52_week(self._FIFTY_TWO_WEEK_HIGH_URL, "https://www.nseindia.com/market-data/52-week-high-equity-market")

    def fetch_52_week_lows(self) -> list[dict[str, Any]]:
        return self._fetch_52_week(self._FIFTY_TWO_WEEK_LOW_URL, "https://www.nseindia.com/market-data/52-week-low-equity-market")

    def fetch_market_data(self, on_result: Callable[[str, Any], None] | None = None) -> dict[str, Any]:
        """Fetch all non-option-chain analytics data through this client only."""
        result: dict[str, Any] = {"errors": []}
        calls = {
            "gainers": lambda: self.fetch_market_snapshot("G"),
            "losers": lambda: self.fetch_market_snapshot("L"),
            "activeValue": lambda: self.fetch_market_snapshot("MAVA"),
            "activeVolume": lambda: self.fetch_market_snapshot("MAVO"),
            "statistics": self.fetch_market_statistics,
            "indices": self.fetch_indices,
            "activeContracts": self.fetch_active_contracts,
            "marquee": self.fetch_marquee,
            "newHighs": self.fetch_52_week_highs,
            "newLows": self.fetch_52_week_lows,
        }
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            futures = {pool.submit(call): name for name, call in calls.items()}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    result[name] = future.result()
                    if on_result:
                        on_result(name, result[name])
                except Exception as error:  # noqa: BLE001
                    result["errors"].append(f"{name}: {error}")
                    result[name] = [] if name not in {"statistics", "activeContracts"} else {}
        if on_result:
            on_result("complete", result)
        return result

    def fetch_card_data(self, card: str) -> dict[str, Any]:
        groups = {
            "summary": {"statistics": self.fetch_market_statistics, "newHighs": self.fetch_52_week_highs, "newLows": self.fetch_52_week_lows},
            "breadth": {"statistics": self.fetch_market_statistics},
            "momentumLeaders": {"marquee": self.fetch_marquee},
            "volatility": {"indices": self.fetch_indices},
            "marketCards": {"gainers": lambda: self.fetch_market_snapshot("G"), "losers": lambda: self.fetch_market_snapshot("L"), "activeValue": lambda: self.fetch_market_snapshot("MAVA"), "activeVolume": lambda: self.fetch_market_snapshot("MAVO"), "newHighs": self.fetch_52_week_highs, "newLows": self.fetch_52_week_lows},
            "activeContracts": {"activeContracts": self.fetch_active_contracts},
        }
        if card not in groups:
            raise ValueError(f"Unsupported analytics card: {card}")
        result: dict[str, Any] = {"errors": []}
        calls = groups[card]
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            futures = {pool.submit(call): name for name, call in calls.items()}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    result[name] = future.result()
                except Exception as error:  # noqa: BLE001
                    result[name] = [] if name != "statistics" else {}
                    result["errors"].append(f"{name}: {error}")
        return result


_market_client = NseMarketDataClient()


def fetch_market_data(on_result: Callable[[str, Any], None] | None = None) -> dict[str, Any]:
    """Module-level market-data entry point for Analytics."""
    return _market_client.fetch_market_data(on_result=on_result)
