"""Full-tournament orchestration and Monte Carlo aggregation."""

import json
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..data_loader import TournamentDef
from ..engine import MatchSimulator
from ..factors import FactorRegistry
from ..models import Lineup, PlayedResult, Team
from .group_stage import GroupResult, play_group, rank_third_placed
from .knockout import KnockoutResult, build_official_bracket_2026, play_knockout

# data/ lives at the repo root
DATA_DIR = Path(__file__).resolve().parents[3] / "data"
FIXTURES_PATH = DATA_DIR / "fixtures_2026.json"

# Milestone a team reaches by *winning* round i of the knockout (R32..F).
_ADVANCE_LABEL = ["R16", "QF", "SF", "Final", "Champion"]
# All stages we track, deepest last, for ordering Monte Carlo reports.
STAGES = ["R32", "R16", "QF", "SF", "Final", "Champion"]


@dataclass
class TournamentOutcome:
    groups: list[GroupResult]
    qualifiers: list[str]               # 32 names, in official bracket (leaf) order
    knockout: KnockoutResult

    @property
    def champion(self) -> Optional[str]:
        return self.knockout.champion

    def deepest_stage(self) -> dict[str, str]:
        """Map every qualifier to the deepest stage it reached."""
        reached: dict[str, str] = {name: "R32" for name in self.qualifiers}
        for idx, rnd in enumerate(self.knockout.rounds):
            label = _ADVANCE_LABEL[idx]
            for match in rnd:
                if match.winner:
                    reached[match.winner] = label
        return reached


@dataclass
class MonteCarloReport:
    iterations: int
    # team -> {stage -> probability of reaching at least that stage}
    reach: dict[str, dict[str, float]] = field(default_factory=dict)

    def title_odds(self) -> list[tuple[str, float]]:
        ranked = sorted(self.reach.items(), key=lambda kv: kv[1].get("Champion", 0.0), reverse=True)
        return [(name, probs.get("Champion", 0.0)) for name, probs in ranked]


