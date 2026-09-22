"""Universe Manager & Stock Resolution for NSE Stocks & Indices"""

from typing import List, Dict, Set, Optional

# Pre-defined curated stock universes for NSE
NIFTY_50 = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "ITC", "SBIN",
    "BHARTIARTL", "LICI", "KOTAKBANK", "LT", "HCLTECH", "AXISBANK", "BAJFINANCE",
    "ASIANPAINT", "MARUTI", "SUNPHARMA", "TITAN", "BAJAJFINSV", "ULTRACEMCO",
    "TATAMOTORS", "NTPC", "ONGC", "JSWSTEEL", "POWERGRID", "M&M", "ADANIENT",
    "COALINDIA", "TATASTEEL", "ADANIPORTS", "IOC", "PIDILITIND", "SIEMENS", "GRASIM",
    "HINDALCO", "BPCL", "VEDL", "EICHERMOT", "NESTLEIND", "DRREDDY", "WIPRO",
    "BRITANNIA", "CIPLA", "TECHM", "SHREECEM", "HEROMOTOCO", "APOLLOHOSP", "DIVISLAB", "TATACONSUM"
]

NIFTY_NEXT_50 = [
    "BEL", "HAL", "VBL", "CHOLAFIN", "TVSMOTOR", "HAVELLS", "AMBUJACEM", "INDIGO",
    "BAJAJHLDNG", "ZOMATO", "DLF", "BANKBARODA", "CANBK", "TORNTPHARM", "GAIL",
    "BOSCHLTD", "SRF", "ABB", "PFC", "RECLTD", "MOTHERSON", "TRENT", "JINDALSTEL"
]

NIFTY_BANK = [
    "HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK",
    "BANKBARODA", "PNB", "FEDERALBNK", "IDFCFIRSTB", "AUBANK", "BANDHANBNK"
]

NIFTY_IT = [
    "TCS", "INFY", "HCLTECH", "WIPRO", "TECHM", "LTIM", "PERSISTENT", "COFORGE", "MPHASIS", "LTTS"
]

UNIVERSES = {
    "NIFTY50": NIFTY_50,
    "NIFTY_NEXT50": NIFTY_NEXT_50,
    "NIFTY_BANK": NIFTY_BANK,
    "NIFTY_IT": NIFTY_IT,
}


class UniverseManager:
    """Manages stock universe definitions and expands universe tags or comma-separated lists"""

    @classmethod
    def list_universes(cls) -> List[str]:
        return list(UNIVERSES.keys())

    @classmethod
    def get_universe(cls, name: str) -> List[str]:
        key = name.upper().replace("-", "_").replace(" ", "_")
        return UNIVERSES.get(key, [])

    @classmethod
    def resolve_instruments(cls, target: str, prefix: str = "NSE:") -> List[str]:
        """
        Resolves input string to a clean list of prefixed instrument symbols.
        Supports:
          - Preset universes: 'NIFTY50', 'NIFTY_BANK', 'NIFTY_IT'
          - Comma-separated tickers: 'RELIANCE, TCS, INFY, SBIN'
          - Single symbol: 'RELIANCE' -> ['NSE:RELIANCE']
          - Slice of universe: 'NIFTY50:10' -> top 10 stocks of Nifty 50
        """
        target = target.strip()
        if not target:
            return []

        # Check for slice like NIFTY50:10 (top 10 stocks)
        limit = None
        if ":" in target and not target.upper().startswith("NSE:") and not target.upper().startswith("NFO:") and not target.upper().startswith("MCX:"):
            parts = target.split(":", 1)
            universe_name = parts[0].strip().upper()
            try:
                limit = int(parts[1].strip())
            except ValueError:
                limit = None
            stocks = cls.get_universe(universe_name)
            if stocks:
                selected = stocks[:limit] if limit else stocks
                return [f"{prefix}{s}" for s in selected]

        # Check standard universe
        upper_target = target.upper().replace("-", "_").replace(" ", "_")
        if upper_target in UNIVERSES:
            return [f"{prefix}{s}" for s in UNIVERSES[upper_target]]

        # Comma-separated list or individual symbols
        tokens = [t.strip() for t in target.split(",") if t.strip()]
        resolved = []
        for t in tokens:
            upper_t = t.upper()
            if upper_t in UNIVERSES:
                resolved.extend([f"{prefix}{s}" for s in UNIVERSES[upper_t]])
            elif ":" in t:
                resolved.append(upper_t)
            elif upper_t.startswith("MCX_") or upper_t.startswith("MCX:"):
                resolved.append(upper_t)
            else:
                resolved.append(f"{prefix}{upper_t}")

        # Return de-duplicated list preserving order
        seen: Set[str] = set()
        deduped = []
        for sym in resolved:
            if sym not in seen:
                seen.add(sym)
                deduped.append(sym)
        return deduped
