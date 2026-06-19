import random

from worldcup.factors import FactorRegistry, MatchContext, TeamMatchState, default_registry
from worldcup.factors.builtin import (
    FatigueFactor,
    FormFactor,
    HomeAdvantageFactor,
    LineupFactor,
)
from worldcup.models import Lineup, Player, Team


def _ctx(home_team: Team, away_team: Team, neutral=True, home=None, away=None):
    return MatchContext(
        home=TeamMatchState.from_team(home_team, is_home=True, **(home or {})),
        away=TeamMatchState.from_team(away_team, is_home=False, **(away or {})),
        neutral=neutral,
        rng=random.Random(0),
    )


def test_default_registry_has_builtins():
    reg = default_registry()
    assert set(reg.names()) >= {"lineup", "home_advantage", "fatigue", "form"}


def test_home_advantage_only_when_not_neutral():
    a, b = Team("A", 80), Team("B", 80)
    f = HomeAdvantageFactor(attack_bonus=3, defense_bonus=2)

    neutral = _ctx(a, b, neutral=True)
    f.adjust(neutral)
    assert neutral.home.attack == 80

    home = _ctx(a, b, neutral=False)
    f.adjust(home)
    assert home.home.attack == 83 and home.home.defense == 82


def test_lineup_factor_weak_xi_reduces_strength():
    squad = [
        Player("GK", "GK", 80),
        Player("D1", "DEF", 85), Player("D2", "DEF", 84),
        Player("M1", "MID", 85), Player("M2", "MID", 83),
        Player("F1", "FWD", 88), Player("F2", "FWD", 60),  # one weak striker
    ]
    team = Team("T", 84, squad=squad)
    weak_xi = Lineup("T", [squad[0], squad[1], squad[3], squad[6]])  # includes the 60-rated FWD

    ctx = MatchContext(
        home=TeamMatchState.from_team(team, is_home=True, lineup=weak_xi),
        away=TeamMatchState.from_team(Team("O", 80), is_home=False),
        rng=random.Random(0),
    )
    LineupFactor().adjust(ctx)
    assert ctx.home.attack < 84  # fielding the weaker striker drops attack


def test_lineup_factor_noop_without_lineup():
    team = Team("T", 84, squad=[Player("F", "FWD", 84)])
    ctx = _ctx(team, Team("O", 80))
    LineupFactor().adjust(ctx)
    assert ctx.home.attack == 84


def test_fatigue_penalises_short_rest():
    a = Team("A", 80)
    ctx = _ctx(a, Team("B", 80), home={"rest_days": 2}, away={"rest_days": 4})
    FatigueFactor(reference_days=4, per_day=0.6).adjust(ctx)
    assert ctx.home.attack < 80  # tired
    assert ctx.away.attack == 80  # reference rest, untouched


def test_form_factor_opt_in():
    a = Team("A", 80)
    ctx = _ctx(a, Team("B", 80), home={"form": 1.0})
    FormFactor(scale=3).adjust(ctx)
    assert ctx.home.attack == 83
    assert ctx.away.attack == 80  # no form set


def test_registry_toggle_and_dedupe():
    reg = FactorRegistry()
    reg.add(HomeAdvantageFactor())
    try:
        reg.add(HomeAdvantageFactor())
        assert False, "duplicate name should raise"
    except ValueError:
        pass
    reg.set_enabled("home_advantage", False)
    ctx = _ctx(Team("A", 80), Team("B", 80), neutral=False)
    reg.apply(ctx)
    assert ctx.home.attack == 80  # disabled, so no boost


def test_custom_factor_integrates():
    from worldcup.factors.base import Factor

    class Altitude(Factor):
        name = "altitude"

        def adjust(self, ctx):
            if ctx.meta.get("venue") == "high":
                ctx.home.attack += 2

    reg = FactorRegistry([Altitude()])
    ctx = _ctx(Team("A", 80), Team("B", 80))
    ctx.meta["venue"] = "high"
    reg.apply(ctx)
    assert ctx.home.attack == 82