class TournamentSimulator:
    """Runs the 2026 format end to end and aggregates Monte Carlo statistics.

    ``lineups`` / ``extras`` are applied to *every* match a team plays, so you
    can ask "what if Argentina rest Messi the whole tournament?" by passing a
    weakened lineup for them once.

    Pass ``results`` (already-played fixtures from ``data_loader.load_results``)
    to make the run *results-aware*: those games are taken as fact and only the
    remaining fixtures are simulated, so odds reflect the tournament so far.
    """

    def __init__(
        self,
        teams: dict[str, Team],
        tournament: TournamentDef,
        registry: Optional[FactorRegistry] = None,
        rng: Optional[random.Random] = None,
        results: Optional[list[PlayedResult]] = None,
    ) -> None:
        self.teams = teams
        self.tournament = tournament
        self.rng = rng or random.Random()
        self.match_sim = MatchSimulator(registry=registry, rng=self.rng)
        self.host_team_names = set(tournament.host_team_names)
        # Played fixtures keyed by team pair for O(1) lookup, split by stage:
        # group games seed play_group, knockout games seed play_knockout.
        self.known_group = {
            r.pair: r for r in (results or []) if r.stage == "group"
        }
        self.known_knockout = {
            r.pair: r for r in (results or []) if r.stage == "knockout"
        }
        # Schedule-aware fatigue
        self.fixtures = []
        if FIXTURES_PATH.exists():
            self.fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
        self.team_last_match: dict[str, str] = {}

    def _get_rest_days(self, team_name: str, current_date_str: str) -> float:
        last_date_str = self.team_last_match.get(team_name)
        if not last_date_str:
            return 4.0  # Default for first match
        fmt = "%Y-%m-%d"
        try:
            d1 = datetime.strptime(last_date_str, fmt)
            d2 = datetime.strptime(current_date_str, fmt)
            return float((d2 - d1).days)
        except ValueError:
            return 4.0

    def _update_extras_with_fatigue(self, home: str, away: str, date_str: str, extras: Optional[dict]) -> dict:
        new_extras = (extras or {}).copy()

        home_rest = self._get_rest_days(home, date_str)
        away_rest = self._get_rest_days(away, date_str)

        # We need to structure this so the MatchSimulator/Factors can read it.
        # MatchSimulator expects home_extras/away_extras.
        # But TournamentSimulator passes one 'extras' dict to play_group/play_knockout.
        # We need to return a dict that play_group can split or use.
        # Actually, TournamentSimulator.run_once passes 'extras' which is dict[str, dict].

        if home not in new_extras:
            new_extras[home] = {}
        if away not in new_extras:
            new_extras[away] = {}

        new_extras[home]["rest_days"] = home_rest
        new_extras[away]["rest_days"] = away_rest

        return new_extras

    def _update_team_last_match(self, home: str, away: str, date_str: str) -> None:
        self.team_last_match[home] = date_str
        self.team_last_match[away] = date_str

    def run_once(
        self,
        lineups: Optional[dict[str, Lineup]] = None,
        extras: Optional[dict[str, dict]] = None,
    ) -> TournamentOutcome:
        self.team_last_match = {}
        # Schedule-aware: Sort played results by date to seed the calendar
        played_sorted = sorted((r for r in self.known_group.values()), key=lambda x: x.date)
        played_sorted += sorted((r for r in self.known_knockout.values()), key=lambda x: x.date)
        for r in played_sorted:
            self.team_last_match[r.home] = r.date
            self.team_last_match[r.away] = r.date

        groups: list[GroupResult] = []
        for letter, names in self.tournament.groups.items():
            team_objs = [self.teams[n] for n in names]
            # Group stage matches are usually at fixed dates.
            # In our simple sim, we'll assume a date sequence or just use results dates.
            # For a more robust fatigue, we need the full schedule dates.
            # For now, we'll let play_group handle its internal sequence if it had dates.
            # Since it doesn't, we'll treat them as a block.
            groups.append(play_group(
                letter, team_objs, self.match_sim, self.rng,
                host_team_names=self.host_team_names, lineups=lineups, extras=extras,
                known=self.known_group,
            ))
            # After each group plays, update last match dates.
            # In a real tournament, groups are interleaved.
            # This is a simplification.
            for g in groups[-1].results:
                # Use a dummy date if not available, or the real one.
                # In play_group, the result.date might be missing for simulated ones.
                d = getattr(g, 'date', '2026-06-25') # Dummy late-group date
                self.team_last_match[g.home] = d
                self.team_last_match[g.away] = d

        # Map each group letter to its winner / runner-up / (advancing) third.
        winners = {g.letter: self.teams[g.winner.team] for g in groups}
        runners_up = {g.letter: self.teams[g.runner_up.team] for g in groups}

        ratings = {n: t.rating for n, t in self.teams.items()}
        thirds = rank_third_placed(
            groups, self.rng, self.tournament.best_third_placed_advance, ratings
        )
        advancing_thirds = {s.team for s in thirds}
        thirds_by_group = {
            g.letter: self.teams[g.third.team]
            for g in groups
            if g.third.team in advancing_thirds
        }

        # Official FIFA 2026 bracket: fixed R32 group-position pairings + the
        # published third-place allocation table, already in leaf order.
        bracket = build_official_bracket_2026(winners, runners_up, thirds_by_group)
        qualifiers = [t.name for t in bracket]

        knockout = play_knockout(
            bracket, self.match_sim, self.rng, lineups=lineups, extras=extras,
            prearranged=True, known=self.known_knockout,
        )
        return TournamentOutcome(groups=groups, qualifiers=qualifiers, knockout=knockout)

    def monte_carlo(
        self,
        iterations: int,
        lineups: Optional[dict[str, Lineup]] = None,
        extras: Optional[dict[str, dict]] = None,
    ) -> MonteCarloReport:
        """Run the whole tournament ``iterations`` times and tally how often each
        team reaches each stage."""
        counts: dict[str, Counter] = {name: Counter() for name in self.teams}
        for _ in range(iterations):
            outcome = self.run_once(lineups=lineups, extras=extras)
            for name, stage in outcome.deepest_stage().items():
                # Reaching a deep stage implies reaching every earlier one.
                depth = STAGES.index(stage)
                for s in STAGES[: depth + 1]:
                    counts[name][s] += 1

        reach = {
            name: {stage: counts[name][stage] / iterations for stage in STAGES}
            for name in self.teams
        }
        return MonteCarloReport(iterations=iterations, reach=reach)
