"""The factor system: the extension point for simulation 'variables'.

A *factor* is any influence on a match — home advantage, lineup quality,
fatigue, morale, weather, altitude, a grudge, whatever you can dream up. Each
factor is a small object that nudges the two teams' effective ``attack`` and
``defense`` ratings before the engine turns them into expected goals.

Adding a new variable is intentionally a three-line job:

    from worldcup.factors.base import Factor, MatchContext, register

    @register
    class AltitudeFactor(Factor):
        name = "altitude"
        def adjust(self, ctx: MatchContext) -> None:
            if ctx.meta.get("venue") == "Mexico City":
                ctx.home.attack += 1.5   # locals are used to the thin air

Because every factor only ever *mutates* the shared :class:`MatchContext`,
factors compose freely and order-independently in the common case. The engine
reads the final numbers; it never needs to know which factors exist.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from ..models import Lineup, Team


@dataclass
class TeamMatchState:
    """Mutable per-team strength while factors are being applied.

    ``attack`` and ``defense`` start at the team's base rating and are adjusted
    by factors. ``extras`` is a free-form scratchpad so factors can stash
    per-team inputs (e.g. ``rest_days``, ``form``) without new dataclass fields.
    """

    team: Team
    is_home: bool
    attack: float
    defense: float
    lineup: Optional[Lineup] = None
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_team(cls, team: Team, is_home: bool, lineup: Optional[Lineup] = None, **extras) -> "TeamMatchState":
        return cls(team=team, is_home=is_home, attack=team.rating, defense=team.rating, lineup=lineup, extras=extras)


@dataclass
class MatchContext:
    """Everything a factor may read or modify for a single match."""

    home: TeamMatchState
    away: TeamMatchState
    stage: str = "group"          # 'group', 'R32', 'R16', 'QF', 'SF', 'F'
    neutral: bool = False         # if True, home advantage factors should no-op
    rng: random.Random = field(default_factory=random.Random)
    meta: dict = field(default_factory=dict)

    def states(self) -> tuple[TeamMatchState, TeamMatchState]:
        return self.home, self.away


class Factor(ABC):
    """Base class for all simulation variables.

    Subclasses set a unique ``name`` and implement :meth:`adjust`. Keep
    ``adjust`` cheap and side-effect-free apart from mutating ``ctx`` — it runs
    for every match of every Monte Carlo iteration.
    """

    name: str = "factor"
    enabled: bool = True

    @abstractmethod
    def adjust(self, ctx: MatchContext) -> None:  # pragma: no cover - interface
        ...

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} enabled={self.enabled}>"


class FactorRegistry:
    """An ordered, mutable collection of factors the engine applies in sequence.

    Order matters only for factors that read another factor's output; the
    built-ins are independent. Use :meth:`apply` to run them all against a
    context.
    """

    def __init__(self, factors: Optional[list[Factor]] = None) -> None:
        self._factors: list[Factor] = list(factors or [])

    def add(self, factor: Factor) -> Factor:
        if any(f.name == factor.name for f in self._factors):
            raise ValueError(f"factor named {factor.name!r} already registered")
        self._factors.append(factor)
        return factor

    def remove(self, name: str) -> None:
        self._factors = [f for f in self._factors if f.name != name]

    def get(self, name: str) -> Factor:
        for f in self._factors:
            if f.name == name:
                return f
        raise KeyError(name)

    def set_enabled(self, name: str, enabled: bool) -> None:
        self.get(name).enabled = enabled

    def names(self) -> list[str]:
        return [f.name for f in self._factors]

    def apply(self, ctx: MatchContext) -> None:
        for f in self._factors:
            if f.enabled:
                f.adjust(ctx)

    def copy(self) -> "FactorRegistry":
        # Factors are shared by reference; that's intentional so toggling an
        # instance's `enabled` is visible everywhere it's used.
        return FactorRegistry(list(self._factors))

    def __iter__(self):
        return iter(self._factors)

    def __len__(self) -> int:
        return len(self._factors)


# A module-level registry of factor *classes* discovered via @register. The
# default engine registry is built from these (see factors/__init__.py), but
# you can always assemble a bespoke FactorRegistry by hand instead.
_REGISTERED_CLASSES: list[type[Factor]] = []


def register(cls: type[Factor]) -> type[Factor]:
    """Class decorator: mark a Factor subclass for inclusion in the default set."""
    if not issubclass(cls, Factor):
        raise TypeError("register expects a Factor subclass")
    _REGISTERED_CLASSES.append(cls)
    return cls


def registered_classes() -> list[type[Factor]]:
    return list(_REGISTERED_CLASSES)
