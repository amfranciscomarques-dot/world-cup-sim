import random

from worldcup.data_loader import load_world_cup
from worldcup.engine import MatchSimulator
from worldcup.models import PlayedResult, Team
from worldcup.tournament import TournamentSimulator, play_group
from worldcup.tournament.group_stage import GroupStanding, rank_standings
from worldcup.tournament.knockout import (
    R32_LEAF_ORDER,
    R32_MATCHES,
    bracket_seed_order,
    build_official_bracket_2026,
    play_knockout,
    third_place_allocation,
)


def test_data_loads_48_teams_in_12_groups():
    teams, tournament = load_world_cup()
    assert len(teams) == 48
    assert len(tournament.groups) == 12
    assert all(len(v) == 4 for v in tournament.groups.values())
    # Every team is linked to its group.
    assert all(t.group is not None for t in teams.values())


def test_play_group_plays_round_robin():
    teams = [Team(n, 80) for n in ("A", "B", "C", "D")]
    result = play_group("X", teams, MatchSimulator(rng=random.Random(0)), random.Random(0))
    assert len(result.results) == 6  # 4 choose 2
    assert len(result.standings) == 4
    assert all(s.played == 3 for s in result.standings)


def test_play_group_uses_known_results_instead_of_simulating():
    teams = [Team(n, 80) for n in ("A", "B", "C", "D")]
    # Pin A-B to a real 3-0; the other five games are still simulated.
    known = {
        frozenset(("A", "B")): PlayedResult("A", "B", 3, 0, stage="group"),
    }
    result = play_group(
        "X", teams, MatchSimulator(rng=random.Random(0)), random.Random(0), known=known
    )
    ab = next(r for r in result.results if {r.home, r.away} == {"A", "B"})
    assert (ab.home, ab.home_goals, ab.away_goals) == ("A", 3, 0)
    a = next(s for s in result.standings if s.team == "A")
    b = next(s for s in result.standings if s.team == "B")
    # The known game contributes a win for A and a loss for B, deterministically.
    assert a.won >= 1 and a.gf >= 3
    assert b.lost >= 1


def test_tournament_simulator_threads_results_into_groups():
    teams, tournament = load_world_cup()
    # A lopsided fictional result between two real group-mates (Group A).
    results = [PlayedResult("Mexico", "South Korea", 5, 0, stage="group")]
    sim = TournamentSimulator(teams, tournament, rng=random.Random(1), results=results)
    outcome = sim.run_once()
    group_a = next(g for g in outcome.groups if g.letter == "A")
    played = [r for r in group_a.results if {r.home, r.away} == {"Mexico", "South Korea"}]
    assert len(played) == 1
    assert (played[0].home_goals, played[0].away_goals) == (5, 0)


def test_standings_sorted_by_points_then_gd():
    standings = [
        GroupStanding("A", pts=6, gf=5, ga=2),
        GroupStanding("B", pts=9, gf=7, ga=1),
        GroupStanding("C", pts=6, gf=8, ga=2),  # same pts as A, better GD
    ]
    ranked = rank_standings(standings, [], random.Random(0))
    assert [s.team for s in ranked] == ["B", "C", "A"]


def test_bracket_seed_order_balanced():
    order = bracket_seed_order(8)
    assert sorted(order) == list(range(1, 9))
    # Seed 1 and seed 2 are in opposite halves.
    assert order.index(1) < 4 and order.index(2) >= 4


def test_knockout_requires_power_of_two():
    teams = [Team(str(i), 80) for i in range(6)]
    try:
        play_knockout(teams, MatchSimulator(rng=random.Random(0)), random.Random(0))
        assert False, "should reject non power-of-two field"
    except ValueError:
        pass


def test_knockout_produces_champion():
    teams = [Team(f"T{i}", 70 + i) for i in range(32)]
    result = play_knockout(teams, MatchSimulator(rng=random.Random(2)), random.Random(2))
    assert result.champion is not None
    assert len(result.rounds) == 5  # R32, R16, QF, SF, F
    assert len(result.rounds[0]) == 16


def _r16_team_names(result):
    return {m.home for m in result.rounds[1]} | {m.away for m in result.rounds[1]}


def test_knockout_uses_known_decisive_result_instead_of_simulating():
    teams = [Team(f"T{i}", 70 + i) for i in range(32)]
    # Pin the first R32 tie (T0 vs T1) to a real 0-2 win for the away side, T1.
    known = {frozenset(("T0", "T1")): PlayedResult("T0", "T1", 0, 2, stage="knockout")}
    result = play_knockout(
        teams, MatchSimulator(rng=random.Random(3)), random.Random(3),
        prearranged=True, known=known,
    )
    pinned = next(m for m in result.rounds[0] if {m.home, m.away} == {"T0", "T1"})
    assert (pinned.home, pinned.home_goals, pinned.away_goals) == ("T0", 0, 2)
    assert pinned.winner == "T1"
    r16 = _r16_team_names(result)
    assert "T1" in r16 and "T0" not in r16  # the real winner advanced, not the loser


def test_knockout_known_penalty_shootout_decides_who_advances():
    teams = [Team(f"T{i}", 70 + i) for i in range(32)]
    # A level tie that T0 won on penalties (4-2) — winner unrecoverable from the
    # score, so the recorded shootout is what advances T0.
    known = {frozenset(("T0", "T1")): PlayedResult(
        "T0", "T1", 1, 1, stage="knockout", home_pens=4, away_pens=2)}
    result = play_knockout(
        teams, MatchSimulator(rng=random.Random(4)), random.Random(4),
        prearranged=True, known=known,
    )
    pinned = next(m for m in result.rounds[0] if {m.home, m.away} == {"T0", "T1"})
    assert pinned.penalties and (pinned.home_pens, pinned.away_pens) == (4, 2)
    assert pinned.winner == "T0"
    r16 = _r16_team_names(result)
    assert "T0" in r16 and "T1" not in r16


