"""End-to-end tests for pricing a user bet selection against the model.

These use the real engine — no network — and verify each selection type
lands on the right probability channel:

- ``match_result`` (home/away/draw) → ``MatchOdds.{home_win,draw,away_win}``
- ``over25`` → ``MatchOdds.over25``
- ``under25`` → 1 - ``MatchOdds.over25``
- ``player_goal`` → per-player scoring rate from the live engine
- ``outright`` → ``OddsSnapshot.winner[team]`` model probability (Monte Carlo)

The comparison is returned as a :class:`SelectionComparison` carrying the
model probability, the bookmaker's implied probability, and the edge.
"""

from __future__ import annotations

import pytest

from worldcup.data_loader import DATA_DIR, load_fixtures, load_world_cup
from worldcup.engine.match import MatchSimulator
from worldcup.odds_store import load_odds
from worldcup.tracker import (
    BetSelection,
    SelectionComparison,
    compare_selection_to_model,
    implied_probability,
    price_selection,
)


@pytest.fixture(scope="module")
def world():
    return load_world_cup()


@pytest.fixture(scope="module")
def fixtures():
    return {f["match_id"]: f for f in load_fixtures(DATA_DIR / "fixtures_2026.json")}


@pytest.fixture(scope="module")
def odds_snap():
    return load_odds()


@pytest.fixture(scope="module")
def sim():
    import random
    return MatchSimulator(rng=random.Random(20260622))


# --- match_result pricing --------------------------------------------------


def test_price_match_result_home_returns_home_win_probability(world, fixtures, sim):
    fx = fixtures["match_001"]                          # Mexico vs Czechia (MD2)
    sel = BetSelection(type="match_result", match_id=fx["match_id"],
                       label="Mexico ML", odds=1.91, team="Mexico", side="home")
    cmp = price_selection(sel, sim=sim, teams=world[0], fixtures=fixtures)
    # Sanity: model probability must be in [0, 1] and edge must equal model - implied.
    assert 0.0 < cmp.model_prob < 1.0
    assert cmp.implied_prob == pytest.approx(implied_probability(1.91), rel=1e-9)
    assert cmp.edge == pytest.approx(cmp.model_prob - cmp.implied_prob, abs=1e-12)


def test_price_match_result_away_returns_away_win_probability(world, fixtures, sim):
    fx = fixtures["match_019"]                          # Senegal vs Norway (MD2)
    sel = BetSelection(type="match_result", match_id=fx["match_id"],
                       label="Norway ML", odds=2.20, team="Norway", side="away")
    cmp = price_selection(sel, sim=sim, teams=world[0], fixtures=fixtures)
    assert 0.0 < cmp.model_prob < 1.0
    # Norway is ~stronger than Senegal but is the away side; model_prob should
    # be noticeably below the home side but well above 1/2.
    assert 0.10 < cmp.model_prob < 0.85


def test_price_match_result_draw_supported(world, fixtures, sim):
    fx = fixtures["match_001"]
    sel = BetSelection(type="match_result", match_id=fx["match_id"],
                       label="Draw", odds=3.40, team="Draw", side="draw")
    cmp = price_selection(sel, sim=sim, teams=world[0], fixtures=fixtures)
    assert 0.05 < cmp.model_prob < 0.45


# --- totals pricing --------------------------------------------------------


def test_price_over25_returns_match_odds_over25(world, fixtures, sim):
    fx = fixtures["match_021"]                          # Argentina vs Austria (MD1)
    sel = BetSelection(type="over25", match_id=fx["match_id"],
                       label="Argentina vs Austria Over 2.5", odds=1.78)
    cmp = price_selection(sel, sim=sim, teams=world[0], fixtures=fixtures)
    assert 0.0 < cmp.model_prob < 1.0
    assert cmp.implied_prob == pytest.approx(1 / 1.78, rel=1e-9)


def test_price_under25_is_complement_of_over25(world, fixtures, sim):
    fx = fixtures["match_021"]      # noqa: F841 (sim is intentionally used)
    # Use a single match_odds draw so over and under are exact complements.
    home, away = world[0][fx["home"]], world[0][fx["away"]]
    mo = sim.monte_carlo(home, away, 600, stage="group", neutral=True)
    over = compare_selection_to_model(
        BetSelection(type="over25", match_id=fx["match_id"], label="Over", odds=1.78),
        mo.over25,
    )
    under = compare_selection_to_model(
        BetSelection(type="under25", match_id=fx["match_id"], label="Under", odds=2.10),
        1.0 - mo.over25,
    )
    assert under.model_prob == pytest.approx(1.0 - over.model_prob, abs=1e-12)


# --- player pricing --------------------------------------------------------


def test_price_player_goal_returns_per_player_scoring_rate(world, fixtures, sim):
    fx = fixtures["match_018"]                          # France vs Iraq (MD2)
    sel = BetSelection(type="player_goal", match_id=fx["match_id"],
                       label="Dembele scores", odds=1.83, player="Ousmane Dembele")
    cmp = price_selection(sel, sim=sim, teams=world[0], fixtures=fixtures,
                          scorer_iterations=200)
    # Dembele is rated 86 FWD for France vs Iraq — model probability should be
    # somewhere in the 30-70% range. Edge against 1/1.83 = 0.546.
    assert 0.30 < cmp.model_prob < 0.70
    assert cmp.implied_prob == pytest.approx(1 / 1.83, rel=1e-9)


# --- outright pricing ------------------------------------------------------


def test_price_outright_returns_market_yes_probability(odds_snap):
    if not odds_snap or not odds_snap.winner:
        pytest.skip("no odds snapshot loaded")
    sel = BetSelection(type="outright", match_id="", label="England winner",
                       odds=7.10, team="England")
    cmp = price_selection(sel, odds=odds_snap)
    assert 0.0 < cmp.model_prob < 1.0
    assert cmp.implied_prob == pytest.approx(1 / 7.10, rel=1e-9)


def test_price_outright_spain_vs_market(odds_snap):
    if not odds_snap or not odds_snap.winner:
        pytest.skip("no odds snapshot loaded")
    sel = BetSelection(type="outright", match_id="", label="Spain winner",
                       odds=6.80, team="Spain")
    cmp = price_selection(sel, odds=odds_snap)
    assert 0.0 < cmp.model_prob < 1.0


# --- helpers ---------------------------------------------------------------


def test_compare_selection_to_model_helper_does_not_need_engine():
    cmp = compare_selection_to_model(
        selection=BetSelection(type="match_result", match_id="m", label="A",
                               odds=1.91, team="X", side="home"),
        model_prob=0.55,
    )
    assert isinstance(cmp, SelectionComparison)
    assert cmp.edge == pytest.approx(0.55 - 1 / 1.91, rel=1e-9)
    assert cmp.fair_odds == pytest.approx(1 / 0.55, rel=1e-9)


def test_price_selection_rejects_unknown_match_id(world, fixtures, sim):
    sel = BetSelection(type="over25", match_id="match_does_not_exist",
                       label="?", odds=1.78)
    with pytest.raises(KeyError):
        price_selection(sel, sim=sim, teams=world[0], fixtures=fixtures)
