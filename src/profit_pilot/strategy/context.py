from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from profit_pilot.data.models import MarketState


@dataclass
class SignalContext:
    """Everything a strategy needs on each call.

    :param state: the current market state (price, timestamp, symbol).
    :param params: strategy parameters (deterministic, frozen per run).
    :param history: rolling recent states for indicator computation, newest
        last. The consumer (backtest / research runner / live feed) owns
        appending each new state; a strategy must treat it as a read-only
        window and NOT mutate it.
    :param reference: symbol/expiry reference data (e.g. forward ATM or
        current-month expiry) keyed by name. Optional.
    """

    state: MarketState
    params: Mapping[str, object] = field(default_factory=dict)
    history: Sequence[MarketState] = field(default_factory=list)
    reference: Mapping[str, object] = field(default_factory=dict)

    @property
    def close(self) -> Sequence[float]:
        """Closing prices across the history window, oldest first."""
        return [s.price for s in self.history] + [self.state.price]

    @property
    def symbol(self) -> str:
        return self.state.symbol