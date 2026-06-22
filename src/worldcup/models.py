"""Core domain models for the World Cup simulator.

These are deliberately plain dataclasses with no simulation logic so that the
data layer, the factor system, and the engine can all share the same vocabulary
without circular dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Valid player positions. The lineup-aggregation factor groups players by these.
POSITIONS = ("GK", "DEF", "MID", "FWD")


@dataclass(frozen=True)
class Player:
    name: str
    pos: str            # one of POSITIONS
    rating: float       # 0-100
    # Optional Football Manager attributes (1-20 scale), e.g. {"Decisions": 17}.
    # When present, the data loader derives ``rating`` from them via
    # :mod:`worldcup.fm_rating`. Excluded from equality/hash so Player stays
    # hashable despite holding a dict.
    attributes: dict = field(default_factory=dict, compare=False)
    # Current club. Players sharing a club in the same XI generate chemistry
    # (see the chemistry factor). Empty string = unknown / no contribution.
    club: str = ""
    # Age in years, 0 = unknown. The intangibles factor uses it to derive an
    # age curve (veterans decline but swing matches; youngsters are raw but can
    # explode) when no explicit ``xfactor`` is supplied.
    age: int = 0
    # Optional intangible override: {"mean": float, "sigma": float} on the
    # rating scale. ``mean`` is the directional expectation (what we think we
    # know), ``sigma`` the genuine uncertainty (what no one knows). Excluded
    # from equality/hash like ``attributes``.
    xfactor: dict = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if self.pos not in POSITIONS:
            raise ValueError(f"{self.name}: invalid position {self.pos!r}; expected one of {POSITIONS}")


@dataclass
class Team:
    """A national team plus its (curated, editable) squad.

    ``rating`` is the headline strength used by the engine when no lineup is
    selected. A chosen :class:`Lineup` nudges that baseline via the lineup
    factor, so squad quality and selection both matter without requiring a full
    26-player list for every nation.
    """

    name: str
    rating: float                      # base team strength, 0-100
    squad: list[Player] = field(default_factory=list)
    group: Optional[str] = None        # filled in by the tournament loader
    # Manager profile read by the coaching factor:
    #   {"skill": float, "bias": float}
    # ``skill`` lifts both ends (good management); ``bias`` in [-1, 1] tilts the
    # side attacking (+) or defensive (-) without changing net strength.
    coach: dict = field(default_factory=dict)
    # Squad-wide match-day fitness in [0, 1], read by the condition factor as a
    # fallback when no per-match ``extras['condition']`` is given. 1.0 = full
    # strength; lower bakes in a disclosed injury burden (key players out/doubtful).
    condition: float = 1.0
    # Human-readable note on what drives a sub-1.0 condition (disclosed injuries).
    injuries: str = ""

    def player(self, name: str) -> Player:
        for p in self.squad:
            if p.name == name:
                return p
        raise KeyError(f"{name!r} not in {self.name} squad")

    def best_lineup(self, size: int = 11) -> "Lineup":
        """Pick the highest-rated ``size`` players, guaranteeing a goalkeeper."""
        gks = sorted((p for p in self.squad if p.pos == "GK"), key=lambda p: p.rating, reverse=True)
        outfield = sorted((p for p in self.squad if p.pos != "GK"), key=lambda p: p.rating, reverse=True)
        chosen: list[Player] = []
        if gks:
            chosen.append(gks[0])
        chosen.extend(outfield[: max(0, size - len(chosen))])
        return Lineup(team=self.name, players=chosen)


@dataclass
class Lineup:
    """A selected set of players for one team in one match."""

    team: str
    players: list[Player] = field(default_factory=list)

    def by_position(self, pos: str) -> list[Player]:
        return [p for p in self.players if p.pos == pos]

    def mean_rating(self) -> float:
        if not self.players:
            return 0.0
        return sum(p.rating for p in self.players) / len(self.players)


@dataclass(frozen=True)
class PlayedResult:
    """A real, already-played fixture loaded from data/results_2026.json.

    The simulator uses these to seed actual group standings and skip simulating
    games that have happened (only the remaining fixtures are drawn). ``home`` /
    ``away`` are our canonical Team names; goals are the final 90'+ (incl. extra
    time) score.

    For a knockout tie that finished level and went to a shootout, set
    ``home_pens`` / ``away_pens`` to record who advanced — otherwise the winner
    can't be recovered from a level score and the simulator falls back to
    simulating that tie. Group games never need them.
    """

    home: str
    away: str
    home_goals: int
    away_goals: int
    stage: str = "group"
    date: str = ""
    home_pens: int = 0
    away_pens: int = 0

    @property
    def pair(self) -> frozenset:
        """Order-independent key for matching against a fixture pairing."""
        return frozenset((self.home, self.away))

    @property
    def winner(self) -> Optional[str]:
        """Team that advanced: by score, then by recorded penalties. ``None`` if
        the score is level and no shootout winner was recorded (so unknown)."""
        if self.home_goals > self.away_goals:
            return self.home
        if self.away_goals > self.home_goals:
            return self.away
        if self.home_pens != self.away_pens:
            return self.home if self.home_pens > self.away_pens else self.away
        return None


@dataclass
class MatchResult:
    """Outcome of a single match."""

    home: str
    away: str
    home_goals: int
    away_goals: int
    stage: str = "group"
    # Populated only for knockout games that needed them.
    extra_time: bool = False
    penalties: bool = False
    home_pens: int = 0
    away_pens: int = 0
    # Final expected goals the engine used (handy for debugging factors).
    home_xg: float = 0.0
    away_xg: float = 0.0
    # Populated only by the live (time-segmented) engine. Each sub is
    # ``(minute, player_off, player_on)`` and each scorer entry is
    # ``(minute, scorer, assist)`` (``assist`` is "" for an unassisted goal);
    # ``cooling_breaks`` counts hydration breaks taken (hot matches only).
    # Empty/zero for the aggregate engine.
    home_subs: list = field(default_factory=list)
    away_subs: list = field(default_factory=list)
    home_scorers: list = field(default_factory=list)
    away_scorers: list = field(default_factory=list)
    cooling_breaks: int = 0
    date: str = ""

    @property
    def winner(self) -> Optional[str]:
        """Winner by score, then penalties. ``None`` only for a drawn group game."""
        if self.home_goals > self.away_goals:
            return self.home
        if self.away_goals > self.home_goals:
            return self.away
        if self.penalties:
            return self.home if self.home_pens > self.away_pens else self.away
        return None

    @property
    def loser(self) -> Optional[str]:
        w = self.winner
        if w is None:
            return None
        return self.away if w == self.home else self.home

    def score_str(self) -> str:
        s = f"{self.home} {self.home_goals}-{self.away_goals} {self.away}"
        if self.penalties:
            s += f" (pens {self.home_pens}-{self.away_pens})"
        elif self.extra_time:
            s += " (a.e.t.)"
        return s
