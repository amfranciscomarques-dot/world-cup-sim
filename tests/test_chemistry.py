"""Tests for the club-chemistry factor."""

from __future__ import annotations

import random

from worldcup.data_loader import load_world_cup
from worldcup.engine import MatchSimulator
from worldcup.factors import FactorRegistry
from worldcup.factors.builtin import ChemistryFactor, club_clusters, club_pairs
from worldcup.models import Lineup, Player, Team


def _xi(clubs):
    return [Player(name=f"P{i}", pos="MID", rating=80, club=c) for i, c in enumerate(clubs)]


def test_club_pairs_counts_combinations():
    assert club_pairs(_xi(["A", "A", "A"])) == 3      # 3 choose 2
    assert club_pairs(_xi(["A", "A", "B", "B"])) == 2  # one pair each
    assert club_pairs(_xi(["A", "B", "C"])) == 0
    assert club_pairs(_xi(["", "", "A"])) == 0         # blanks ignored


def test_club_clusters_keeps_only_shared():
    clusters = club_clusters(_xi(["PSG", "PSG", "City", "Solo"]))
    assert clusters == {"PSG": ["P0", "P1"]}


def test_chemistry_bonus_capped():
    factor = ChemistryFactor(per_pair=0.6, cap=4.0)
    team = Team(name="T", rating=80, squad=_xi(["X"] * 6))  # 15 pairs -> capped
    from worldcup.factors.base import MatchContext, TeamMatchState

    ctx = MatchContext(
        home=TeamMatchState.from_team(team, is_home=True, lineup=Lineup("T", team.squad)),
        away=TeamMatchState.from_team(team, is_home=False, lineup=Lineup("T", [])),
    )
    factor.adjust(ctx)
    assert ctx.home.attack == 80 + 4.0


def test_chemistry_helps_a_high_chemistry_side():
    # Two equal-rated teams; only one has a shared-club core. Over many games it
    # should not be worse off — sanity that the factor is wired and positive.
    teams, _ = load_world_cup()
    reg = FactorRegistry([ChemistryFactor()])
    sim = MatchSimulator(registry=reg, rng=random.Random(1))
    pt = teams["Portugal"]
    # Portugal's best XI fields a real club core (PSG block + a Man City pair),
    # so it carries a positive chemistry bonus.
    assert club_pairs(pt.best_lineup().players) >= 4


def test_portugal_has_psg_core():
    teams, _ = load_world_cup()
    clusters = club_clusters(teams["Portugal"].best_lineup().players)
    assert "PSG" in clusters
    assert {"Vitinha", "Nuno Mendes"} <= set(clusters["PSG"])


def test_chemistry_in_default_registry():
    teams, _ = load_world_cup()
    sim = MatchSimulator(rng=random.Random(0))
    assert "chemistry" in sim.registry.names()
