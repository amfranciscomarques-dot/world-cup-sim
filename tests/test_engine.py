import random

from worldcup.engine import poisson
from worldcup.engine.match import MatchSimulator
from worldcup.models import Team


def test_expected_goals_increases_with_attack_edge():
    even = poisson.expected_goals(78, 78)
    strong = poisson.expected_goals(90, 70)
    weak = poisson.expected_goals(65, 85)
    assert weak < even < strong
    assert abs(even - poisson.BASE_GOALS) < 1e-9


def test_expected_goals_clamped():
    assert poisson.expected_goals(200, 0) <= poisson.MAX_XG
    assert poisson.expected_goals(0, 200) >= poisson.MIN_XG


def test_sample_poisson_nonnegative_and_seeded():
    rng = random.Random(123)
    draws = [poisson.sample_poisson(1.4, rng) for _ in range(100)]
    assert all(d >= 0 for d in draws)
    # Same seed reproduces the same sequence.
    rng2 = random.Random(123)
    draws2 = [poisson.sample_poisson(1.4, rng2) for _ in range(100)]
    assert draws == draws2


def test_match_is_reproducible_with_seed():
    a = Team("A", 85)
    b = Team("B", 75)
    r1 = MatchSimulator(rng=random.Random(7)).simulate(a, b)
    r2 = MatchSimulator(rng=random.Random(7)).simulate(a, b)
    assert (r1.home_goals, r1.away_goals) == (r2.home_goals, r2.away_goals)


def test_knockout_always_has_a_winner():
    a = Team("A", 80)
    b = Team("B", 80)
    sim = MatchSimulator(rng=random.Random(0))
    for _ in range(50):
        result = sim.simulate(a, b, stage="QF", neutral=True)
        assert result.winner in {"A", "B"}


def test_stronger_team_wins_more_often():
    strong = Team("Strong", 92)
    weak = Team("Weak", 64)
    sim = MatchSimulator(rng=random.Random(1))
    wins = sum(sim.simulate(strong, weak).winner == "Strong" for _ in range(400))
    assert wins > 280  # comfortably favoured
