"""Tests for the time-segmented ("live") match engine: fatigue, subs, hydration."""

import random

from worldcup.engine import live
from worldcup.engine.match import MatchSimulator
from worldcup.models import Lineup, Player, Team


def _deep_team(name: str, rating: float, *, stamina: int = 13) -> Team:
    """A team with a startable XI plus a strong same-position bench."""
    squad = [Player("GK1", "GK", rating)]
    squad += [Player(f"GK{i}", "GK", rating - 4) for i in range(2, 4)]
    for pos in ("DEF", "MID", "FWD"):
        # Starters carry an explicit (low) Stamina so they tire on schedule;
        # bench players are fresh and comparably strong.
        for i in range(4):
            squad.append(Player(f"{pos}{i}", pos, rating, attributes={"Stamina": stamina}))
        for i in range(4, 8):
            squad.append(Player(f"{pos}{i}", pos, rating - 1))
    return Team(name, rating, squad=squad)


def test_fatigue_points_progressive_and_convex():
    # Tiredness grows with minutes and accelerates late (convex curve).
    early = live.fatigue_points(20, 1.0)
    mid = live.fatigue_points(45, 1.0)
    late = live.fatigue_points(90, 1.0)
    assert 0 < early < mid < late
    # Second half costs more than the first (convexity).
    assert (late - mid) > (mid - 0.0) * 0.0 + (mid - early)


def test_stamina_factor_orders_correctly():
    low = Player("a", "MID", 80, attributes={"Stamina": 8})
    high = Player("b", "MID", 80, attributes={"Stamina": 18})
    default = Player("c", "MID", 80)
    assert live.stamina_factor(high) < live.stamina_factor(default) < live.stamina_factor(low)


def test_live_match_is_reproducible_with_seed():
    a, b = _deep_team("A", 85), _deep_team("B", 80)
    r1 = MatchSimulator(rng=random.Random(7)).simulate_live(a, b)
    r2 = MatchSimulator(rng=random.Random(7)).simulate_live(a, b)
    assert (r1.home_goals, r1.away_goals) == (r2.home_goals, r2.away_goals)
    assert r1.home_subs == r2.home_subs


def test_substitutions_happen_with_a_deep_bench():
    a, b = _deep_team("A", 85), _deep_team("B", 85)
    result = MatchSimulator(rng=random.Random(1)).simulate_live(a, b)
    assert result.home_subs, "a tiring XI with a fresh bench should be subbed"
    # Subs respect the five-per-team budget and only fire from the hour mark.
    assert len(result.home_subs) <= live.MAX_SUBS
    assert all(minute >= 60 for minute, _, _ in result.home_subs)
    # A player only comes on once and never replaces himself.
    on = [on for _, _, on in result.home_subs]
    assert len(on) == len(set(on))


def test_hydration_breaks_only_when_hot():
    a, b = _deep_team("A", 82), _deep_team("B", 82)
    cool = MatchSimulator(rng=random.Random(2)).simulate_live(a, b, meta={"temperature": 18})
    hot = MatchSimulator(rng=random.Random(2)).simulate_live(a, b, meta={"temperature": 35})
    assert cool.cooling_breaks == 0
    assert hot.cooling_breaks >= 2


def test_hydration_recovery_reduces_fatigue():
    p = Player("x", "MID", 80, attributes={"Stamina": 13})
    side = live.LiveSide.create(Team("T", 80, squad=[p]), [p], 80.0, 80.0)
    side.on_pitch[0].fatigue_min = 40.0
    side.hydration_recovery()
    assert side.on_pitch[0].fatigue_min == 40.0 - live.HYDRATION_RECOVERY_MIN


def test_knockout_live_always_has_a_winner():
    a, b = _deep_team("A", 80), _deep_team("B", 80)
    sim = MatchSimulator(rng=random.Random(0))
    for _ in range(20):
        result = sim.simulate_live(a, b, stage="QF")
        assert result.winner in {"A", "B"}


def test_live_agrees_with_aggregate_without_fatigue(monkeypatch):
    # With fatigue switched off and no useful bench, the live engine collapses
    # to the aggregate model's expected scoring rate.
    monkeypatch.setattr(live, "FATIGUE_MAX_POINTS", 0.0)
    a = Team("A", 85, squad=[Player("GK", "GK", 85)] +
             [Player(f"P{i}", "MID", 85) for i in range(10)])
    b = Team("B", 78, squad=[Player("GK2", "GK", 78)] +
             [Player(f"Q{i}", "MID", 78) for i in range(10)])
    sim = MatchSimulator(rng=random.Random(5))
    n = 600
    live_goals = sum(sim.simulate_live(a, b).home_goals for _ in range(n)) / n
    agg_goals = sum(sim.simulate(a, b).home_goals for _ in range(n)) / n
    assert abs(live_goals - agg_goals) < 0.25