def test_knockout_level_score_without_pens_falls_back_to_simulation():
    teams = [Team(f"T{i}", 70 + i) for i in range(32)]
    # Level score, no shootout recorded: the winner is unknowable, so the tie is
    # simulated rather than silently advancing one side.
    known = {frozenset(("T0", "T1")): PlayedResult("T0", "T1", 1, 1, stage="knockout")}
    result = play_knockout(
        teams, MatchSimulator(rng=random.Random(5)), random.Random(5),
        prearranged=True, known=known,
    )
    pinned = next(m for m in result.rounds[0] if {m.home, m.away} == {"T0", "T1"})
    assert pinned.winner in ("T0", "T1")            # a real winner is produced
    r16 = _r16_team_names(result)
    assert ("T0" in r16) != ("T1" in r16)           # exactly one side advances


def test_tournament_simulator_splits_known_results_by_stage():
    teams, tournament = load_world_cup()
    results = [
        PlayedResult("Mexico", "South Korea", 5, 0, stage="group"),
        PlayedResult("Brazil", "Argentina", 2, 1, stage="knockout"),
    ]
    sim = TournamentSimulator(teams, tournament, rng=random.Random(1), results=results)
    assert frozenset(("Mexico", "South Korea")) in sim.known_group
    assert frozenset(("Brazil", "Argentina")) in sim.known_knockout
    assert frozenset(("Brazil", "Argentina")) not in sim.known_group


# Candidate third-place groups allowed in each winner-group slot, per the
# official format (Wikipedia "2026 FIFA World Cup knockout stage").
_THIRD_CANDIDATES = {
    "A": set("CEFHI"), "B": set("EFGIJ"), "D": set("BEFIJ"), "E": set("ABCDF"),
    "G": set("AEHIJ"), "I": set("CDFGH"), "K": set("DEIJL"), "L": set("EHIJK"),
}


def test_third_place_allocation_table_is_valid():
    # Every 8-group combination resolves to a permutation that respects each
    # slot's candidate list.
    import itertools

    for combo in itertools.combinations("ABCDEFGHIJKL", 8):
        alloc = third_place_allocation(set(combo))
        assert set(alloc.values()) == set(combo)            # bijection onto the 8
        for slot, third_group in alloc.items():
            assert third_group in _THIRD_CANDIDATES[slot]


def test_official_bracket_reproduces_fifa_r32_matchups():
    groups = "ABCDEFGHIJKL"
    winners = {g: Team(f"1{g}", 80) for g in groups}
    runners = {g: Team(f"2{g}", 80) for g in groups}
    advancing = set("CEFHIJKL")
    thirds = {g: Team(f"3{g}", 80) for g in advancing}
    alloc = third_place_allocation(advancing)

    bracket = build_official_bracket_2026(winners, runners, thirds)
    names = [t.name for t in bracket]
    assert len(set(names)) == 32

    def label(ref):
        kind, grp = ref
        if kind == "W":
            return f"1{grp}"
        if kind == "R":
            return f"2{grp}"
        return f"3{alloc[grp]}"  # third-place slot resolved via the table

    for i, match_no in enumerate(R32_LEAF_ORDER):
        home_ref, away_ref = R32_MATCHES[match_no]
        assert names[2 * i] == label(home_ref)
        assert names[2 * i + 1] == label(away_ref)


def test_run_once_yields_32_qualifiers_and_champion():
    teams, tournament = load_world_cup()
    sim = TournamentSimulator(teams, tournament, rng=random.Random(5))
    outcome = sim.run_once()
    assert len(outcome.qualifiers) == 32
    assert len(set(outcome.qualifiers)) == 32
    assert outcome.champion in outcome.qualifiers


def test_monte_carlo_probabilities_are_consistent():
    teams, tournament = load_world_cup()
    sim = TournamentSimulator(teams, tournament, rng=random.Random(9))
    report = sim.monte_carlo(30)
    total_titles = sum(r["Champion"] for r in report.reach.values())
    assert abs(total_titles - 1.0) < 1e-6  # exactly one champion per run
    for probs in report.reach.values():
        # Reaching a deeper stage implies reaching shallower ones.
        assert probs["R32"] >= probs["R16"] >= probs["QF"] >= probs["SF"] >= probs["Final"] >= probs["Champion"]
        assert all(0.0 <= p <= 1.0 for p in probs.values())


def test_lineup_override_changes_outcome_distribution():
    """Fielding a gutted lineup for the favourite should lower its title rate."""
    teams, tournament = load_world_cup()
    arg = teams["Argentina"]
    from worldcup.models import Lineup
    # Bench everyone except a single weak outfield player.
    worst = min(arg.squad, key=lambda p: p.rating)
    gutted = {"Argentina": Lineup("Argentina", [worst])}

    # 120 runs (not 60): the stochastic intangibles factor adds per-match
    # variance, so a larger sample is needed for the deterministic lineup
    # penalty to dominate the noise reliably.
    base = TournamentSimulator(teams, tournament, rng=random.Random(3)).monte_carlo(120)
    weak = TournamentSimulator(teams, tournament, rng=random.Random(3)).monte_carlo(120, lineups=gutted)
    assert weak.reach["Argentina"]["Champion"] <= base.reach["Argentina"]["Champion"]
