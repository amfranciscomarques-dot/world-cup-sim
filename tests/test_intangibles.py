"""Tests for the condition, coaching and intangibles factors."""

from __future__ import annotations

import random

from worldcup.data_loader import load_world_cup
from worldcup.factors.base import MatchContext, TeamMatchState
from worldcup.factors.builtin import (
    CoachFactor,
    ConditionFactor,
    IntangiblesFactor,
    age_xfactor,
)
from worldcup.models import Lineup, Player, Team


def _ctx(home, away, **home_extras):
    return MatchContext(
        home=TeamMatchState.from_team(home, is_home=True, **home_extras),
        away=TeamMatchState.from_team(away, is_home=False),
        rng=random.Random(0),
    )


def _team(**coach):
    sq = [Player(name=f"P{i}", pos="MID", rating=80) for i in range(11)]
    return Team(name="T", rating=80, squad=sq, coach=coach or {})


# --- condition -------------------------------------------------------------

def test_condition_scales_down_when_unfit():
    t = _team()
    ctx = _ctx(t, _team(), condition=0.5)
    ConditionFactor(scale=12.0).adjust(ctx)
    assert ctx.home.attack == 80 - 6.0   # (0.5-1)*12
    assert ctx.home.defense == 80 - 6.0


def test_condition_peak_is_noop_and_missing_untouched():
    t = _team()
    ctx = _ctx(t, _team(), condition=1.0)
    ConditionFactor().adjust(ctx)
    assert ctx.home.attack == 80
    assert ctx.away.attack == 80   # away at full fitness, no condition


def test_condition_falls_back_to_team_value():
    # No extras['condition'] -> the baked team.condition (injury burden) applies.
    t = _team()
    t.condition = 0.9
    ctx = _ctx(t, _team())
    ConditionFactor(scale=12.0).adjust(ctx)
    assert abs(ctx.home.attack - (80 - 1.2)) < 1e-9   # (0.9-1)*12
    assert ctx.away.attack == 80                       # away at default 1.0


def test_extras_condition_overrides_team_value():
    t = _team()
    t.condition = 0.5
    ctx = _ctx(t, _team(), condition=1.0)   # per-match override wins
    ConditionFactor().adjust(ctx)
    assert ctx.home.attack == 80


def test_injured_contenders_have_baked_condition():
    teams, _ = load_world_cup()
    assert teams["Argentina"].condition < 1.0   # Romero/Molina out, Messi doubt
    assert teams["Germany"].condition < 1.0      # ter Stegen out, Musiala doubt
    assert teams["Portugal"].condition == 1.0    # no disclosed injuries to our XI


# --- coaching --------------------------------------------------------------

def test_coach_skill_lifts_both_ends():
    t = _team(skill=2.0, bias=0.0)
    ctx = _ctx(t, _team())
    CoachFactor().adjust(ctx)
    assert ctx.home.attack == 82.0
    assert ctx.home.defense == 82.0


def test_coach_bias_tilts_without_changing_net():
    t = _team(skill=0.0, bias=1.0)
    ctx = _ctx(t, _team())
    CoachFactor(tilt=2.5).adjust(ctx)
    assert ctx.home.attack == 82.5
    assert ctx.home.defense == 77.5
    # net strength (attack+defense) is unchanged by a pure tilt
    assert ctx.home.attack + ctx.home.defense == 160.0


# --- intangibles -----------------------------------------------------------

def test_age_xfactor_veteran_is_negative_mean_large_sigma():
    mean, sigma = age_xfactor(41, 83)
    assert mean < 0          # legs are a minus
    assert sigma > 3.0       # but a big swing either way (experience/morale)


def test_age_xfactor_prime_is_neutral():
    assert age_xfactor(27, 85) == (0.0, 0.4)
    assert age_xfactor(0, 85) == (0.0, 0.0)   # unknown age -> no contribution


def test_age_xfactor_youngster_is_volatile():
    mean, sigma = age_xfactor(18, 89)
    assert mean < 0
    assert sigma > 1.5


def test_intangibles_mean_is_recovered_over_many_draws():
    # A single veteran: average drawn delta should approach his mean.
    p = Player(name="Vet", pos="FWD", rating=83, age=41)
    sq = [p] + [Player(name=f"P{i}", pos="MID", rating=80, age=27) for i in range(10)]
    t = Team(name="T", rating=80, squad=sq)
    lineup = Lineup("T", sq)
    factor = IntangiblesFactor(cap=20.0)   # wide cap so clamping doesn't bias the mean
    deltas = []
    for s in range(4000):
        ctx = MatchContext(
            home=TeamMatchState.from_team(t, is_home=True, lineup=lineup),
            away=TeamMatchState.from_team(_team(), is_home=False),
            rng=random.Random(s),
        )
        factor.adjust(ctx)
        deltas.append(ctx.home.attack - 80)
    avg = sum(deltas) / len(deltas)
    exp_mean = age_xfactor(41, 83)[0]   # prime players contribute ~0 mean
    assert abs(avg - exp_mean) < 0.3


def test_intangibles_explicit_xfactor_overrides_age():
    p = Player(name="X", pos="FWD", rating=80, age=41, xfactor={"mean": 2.0, "sigma": 0.0})
    t = Team(name="T", rating=80, squad=[p])
    ctx = MatchContext(
        home=TeamMatchState.from_team(t, is_home=True, lineup=Lineup("T", [p])),
        away=TeamMatchState.from_team(_team(), is_home=False),
        rng=random.Random(1),
    )
    IntangiblesFactor().adjust(ctx)
    assert ctx.home.attack == 82.0   # sigma 0 -> deterministic +mean


def test_new_factors_in_default_registry():
    teams, _ = load_world_cup()
    from worldcup.engine import MatchSimulator

    names = MatchSimulator(rng=random.Random(0)).registry.names()
    for n in ("condition", "coaching", "intangibles"):
        assert n in names


def test_marquee_veterans_have_ages():
    teams, _ = load_world_cup()
    assert teams["Portugal"].player("Cristiano Ronaldo").age >= 40
    assert teams["Argentina"].player("Lionel Messi").age >= 38
    assert teams["Spain"].player("Lamine Yamal").age <= 20
