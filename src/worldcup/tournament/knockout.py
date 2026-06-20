"""Single-elimination knockout stage (Round of 32 through the Final).

The bracket follows the **official FIFA 2026 format**: the Round of 32 pairings
are fixed by group-finishing position (see ``R32_MATCHES``), and the eight
third-placed qualifiers are slotted using FIFA's published allocation table
(``data/third_place_allocation_2026.json``, Annex C of the regulations — 495
combinations). Winners then feed forward through R16/QF/SF/Final exactly as in
the official match-number tree.

``build_official_bracket_2026`` turns group results into the 32-team field
already arranged in bracket (leaf) order; ``play_knockout(..., prearranged=True)``
then just pairs neighbours and collapses winners up the tree.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from ..engine import MatchSimulator
from ..models import Lineup, MatchResult, PlayedResult, Team

ROUND_NAMES = {32: "R32", 16: "R16", 8: "QF", 4: "SF", 2: "F"}

# --- Official 2026 Round-of-32 layout ---------------------------------------
# Each match maps to (home, away); every slot is a (kind, group) reference:
#   ("W", X)  winner of group X
#   ("R", X)  runner-up of group X
#   ("3", X)  third-placed team facing the winner of group X (the actual group
#             is resolved through the allocation table — see _resolve below).
# Source: FIFA 2026 fixtures / Wikipedia "2026 FIFA World Cup knockout stage".
R32_MATCHES: dict[int, tuple[tuple[str, str], tuple[str, str]]] = {
    73: (("R", "A"), ("R", "B")),
    74: (("W", "E"), ("3", "E")),
    75: (("W", "F"), ("R", "C")),
    76: (("W", "C"), ("R", "F")),
    77: (("W", "I"), ("3", "I")),
    78: (("R", "E"), ("R", "I")),
    79: (("W", "A"), ("3", "A")),
    80: (("W", "L"), ("3", "L")),
    81: (("W", "D"), ("3", "D")),
    82: (("W", "G"), ("3", "G")),
    83: (("R", "K"), ("R", "L")),
    84: (("W", "H"), ("R", "J")),
    85: (("W", "B"), ("3", "B")),
    86: (("W", "J"), ("R", "H")),
    87: (("W", "K"), ("3", "K")),
    88: (("R", "D"), ("R", "G")),
}

# Order of the R32 matches so that neighbour-pairing + winner-collapse in
# ``play_knockout`` reproduces the official R16/QF/SF/Final match tree
# (R16 89=W74/W77, 90=W73/W75, ... ; QF 97=W89/W90, ... ; SF 101=W97/W98, ...).
R32_LEAF_ORDER: list[int] = [74, 77, 73, 75, 83, 84, 81, 82,
                             76, 78, 79, 80, 86, 88, 85, 87]

_ALLOC_PATH = Path(__file__).resolve().parents[3] / "data" / "third_place_allocation_2026.json"


@lru_cache(maxsize=1)
def _allocation_table() -> dict[str, dict[str, str]]:
    """FIFA's third-place slot table: ``{sorted 8 advancing groups -> {winner-group slot -> third's group}}``."""
    return json.loads(_ALLOC_PATH.read_text(encoding="utf-8"))["allocation"]


def third_place_allocation(advancing_groups: set[str]) -> dict[str, str]:
    """Look up which group's third-placed team fills each winner-group slot.

    ``advancing_groups`` is the set of eight group letters whose third-placed
    team qualified. Returns ``{winner_group_letter: third_place_group_letter}``.
    """
    key = "".join(sorted(advancing_groups))
    table = _allocation_table()
    try:
        return table[key]
    except KeyError:  # pragma: no cover - guards against a malformed third set
        raise ValueError(
            f"no FIFA third-place allocation for advancing groups {key!r} "
            f"(expected exactly 8 of the 12 group letters)"
        )


def build_official_bracket_2026(
    winners: dict[str, Team],
    runners_up: dict[str, Team],
    thirds_by_group: dict[str, Team],
) -> list[Team]:
    """Arrange the 32 qualifiers into official-bracket (leaf) order.

    ``winners`` / ``runners_up`` map every group letter (A-L) to a Team.
    ``thirds_by_group`` maps each of the eight groups whose third-placed team
    advanced to that Team. The returned list is consumed by
    ``play_knockout(..., prearranged=True)``.
    """
    allocation = third_place_allocation(set(thirds_by_group))

    def resolve(ref: tuple[str, str]) -> Team:
        kind, group = ref
        if kind == "W":
            return winners[group]
        if kind == "R":
            return runners_up[group]
        # A third-placed team: the allocation says which group's third plays here.
        return thirds_by_group[allocation[group]]

    ordered: list[Team] = []
    for match_no in R32_LEAF_ORDER:
        home_ref, away_ref = R32_MATCHES[match_no]
        ordered.append(resolve(home_ref))
        ordered.append(resolve(away_ref))
    return ordered


def bracket_seed_order(n: int) -> list[int]:
    """Standard balanced single-elim seed ordering for ``n`` (a power of two).

    Returns 1-based seed numbers arranged so that seed 1 and seed 2 can only
    meet in the final, seed 1 vs seed n in round one, etc.
    """
    order = [1, 2]
    while len(order) < n:
        size = len(order) * 2
        expanded: list[int] = []
        for s in order:
            expanded.append(s)
            expanded.append(size + 1 - s)
        order = expanded
    return order


@dataclass
class KnockoutResult:
    rounds: list[list[MatchResult]] = field(default_factory=list)  # one list per round
    champion: Optional[str] = None
    runner_up: Optional[str] = None

    def round_named(self, name: str) -> list[MatchResult]:
        for rnd in self.rounds:
            if rnd and rnd[0].stage == name:
                return rnd
        return []


def _played_knockout_result(
    played: PlayedResult, home: Team, away: Team, stage: str
) -> Optional[MatchResult]:
    """Turn a real knockout result into a :class:`MatchResult`, or ``None`` if it
    can't decide who advanced (level score with no shootout recorded) — the
    caller then simulates that tie instead of inventing a winner.

    The result keeps the snapshot's own home/away orientation; the winner is
    guaranteed to be one of the two teams contesting this bracket slot.
    """
    winner = played.winner
    if winner is None or winner not in (home.name, away.name):
        return None
    result = MatchResult(
        home=played.home, away=played.away,
        home_goals=played.home_goals, away_goals=played.away_goals,
        stage=stage,
    )
    if played.home_goals == played.away_goals:  # advanced on penalties
        result.extra_time = True
        result.penalties = True
        result.home_pens = played.home_pens
        result.away_pens = played.away_pens
    return result


def play_knockout(
    seeded_teams: list[Team],
    simulator: MatchSimulator,
    rng: random.Random,
    *,
    lineups: Optional[dict[str, Lineup]] = None,
    extras: Optional[dict[str, dict]] = None,
    prearranged: bool = False,
    known: Optional[dict[frozenset, PlayedResult]] = None,
) -> KnockoutResult:
    """Run the bracket on a power-of-two field (32 for the World Cup).

    By default ``seeded_teams`` is ordered best-seed-first and dropped into a
    balanced seed bracket. Pass ``prearranged=True`` when the list is already in
    bracket (leaf) order — e.g. from ``build_official_bracket_2026`` — and it is
    used as-is.

    ``known`` maps a ``frozenset({home, away})`` to a real ``PlayedResult``: a
    tie between two teams with a known, decisive result is recorded as fact
    instead of being simulated, so a part-played bracket stays true to reality
    and only the remaining ties are drawn. A level knockout score with no
    recorded shootout winner is simulated (the advancing side is unknowable from
    the score alone)."""
    lineups = lineups or {}
    extras = extras or {}
    known = known or {}
    n = len(seeded_teams)
    if n & (n - 1) != 0:
        raise ValueError(f"knockout needs a power-of-two field, got {n}")

    if prearranged:
        bracket = list(seeded_teams)
    else:
        # Arrange teams into bracket order so play-down is just pairing neighbours.
        order = bracket_seed_order(n)
        bracket = [seeded_teams[seed - 1] for seed in order]

    result = KnockoutResult()
    current = bracket
    while len(current) > 1:
        stage = ROUND_NAMES[len(current)]
        round_results: list[MatchResult] = []
        winners: list[Team] = []
        for i in range(0, len(current), 2):
            home, away = current[i], current[i + 1]
            played = known.get(frozenset((home.name, away.name)))
            match = _played_knockout_result(played, home, away, stage) if played else None
            if match is None:
                match = simulator.simulate(
                    home, away, stage=stage, neutral=True,
                    home_lineup=lineups.get(home.name), away_lineup=lineups.get(away.name),
                    home_extras=extras.get(home.name), away_extras=extras.get(away.name),
                )
            round_results.append(match)
            winners.append(home if match.winner == home.name else away)
        result.rounds.append(round_results)
        if stage == "F":
            final = round_results[0]
            result.champion = final.winner
            result.runner_up = final.loser
        current = winners
    return result
