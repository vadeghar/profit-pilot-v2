from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Mapping

from profit_pilot.data.models import MarketState
from profit_pilot.strategy.context import SignalContext
from profit_pilot.strategy.signal import Signal, SignalAction


@dataclass
class Strategy(ABC):
    """Base class for ProfitPilot strategies.

    Concrete strategies subclass this, declare :attr:`name` and additional
    parameters, and implement :meth:`on_market_state`. The registry key is
    the class name by default; the decorator in :mod:`registry` auto-registers
    subclasses.

    Existing code that treats ``Strategy`` as a structural ``Protocol`` relies
    on the single required method :meth:`on_market_state`, so that signature is
    preserved. The richer :meth:`on_signal_context` hook is the default entry
    point for strategies that need reference data and a rolling history.

    Every strategy participates in the signal lifecycle:

    ``on_signal_context(ctx) -> Signal``
        Produce a raw :class:`Signal` from a :class:`SignalContext`.

    ``normalise(signal, state, ctx) -> Signal``
        Base implementation of the signal engine: maps :data:`SignalAction.HOLD`
        to a zero-quantity HOLD and clamps a numeric quantity to a whole
        non-negative integer. Subclasses wrap or override this to apply
        quantity sizing / risk rules. Overridden ``normalise`` should call
        ``super()`` *or* reproduce the same invariants so downstream code never
        sees a negative or fractional quantity.
    """

    name: str = "anonymous"
    params: Mapping[str, object] = field(default_factory=dict)

    @property
    @abstractmethod
    def symbol(self) -> str:
        """Symbol this strategy trades (e.g. ``NIFTY``)."""

    @property
    def registry_name(self) -> str:
        """Key used by the strategy registry; class name by default."""
        return self.__class__.__name__

    @abstractmethod
    def on_signal_context(self, ctx: SignalContext) -> Signal:
        """Compute the strategy's raw signal from a signal context."""

    def on_market_state(self, state: MarketState) -> Signal:
        """Backwards-compatible hook: compute a signal from a bare state.

        The default implementation builds a single-point ``SignalContext`` from
        :paramref:`state` and forwards to :meth:`on_signal_context`. This keeps
        :class:`Strategy` usable wherever a structural ``Strategy`` (one-method
        protocol) is accepted, e.g. the backtest engine.
        """
        ctx = SignalContext(state=state, params=self.params)
        return self.normalise(self.on_signal_context(ctx), state, ctx)

    def normalise(self, signal: Signal, state: MarketState, ctx: SignalContext) -> Signal:
        """Signal-engine step: produce a tradeable, validated Signal."""
        if signal.action.value == "HOLD":
            return Signal(SignalAction.HOLD, signal.symbol, 0, signal.metadata)
        qty = int(signal.quantity)
        if qty < 0:
            raise ValueError(f"signal quantity must be non-negative, got {signal.quantity}")  # noqa: TRY003
        return Signal(signal.action, signal.symbol, qty, signal.metadata)