"""Factor system public API and the default registry builder."""

from __future__ import annotations

from .base import (
    Factor,
    FactorRegistry,
    MatchContext,
    TeamMatchState,
    register,
    registered_classes,
)
from . import builtin  # noqa: F401  -- import for @register side effects


def default_registry() -> FactorRegistry:
    """Build a fresh registry containing one instance of every built-in factor.

    The engine uses this when you don't pass your own. Build a custom set by
    instantiating factors yourself and adding them to a :class:`FactorRegistry`,
    or by toggling ``registry.set_enabled(name, False)``.
    """
    registry = FactorRegistry()
    for cls in registered_classes():
        registry.add(cls())
    return registry


__all__ = [
    "Factor",
    "FactorRegistry",
    "MatchContext",
    "TeamMatchState",
    "register",
    "registered_classes",
    "default_registry",
]
