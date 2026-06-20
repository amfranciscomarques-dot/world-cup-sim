"""Tournament layer: group stage, knockout bracket, and Monte Carlo simulator."""

from .group_stage import (
    GroupResult,
    GroupStanding,
    play_group,
    rank_third_placed,
    standings_from_results,
)
from .knockout import (
    KnockoutResult,
    build_official_bracket_2026,
    play_knockout,
    third_place_allocation,
)
from .simulator import MonteCarloReport, TournamentOutcome, TournamentSimulator

__all__ = [
    "GroupResult",
    "GroupStanding",
    "play_group",
    "rank_third_placed",
    "standings_from_results",
    "KnockoutResult",
    "play_knockout",
    "build_official_bracket_2026",
    "third_place_allocation",
    "TournamentSimulator",
    "TournamentOutcome",
    "MonteCarloReport",
]
