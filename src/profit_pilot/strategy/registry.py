from __future__ import annotations

from typing import Callable, TypeVar

from profit_pilot.strategy.base import Strategy

S = TypeVar("S", bound=Strategy)

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {}


def register(strategy_type: type[S]) -> type[S]:
    """Class decorator that adds a strategy to the registry by name.

    The registry key is ``strategy_type.registry_name`` (defaults to the class
    name). Re-registering the same name raises :class:`ValueError` so an
    accidental name collision is caught at import time.
    """
    key = getattr(strategy_type, "registry_name", None)
    if key is None or isinstance(key, property):
        # registry_name is an instance property on the base class; when it is
        # not overridden as a class attribute the descriptor itself is returned.
        key = strategy_type.__name__
    if key in STRATEGY_REGISTRY:
        raise ValueError(f"strategy already registered: {key}")  # noqa: TRY003
    STRATEGY_REGISTRY[key] = strategy_type
    return strategy_type


def get_strategy(name: str) -> type[Strategy]:
    """Look up a strategy class by registered name.

    :raises KeyError: no strategy with that name is registered.
    """
    try:
        return STRATEGY_REGISTRY[name]
    except KeyError:
        raise KeyError(f"strategy not registered: {name}") from None


def registered_names() -> tuple[str, ...]:
    """Names of all registered strategies, in registration order."""
    return tuple(STRATEGY_REGISTRY)