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


def _head_to_head_stats(
    team: str, others: set[str], results: list[MatchResult]
) -> tuple[int, int, int]:
    """(points, goal difference, goals for) for ``team`` in games against
    ``others`` only — the head-to-head mini-table FIFA's first tiebreaks use."""
    pts = gd = gf = 0
    for r in results:
        if r.home == team and r.away in others:
            gf += r.home_goals
            gd += r.home_goals - r.away_goals
            pts += 3 if r.home_goals > r.away_goals else (1 if r.home_goals == r.away_goals else 0)
        elif r.away == team and r.home in others:
            gf += r.away_goals
            gd += r.away_goals - r.home_goals
            pts += 3 if r.away_goals > r.home_goals else (1 if r.away_goals == r.home_goals else 0)
    return pts, gd, gf


def _resolve_overall(
    cluster: list[GroupStanding], rng: random.Random, ratings: dict[str, float]
) -> list[GroupStanding]:
    """Break a still-tied cluster with the group-wide criteria, applied in order:
    overall goal difference, overall goals scored, then the FIFA ranking (proxied
    by team rating — the highest curated strength stands in for the highest
    ranked side) and finally a random draw of lots.

    Fair play points (cards) sit between goals-scored and the FIFA ranking in the
    official order but the engine does not model bookings, so that criterion is
    skipped here; add it once cards are tracked."""
    return sorted(
        cluster,
        key=lambda s: (s.gd, s.gf, ratings.get(s.team, 0.0), rng.random()),
        reverse=True,
    )


def _resolve_tied(
    cluster: list[GroupStanding],
    results: list[MatchResult],
    rng: random.Random,
    ratings: dict[str, float],
) -> list[GroupStanding]:
    """Rank teams level on points using FIFA's head-to-head procedure.

    The head-to-head mini-table (points, then goal difference, then goals scored)
    is built from *only* the matches among the tied teams. Where that leaves a
    smaller subset still level, the criteria are re-applied to just those teams
    (recomputing the mini-table among them). Only when head-to-head cannot
    separate the cluster at all do the group-wide criteria take over."""
    if len(cluster) == 1:
        return cluster

    names = {s.team for s in cluster}
    h2h = {s.team: _head_to_head_stats(s.team, names - {s.team}, results) for s in cluster}
    ordered = sorted(cluster, key=lambda s: h2h[s.team], reverse=True)

    out: list[GroupStanding] = []
    i = 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and h2h[ordered[j].team] == h2h[ordered[i].team]:
            j += 1
        sub = ordered[i:j]
        if len(sub) == 1:
            out.extend(sub)
        elif len(sub) == len(cluster):
            # Head-to-head separated nothing: fall through to the overall criteria.
            out.extend(_resolve_overall(sub, rng, ratings))
        else:
            # A smaller still-level subset: re-apply head-to-head among just these.
            out.extend(_resolve_tied(sub, results, rng, ratings))
        i = j
    return out


def rank_standings(
    standings: list[GroupStanding],
    results: list[MatchResult],
    rng: random.Random,
    ratings: Optional[dict[str, float]] = None,
) -> list[GroupStanding]:
    """Order a group by the FIFA 2026 group-stage criteria, in strict order:

    1. Overall points.
    2-4. Head-to-head among the tied teams: points, then goal difference, then
         goals scored (re-applied to any smaller subset that stays level).
    5-6. Overall goal difference, then overall goals scored.
    7. Fair play points (not modelled — see :func:`_resolve_overall`).
    8. FIFA ranking (proxied by ``ratings``), then a drawing of lots.

    Since the 48-team expansion, head-to-head outranks overall goal difference,
    so the tied teams are settled by their mutual results before the group-wide
    numbers are consulted. ``ratings`` maps team name to its FIFA-ranking proxy;
    when omitted the final split is a pure random draw."""
    ratings = ratings or {}
    by_points = sorted(standings, key=lambda s: s.pts, reverse=True)

    ranked: list[GroupStanding] = []
    i = 0
    while i < len(by_points):
        j = i + 1
        while j < len(by_points) and by_points[j].pts == by_points[i].pts:
            j += 1
        cluster = by_points[i:j]
        ranked.extend(
            _resolve_tied(cluster, results, rng, ratings) if len(cluster) > 1 else cluster
        )
        i = j
    return ranked


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

    ratings = {t.name: t.rating for t in teams}
    standings = rank_standings(list(table.values()), results, rng, ratings)
    return GroupResult(letter=letter, standings=standings, results=results)


def standings_from_results(
    letter: str,
    team_names: list[str],
    results: list[PlayedResult],
    rng: random.Random,
    ratings: Optional[dict[str, float]] = None,
) -> GroupResult:
    """Build a group's *current* table from already-played results only.

    Unlike :func:`play_group`, nothing is simulated: only fixtures between two
    teams in ``team_names`` that appear in ``results`` are counted, so the table
    reflects exactly the games played so far (teams may have ``played < 3``).
    ``ratings`` is the optional FIFA-ranking proxy passed to :func:`rank_standings`."""
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
    standings = rank_standings(list(table.values()), played, rng, ratings)
    return GroupResult(letter=letter, standings=standings, results=played)


def rank_third_placed(
    groups: list[GroupResult],
    rng: random.Random,
    take: int,
    ratings: Optional[dict[str, float]] = None,
) -> list[GroupStanding]:
    """Rank every group's third-placed team and return the best ``take`` of them.

    These teams come from different groups, so there is no head-to-head to apply;
    FIFA uses the simplified order points, overall goal difference, overall goals
    scored, fair play (not modelled), then FIFA ranking (proxied by ``ratings``)
    and finally a drawing of lots."""
    ratings = ratings or {}
    thirds = [g.third for g in groups]
    thirds.sort(
        key=lambda s: (s.pts, s.gd, s.gf, ratings.get(s.team, 0.0), rng.random()),
        reverse=True,
    )
    return thirds[:take]
