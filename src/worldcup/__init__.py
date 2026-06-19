"""worldcup — a modular Monte Carlo simulator for the 2026 FIFA World Cup.

Quick start:

    from worldcup import load_world_cup, TournamentSimulator

    teams, tournament = load_world_cup()
    sim = TournamentSimulator(teams, tournament)
    report = sim.monte_carlo(1000)
    for name, p in report.title_odds()[:10]:
        print(f"{name:15s} {p:6.1%}")

Add a new 'variable' by subclassing Factor (see worldcup.factors).
"""

from __future__ import annotations

from .data_loader import (
    TournamentDef,
    load_teams,
    load_tournament,
    load_world_cup,
)
from .engine import MatchSimulator
from .factors import Factor, FactorRegistry, MatchContext, default_registry, register
from .models import Lineup, MatchResult, Player, Team
from .tournament import (
    MonteCarloReport,
    TournamentOutcome,
    TournamentSimulator,
)

__version__ = "0.1.0"

__all__ = [
    "load_world_cup",
    "load_teams",
    "load_tournament",
    "TournamentDef",
    "MatchSimulator",
    "Factor",
    "FactorRegistry",
    "MatchContext",
    "register",
    "default_registry",
    "Player",
    "Team",
    "Lineup",
    "MatchResult",
    "TournamentSimulator",
    "TournamentOutcome",
    "MonteCarloReport",
]
