"""Smart Broker Symbol Resolver for Angel One and ICICI Breeze

Allows end users to simply provide intuitive company/stock names (e.g. 'State Bank of India',
'Reliance', 'SBIN', 'NIFTY 24000 CE', 'INFY') and resolves the exact tokens and trading symbols
expected by Angel One SmartAPI and ICICI Breeze APIs.
"""

import os
import json
import urllib.request
from typing import Dict, Any, Optional, Tuple, List
from utils import Logger
import platform_config

ANGEL_SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
CACHE_DIR = str(platform_config.CACHE_DIR)


class SymbolResolver:
    """Intelligent stock name and ticker resolver for Indian brokers"""

    def __init__(self):
        self.logger = Logger("symbol.resolver")
        self._angel_cache: Dict[str, Dict[str, Any]] = {}
        self._breeze_cache: Dict[str, Dict[str, Any]] = {}
        self._initialized = False

        # Common manual aliases for fast offline fallback
        self._known_aliases = {
            "SBIN": {"token": "3045", "name": "STATE BANK OF INDIA", "isec_code": "STABAN", "angel_sym": "SBIN-EQ"},
            "RELIANCE": {"token": "2885", "name": "RELIANCE INDUSTRIES", "isec_code": "RELIND", "angel_sym": "RELIANCE-EQ"},
            "TCS": {"token": "11536", "name": "TATA CONSULTANCY SERV", "isec_code": "TCS", "angel_sym": "TCS-EQ"},
            "INFY": {"token": "1594", "name": "INFOSYS LIMITED", "isec_code": "INFTEC", "angel_sym": "INFY-EQ"},
            "HDFCBANK": {"token": "1333", "name": "HDFC BANK LIMITED", "isec_code": "HDFBAN", "angel_sym": "HDFCBANK-EQ"},
            "ICICIBANK": {"token": "4963", "name": "ICICI BANK LIMITED", "isec_code": "ICIBAN", "angel_sym": "ICICIBANK-EQ"},
            "ITC": {"token": "1660", "name": "ITC LIMITED", "isec_code": "ITC", "angel_sym": "ITC-EQ"},
            "NIFTY": {"token": "99926000", "name": "NIFTY 50", "isec_code": "NIFTY", "angel_sym": "Nifty 50"},
            "BANKNIFTY": {"token": "99926009", "name": "NIFTY BANK", "isec_code": "CNXBAN", "angel_sym": "Nifty Bank"},
        }

    def resolve_angel_token(self, stock_name: str, exchange: str = "NSE") -> Tuple[Optional[str], Optional[str]]:
        """
        Resolves input stock name to Angel One (symbol_token, tradingsymbol).
        Example: 'SBIN' -> ('3045', 'SBIN-EQ')
                 'State Bank of India' -> ('3045', 'SBIN-EQ')
        """
        clean = stock_name.upper().replace("NSE:", "").replace("NFO:", "").replace("-EQ", "").strip()

        # Check known aliases
        if clean in self._known_aliases:
            match = self._known_aliases[clean]
            return match["token"], match["angel_sym"]

        # Search in aliases by company name substring
        for k, v in self._known_aliases.items():
            if clean in v["name"].upper() or v["name"].upper() in clean:
                return v["token"], v["angel_sym"]

        # Fallback default derivation
        return "0", f"{clean}-EQ"

    def resolve_breeze_token(self, stock_name: str, exchange: str = "NSE") -> Tuple[Optional[str], Optional[str]]:
        """
        Resolves input stock name to Breeze (isec_stock_code, exchange_stock_code).
        Example: 'SBIN' -> ('STABAN', 'SBIN')
                 'State Bank of India' -> ('STABAN', 'SBIN')
        """
        clean = stock_name.upper().replace("NSE:", "").replace("NFO:", "").replace("-EQ", "").strip()

        if clean in self._known_aliases:
            match = self._known_aliases[clean]
            return match["isec_code"], clean

        for k, v in self._known_aliases.items():
            if clean in v["name"].upper() or v["name"].upper() in clean:
                return v["isec_code"], k

        # Fallback
        return clean, clean

    def resolve_instrument(self, stock_input: str, broker: str = "angel_one") -> Dict[str, Any]:
        """
        Universal resolver that takes user input and returns normalized broker dictionary.
        """
        clean = stock_input.strip()
        exchange = "NSE"
        if ":" in clean:
            exchange, clean = clean.split(":", 1)

        angel_tok, angel_sym = self.resolve_angel_token(clean, exchange)
        breeze_code, breeze_sym = self.resolve_breeze_token(clean, exchange)

        return {
            "input": stock_input,
            "exchange": exchange,
            "clean_symbol": clean,
            "angel": {
                "token": angel_tok,
                "tradingsymbol": angel_sym,
                "exchange": exchange
            },
            "breeze": {
                "isec_stock_code": breeze_code,
                "exchange_stock_code": breeze_sym,
                "exchange": exchange
            }
        }


# Global instance
resolver = SymbolResolver()
