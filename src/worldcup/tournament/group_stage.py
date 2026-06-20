"""Group stage: round-robin scheduling, standings, and FIFA-style tiebreakers."""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from typing import Optional

from ..engine import MatchSimulator
from ..models import Lineup, MatchResult, PlayedResult, Team


@dataclass
class GroupStanding:
    team: str
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    gf: int = 0
    ga: int = 0
    pts: int = 0

    @property
    def gd(self) -> int:
        return self.gf - self.ga

    def record(self, scored: int, conceded: int) -> None:
        self.played += 1
        self.gf += scored
        self.ga += conceded
        if scored > conceded:
            self.won += 1
            self.pts += 3
        elif scored == conceded:
            self.drawn += 1
            self.pts += 1
        else:
            self.lost += 1


@dataclass
class GroupResult:
    letter: str
    standings: list[GroupStanding]      # sorted best-to-worst
    results: list[MatchResult] = field(default_factory=list)

    @property
    def winner(self) -> GroupStanding:
        return self.standings[0]

    @property
    def runner_up(self) -> GroupStanding:
        return self.standings[1]

    @property
    def third(self) -> GroupStanding:
        return self.standings[2]


def _head_to_head_points(team: str, others: set[str], results: list[MatchResult]) -> tuple[int, int]:
    """(points, goal difference) for ``team`` in games against ``others`` only."""
    pts = gd = 0
    for r in results:
        if r.home == team and r.away in others:
            gd += r.home_goals - r.away_goals
            pts += 3 if r.home_goals > r.away_goals else (1 if r.home_goals == r.away_goals else 0)
        elif r.away == team and r.home in others:
            gd += r.away_goals - r.home_goals
            pts += 3 if r.away_goals > r.home_goals else (1 if r.away_goals == r.home_goals else 0)
    return pts, gd


def rank_standings(
    standings: list[GroupStanding],
    results: list[MatchResult],
    rng: random.Random,
) -> list[GroupStanding]:
    """Order a group: points, goal difference, goals for, then head-to-head among
    still-tied teams, then a random draw of lots (FIFA's final tiebreaker)."""
    ordered = sorted(standings, key=lambda s: (s.pts, s.gd, s.gf), reverse=True)

    # Refine clusters that remain tied on (pts, gd, gf) using head-to-head.
    refined: list[GroupStanding] = []
    i = 0
    while i < len(ordered):
        j = i + 1
        key = (ordered[i].pts, ordered[i].gd, ordered[i].gf)
        while j < len(ordered) and (ordered[j].pts, ordered[j].gd, ordered[j].gf) == key:
            j += 1
        cluster = ordered[i:j]
        if len(cluster) > 1:
            names = {s.team for s in cluster}
            cluster.sort(
                key=lambda s: (*_head_to_head_points(s.team, names - {s.team}, results), rng.random()),
                reverse=True,
            )
        refined.extend(cluster)
        i = j
    return refined


def play_group(
    letter: str,
    teams: list[Team],
    simulator: MatchSimulator,
    rng: random.Random,
    *,
    host_team_names: Optional[set[str]] = None,
    lineups: Optional[dict[str, Lineup]] = None,
    extras: Optional[dict[str, dict]] = None,
    known: Optional[dict[frozenset, PlayedResult]] = None,
) -> GroupResult:
    """Play every pairing in a group once and return the ranked standings.

    ``known`` maps a ``frozenset({home, away})`` to a real ``PlayedResult``;
    such fixtures are recorded as-is instead of being simulated, so the standings
    reflect games that have actually happened and only the rest are drawn."""
    host_team_names = host_team_names or set()
    lineups = lineups or {}
    extras = extras or {}
    known = known or {}
    table = {t.name: GroupStanding(team=t.name) for t in teams}
    results: list[MatchResult] = []

    for home, away in itertools.combinations(teams, 2):
        played = known.get(frozenset((home.name, away.name)))
        if played is not None:
            # Use the real result, keeping its actual home/away orientation.
            result = MatchResult(
                home=played.home, away=played.away,
                home_goals=played.home_goals, away_goals=played.away_goals,
                stage="group",
            )
            results.append(result)
            table[played.home].record(played.home_goals, played.away_goals)
            table[played.away].record(played.away_goals, played.home_goals)
            continue

        # A host nation plays at home (not neutral); everyone else is neutral.
        if home.name in host_team_names and away.name not in host_team_names:
            h, a, neutral = home, away, False
        elif away.name in host_team_names and home.name not in host_team_names:
            h, a, neutral = away, home, False
        else:
            h, a, neutral = home, away, True

        result = simulator.simulate(
            h, a, stage="group", neutral=neutral,
            home_lineup=lineups.get(h.name), away_lineup=lineups.get(a.name),
            home_extras=extras.get(h.name), away_extras=extras.get(a.name),
        )
        results.append(result)
        table[h.name].record(result.home_goals, result.away_goals)
        table[a.name].record(result.away_goals, result.home_goals)

    standings = rank_standings(list(table.values()), results, rng)
    return GroupResult(letter=letter, standings=standings, results=results)


def standings_from_results(
    letter: str,
    team_names: list[str],
    results: list[PlayedResult],
    rng: random.Random,
) -> GroupResult:
    """Build a group's *current* table from already-played results only.

    Unlike :func:`play_group`, nothing is simulated: only fixtures between two
    teams in ``team_names`` that appear in ``results`` are counted, so the table
    reflects exactly the games played so far (teams may have ``played < 3``)."""
    nameset = set(team_names)
    table = {n: GroupStanding(team=n) for n in team_names}
    played: list[MatchResult] = []
    for r in results:
        if r.home in nameset and r.away in nameset:
            table[r.home].record(r.home_goals, r.away_goals)
            table[r.away].record(r.away_goals, r.home_goals)
            played.append(MatchResult(
                home=r.home, away=r.away,
                home_goals=r.home_goals, away_goals=r.away_goals, stage="group",
            ))
    standings = rank_standings(list(table.values()), played, rng)
    return GroupResult(letter=letter, standings=standings, results=played)


def rank_third_placed(groups: list[GroupResult], rng: random.Random, take: int) -> list[GroupStanding]:
    """Rank every group's third-placed team and return the best ``take`` of them."""
    thirds = [g.third for g in groups]
    thirds.sort(key=lambda s: (s.pts, s.gd, s.gf, rng.random()), reverse=True)
    return thirds[:take]
