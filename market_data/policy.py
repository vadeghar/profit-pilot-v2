"""Data-provider policy for option/expired-contract backtests.

Breeze is the only configured provider with multi-year expired NFO/BFO/MCX
option-contract history; Angel One and yfinance do not serve it. Any runner
that resolves and prices real option contracts (rather than trading the
underlying spot/futures) must enforce Breeze explicitly rather than trusting
whatever provider happened to be passed in - this makes that requirement a
checked contract instead of an implicit assumption.
"""
from __future__ import annotations

from typing import Any


def require_breeze_provider(provider: Any, *, context: str) -> None:
    """Raise ``ValueError`` unless ``provider`` is Breeze.

    ``context`` names the caller (e.g. "four_indicator_backtest") for a
    useful error message.
    """
    name = getattr(provider, "name", None)
    if name != "breeze":
        raise ValueError(
            f"{context} requires the Breeze provider for option-contract history "
            f"(got {name!r}). Breeze is the only provider with expired-contract "
            f"data; pass a BreezeHistoricalDataProvider instance."
        )


__all__ = ["require_breeze_provider"]
