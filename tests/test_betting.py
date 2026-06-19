"""Offline tests for the Polymarket comparison / betting layer.

These never touch the network: market data is faked by constructing
``PolyEvent`` objects directly, so only our maths and settlement logic is under
test. (The live Gamma client is exercised manually, not in CI.)
"""

import random

from worldcup.betting import (
    Comparison,
    kelly_fraction,
    run_betting,
    select_bets,
    settles,
)
from worldcup.data_loader import load_world_cup
from worldcup.polymarket import (
    CHAMPION,
    GROUP_WINNER,
    MarketLine,
    PolyEvent,
    map_team,
    settlement_mode,
)
from worldcup.tournament import TournamentSimulator


def test_settlement_mode_from_slug():
    assert settlement_mode("world-cup-winner") == CHAMPION
    assert settlement_mode("world-cup-group-d-winner") == GROUP_WINNER
    assert settlement_mode("world-cup-nation-to-reach-quarterfinals") == "reach:QF"
    assert settlement_mode("world-cup-nation-to-reach-round-of-16") == "reach:R16"


def test_map_team_handles_aliases_and_accents():
    teams, _ = load_world_cup()
    assert map_team("Spain", teams) == "Spain"
    assert map_team("USA", teams) == "USA"
    assert map_team("United States", teams) == "USA"
    assert map_team("Congo DR", teams) == "DR Congo"
    assert map_team("Türkiye", teams) == "Turkiye"
    assert map_team("Bosnia and Herzegovina", teams) == "Bosnia-Herzegovina"
    assert map_team("Atlantis", teams) is None


def test_kelly_fraction_only_bets_with_edge():
    # Edge present: 0 < f < 1, and clean formula (p - price)/(1 - price).
    assert abs(kelly_fraction(0.20, 0.10) - (0.10 / 0.90)) < 1e-9
    # No edge or negative edge -> no stake.
    assert kelly_fraction(0.10, 0.20) == 0.0
    assert kelly_fraction(0.10, 0.10) == 0.0
    # Degenerate prices are rejected.
    assert kelly_fraction(0.5, 0.0) == 0.0
    assert kelly_fraction(0.5, 1.0) == 0.0


def test_comparison_edge_and_ev():
    c = Comparison("X", market_prob=0.10, model_prob=0.15)
    assert abs(c.edge - 0.05) < 1e-9
    assert abs(c.ev - 0.5) < 1e-9  # 0.15 / 0.10 - 1


def test_select_bets_filters_and_caps_to_bankroll():
    comps = [
        Comparison("A", 0.10, 0.20),   # big edge -> bet
        Comparison("B", 0.50, 0.49),   # negative edge -> skip
        Comparison("C", 0.30, 0.30),   # no edge -> skip
    ]
    bets = select_bets(comps, bankroll=100.0, kelly_mult=0.5, min_edge=0.02)
    assert [b.team for b in bets] == ["A"]
    assert 0 < bets[0].stake <= 100.0
    assert abs(bets[0].shares - bets[0].stake / bets[0].price) < 1e-9

    # When combined Kelly would exceed the bankroll, total stake is capped.
    greedy = [Comparison(n, 0.05, 0.95) for n in ("A", "B", "C")]
    capped = select_bets(greedy, bankroll=100.0, kelly_mult=1.0, min_edge=0.02)
    assert sum(b.stake for b in capped) <= 100.0 + 1e-6


def test_settles_for_each_mode():
    teams, tournament = load_world_cup()
    sim = TournamentSimulator(teams, tournament, rng=random.Random(7))
    outcome = sim.run_once()
    champ = outcome.champion

    assert settles(CHAMPION, champ, outcome) is True
    # The champion necessarily reached every earlier stage.
    assert settles("reach:QF", champ, outcome) is True
    assert settles("reach:R16", champ, outcome) is True
    # A team that did not qualify never settles a reach market.
    non_qualifier = next(n for n in teams if n not in outcome.qualifiers)
    assert settles("reach:R16", non_qualifier, outcome) is False
    # Exactly 12 group winners settle the group-winner market.
    group_winners = [n for n in teams if settles(GROUP_WINNER, n, outcome)]
    assert len(group_winners) == 12


def _fake_event(teams):
    """A champion market where we deliberately misprice two favourites low."""
    lines = [
        MarketLine("Brazil", "Brazil", yes_price=0.02),
        MarketLine("Argentina", "Argentina", yes_price=0.02),
        MarketLine("Made Up FC", None, yes_price=0.5),  # unmatched
    ]
    return PolyEvent(slug="world-cup-winner", title="Fake Winner", mode=CHAMPION, lines=lines)


def test_run_betting_produces_report_offline():
    teams, tournament = load_world_cup()
    event = _fake_event(teams)
    sim = TournamentSimulator(teams, tournament, rng=random.Random(11))
    report = run_betting(sim, event, iterations=80, bankroll=100.0, kelly_mult=0.5)

    # Two teams compared, one entry skipped as unmatched.
    assert {c.team for c in report.comparisons} == {"Brazil", "Argentina"}
    assert report.unmatched == ["Made Up FC"]
    # Model probabilities are valid and there is one P&L sample per iteration.
    assert all(0.0 <= c.model_prob <= 1.0 for c in report.comparisons)
    assert len(report.pnl) == 80
    # Drastically underpriced favourites must surface as positive-edge bets.
    assert report.bets, "expected value bets against mispriced favourites"
    assert report.total_staked <= 100.0 + 1e-6
    assert 0.0 <= report.win_rate <= 1.0
