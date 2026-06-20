"""Tests for the SofaScore form parameter: the factor, the snapshot store, and
its effect on the simulation. All offline (the network fetch is monkeypatched)."""

import json
import random

import pytest

from worldcup.engine import MatchSimulator
from worldcup.factors import default_registry
from worldcup.factors.builtin import SOFA_BASELINE, SofaScoreFactor
from worldcup.factors.base import MatchContext, TeamMatchState
from worldcup.models import Team


def _ctx(home_team, away_team, home=None, away=None):
    return MatchContext(
        home=TeamMatchState.from_team(home_team, is_home=True, **(home or {})),
        away=TeamMatchState.from_team(away_team, is_home=False, **(away or {})),
        neutral=True,
        rng=random.Random(0),
    )


# --- the factor -------------------------------------------------------------

def test_sofascore_in_default_registry():
    assert "sofascore" in default_registry().names()


def test_sofascore_above_baseline_boosts_both_ends():
    a = Team("A", 80)
    ctx = _ctx(a, Team("B", 80), home={"sofa_rating": SOFA_BASELINE + 1.0})
    SofaScoreFactor(scale=5.0, cap=4.0).adjust(ctx)
    assert ctx.home.attack == 84  # +1.0 rating * scale 5 = +5, capped to +4
    assert ctx.home.defense == 84
    assert ctx.away.attack == 80  # no rating set

    # A small gap (below the cap) maps linearly: +0.4 * 5 = +2.0.
    ctx2 = _ctx(a, Team("B", 80), home={"sofa_rating": SOFA_BASELINE + 0.4})
    SofaScoreFactor(scale=5.0, cap=4.0).adjust(ctx2)
    assert ctx2.home.attack == pytest.approx(82.0)


def test_sofascore_below_baseline_penalises():
    a = Team("A", 80)
    ctx = _ctx(a, Team("B", 80), home={"sofa_rating": SOFA_BASELINE - 0.4})
    SofaScoreFactor(scale=5.0, cap=4.0).adjust(ctx)
    assert ctx.home.attack == pytest.approx(78.0)  # -0.4 * 5 = -2.0
    assert ctx.home.defense == pytest.approx(78.0)


def test_sofascore_at_baseline_is_noop():
    a = Team("A", 80)
    ctx = _ctx(a, Team("B", 80), home={"sofa_rating": SOFA_BASELINE})
    SofaScoreFactor().adjust(ctx)
    assert ctx.home.attack == 80 and ctx.home.defense == 80


def test_sofascore_absent_is_noop():
    a = Team("A", 80)
    ctx = _ctx(a, Team("B", 80))  # no sofa_rating in extras
    SofaScoreFactor().adjust(ctx)
    assert ctx.home.attack == 80 and ctx.away.attack == 80


# --- the store --------------------------------------------------------------

def _write_snapshot(path):
    payload = {
        "_fetched": "2026-06-20",
        "games": [
            {"date": "2026-06-11", "home": "A", "away": "B",
             "home_rating": 7.0, "away_rating": 6.0, "score": "2-0"},
            # A plays again: its average should fold both games together.
            {"date": "2026-06-15", "home": "C", "away": "A",
             "home_rating": 6.5, "away_rating": 8.0, "score": "1-1",
             "home_players": [{"name": "p", "rating": 6.5}]},
            # A game with a missing rating contributes only the rated side.
            {"date": "2026-06-16", "home": "B", "away": "C",
             "home_rating": None, "away_rating": 7.2, "score": "0-0"},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_team_ratings_average_and_skip_missing(tmp_path):
    from worldcup.sofascore_store import load_sofascore, team_ratings

    p = tmp_path / "sofa.json"
    _write_snapshot(p)
    snap = load_sofascore(p)
    tr = team_ratings(snap)
    assert tr["A"] == pytest.approx((7.0 + 8.0) / 2)   # two games
    assert tr["B"] == pytest.approx(6.0)               # null rating skipped
    assert tr["C"] == pytest.approx((6.5 + 7.2) / 2)


def test_form_extras_shape_and_empty(tmp_path):
    from worldcup.sofascore_store import form_extras, load_form_extras

    p = tmp_path / "sofa.json"
    _write_snapshot(p)
    extras = load_form_extras(p)
    assert extras["A"] == {"sofa_rating": pytest.approx(7.5)}
    # No snapshot -> empty extras (factor no-ops everywhere).
    assert form_extras(None) == {}
    assert load_form_extras(tmp_path / "missing.json") == {}


def test_load_sofascore_missing_file_is_none(tmp_path):
    from worldcup.sofascore_store import load_sofascore

    assert load_sofascore(tmp_path / "nope.json") is None


# --- effect on the simulation ----------------------------------------------

def test_form_makes_a_team_win_more():
    a, b = Team("A", 80), Team("B", 80)
    base = MatchSimulator(rng=random.Random(1)).monte_carlo(a, b, 800, neutral=True)
    boosted = MatchSimulator(rng=random.Random(1)).monte_carlo(
        a, b, 800, neutral=True, home_extras={"sofa_rating": SOFA_BASELINE + 1.0})
    # An overperforming side (capped +4 to both ends) should win materially more.
    assert boosted.home_win > base.home_win + 0.05


# --- refresh (network monkeypatched) ---------------------------------------

def test_refresh_writes_snapshot(monkeypatch, tmp_path):
    from worldcup import sofascore
    from worldcup.sofascore_store import load_sofascore, refresh_sofascore

    fake = [
        sofascore.MatchRatings(date="2026-06-11", home="A", away="B",
                               home_rating=7.1, away_rating=6.3, score="2-0",
                               home_players=[sofascore.PlayerRating("p", 7.1)]),
        sofascore.MatchRatings(date="2026-06-12", home="C", away=None,
                               home_rating=6.8, away_rating=None),  # unmapped -> skipped
    ]
    monkeypatch.setattr(sofascore, "fetch_finished_matches", lambda teams, **kw: fake)

    out = tmp_path / "sofa.json"
    summary = refresh_sofascore(teams={}, tournament=object(), path=out)
    assert summary["games"] == 1
    assert summary["teams_rated"] == 2
    assert len(summary["skipped"]) == 1

    snap = load_sofascore(out)
    assert snap.game("A", "B").home_rating == 7.1


def test_refresh_empty_fetch_protects_snapshot(monkeypatch, tmp_path):
    from worldcup import sofascore
    from worldcup.sofascore_store import refresh_sofascore

    monkeypatch.setattr(sofascore, "fetch_finished_matches", lambda teams, **kw: [])
    out = tmp_path / "sofa.json"
    with pytest.raises(sofascore.SofaScoreError):
        refresh_sofascore(teams={}, tournament=object(), path=out)
    assert not out.exists()  # never wrote an empty snapshot


def test_seed_snapshot_rates_all_teams():
    """The committed seed snapshot loads and produces a form signal per team."""
    from worldcup.data_loader import load_world_cup
    from worldcup.sofascore_store import load_sofascore, team_ratings

    snap = load_sofascore()
    assert snap is not None
    teams, _ = load_world_cup()
    tr = team_ratings(snap)
    # Every rated team is one of ours and sits in a sane SofaScore band.
    assert tr and all(name in teams for name in tr)
    assert all(5.0 <= v <= 10.0 for v in tr.values())
