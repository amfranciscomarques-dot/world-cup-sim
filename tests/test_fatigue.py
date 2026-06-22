import random
from worldcup.tournament.simulator import TournamentSimulator
from worldcup.data_loader import load_world_cup

def test_schedule_aware_fatigue():
    teams, tournament = load_world_cup()
    sim = TournamentSimulator(teams, tournament, rng=random.Random(42))

    # Test rest days computation
    sim.team_last_match["Argentina"] = "2026-06-17"
    rest = sim._get_rest_days("Argentina", "2026-06-21")
    assert rest == 4.0

    rest_short = sim._get_rest_days("Argentina", "2026-06-20")
    assert rest_short == 3.0

    # Test simulation reproducibility (smoke)
    outcome = sim.run_once()
    assert outcome.champion is not None
