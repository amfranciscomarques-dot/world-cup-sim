"""Tests for the offline dashboard stack: standings-from-results, the odds
snapshot loader, the report builder, and the HTML renderer. All offline."""

import json
import random

from worldcup.data_loader import load_world_cup
from worldcup.html import render_dashboard
from worldcup.models import PlayedResult
from worldcup.odds_store import GameOdds, OddsSnapshot, load_odds
from worldcup.report import build_report
from worldcup.tournament import standings_from_results


def test_standings_from_results_counts_only_played():
    names = ["A", "B", "C", "D"]
    results = [
        PlayedResult("A", "B", 2, 0, stage="group"),
        PlayedResult("C", "D", 1, 1, stage="group"),
        # A result with an outsider must be ignored for this group.
        PlayedResult("A", "Z", 9, 0, stage="group"),
    ]
    gr = standings_from_results("X", names, results, random.Random(0))
    table = {s.team: s for s in gr.standings}
    assert table["A"].played == 1 and table["A"].pts == 3 and table["A"].gf == 2
    assert table["B"].played == 1 and table["B"].lost == 1
    assert table["C"].played == 1 and table["C"].drawn == 1
    assert table["D"].played == 1 and table["D"].pts == 1
    # Leader is A (3 pts); the two drawn teams sit on 1.
    assert gr.standings[0].team == "A"


def test_game_odds_result_and_market_triple():
    g = GameOdds(date="2026-06-11", home="A", away="B", closed=True,
                 score="2-0", live=(0.1, 0.1, 0.1), pregame=(0.6, 0.2, 0.2))
    assert g.result == "H"
    # De-vigged from pregame (closed game), summing to 1.
    mt = g.market_triple()
    assert abs(sum(mt) - 1.0) < 1e-9
    assert mt[0] > mt[1]  # home favourite preserved

    draw = GameOdds("d", "A", "B", True, "1-1", None, (0.3, 0.4, 0.3))
    assert draw.result == "D"
    upcoming = GameOdds("u", "A", "B", False, None, (0.5, 0.25, 0.25), None)
    assert upcoming.result is None
    assert abs(sum(upcoming.market_triple()) - 1.0) < 1e-9


def test_load_odds_roundtrip(tmp_path):
    payload = {
        "_fetched": "2026-06-20",
        "winner": [{"team": "Spain", "price": 0.18}, {"team": "Brazil", "price": 0.1}],
        "reach": {"R16": [{"team": "Spain", "price": 0.95}]},
        "group_winners": {"A": [{"team": "Mexico", "price": 0.8}]},
        "games": [
            {"date": "2026-06-11", "home": "Mexico", "away": "South Africa",
             "closed": True, "score": "2-0",
             "live": [0.0, 0.0, 1.0], "pregame": [0.7, 0.2, 0.1]},
        ],
    }
    p = tmp_path / "odds.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    snap = load_odds(p)
    assert snap.fetched == "2026-06-20"
    assert snap.winner["Spain"] == 0.18
    assert snap.reach["R16"]["Spain"] == 0.95
    assert snap.game("South Africa", "Mexico").score == "2-0"   # orientation-agnostic
    assert load_odds(tmp_path / "missing.json") is None


def _toy_snapshot(teams_in_group):
    a, b, c, d = teams_in_group
    return OddsSnapshot(
        fetched="2026-06-20",
        winner={a: 0.2, b: 0.1},
        reach={}, group_winners={},
        games=[
            GameOdds("2026-06-11", a, b, True, "2-0", (0, 0, 1), (0.6, 0.25, 0.15)),
            GameOdds("2026-06-12", c, d, False, None, (0.4, 0.3, 0.3), None),
        ],
    )


def test_build_report_with_market_produces_full_payload():
    teams, tournament = load_world_cup()
    group_a = tournament.groups["A"]
    results = [PlayedResult(group_a[0], group_a[1], 2, 0, stage="group")]
    odds = _toy_snapshot(group_a)

    rep = build_report(teams, tournament, results, odds,
                       title_iters=80, game_iters=80, seed=1)

    assert rep["meta"]["has_market"] is True
    assert len(rep["title"]) > 0
    assert all(0.0 <= r["champion"] <= 1.0 for r in rep["title"])
    assert len(rep["groups"]) == 12
    # The played game was graded against its pre-kickoff market line.
    assert rep["performance"]["n"] == 1
    assert rep["results"] and rep["results"][0]["market"] is not None
    assert rep["champion_pick"] is not None
    # Champion probabilities sum to ~1 across all teams in the underlying sim,
    # so the leader's probability is a sane fraction.
    assert 0.0 < rep["champion_pick"]["champion"] <= 1.0


def test_build_report_model_only_without_odds():
    teams, tournament = load_world_cup()
    results = [PlayedResult(tournament.groups["A"][0], tournament.groups["A"][1],
                            1, 0, stage="group")]
    rep = build_report(teams, tournament, results, None,
                       title_iters=60, game_iters=60, seed=2)
    assert rep["meta"]["has_market"] is False
    assert rep["value_bets"] is None
    assert rep["performance"]["n"] == 0          # no market -> nothing to grade
    assert len(rep["results"]) == 1              # synthesised from the result
    assert len(rep["upcoming"]) > 0              # synthesised from unplayed pairings


def test_render_dashboard_is_self_contained_html():
    teams, tournament = load_world_cup()
    odds = _toy_snapshot(tournament.groups["A"])
    rep = build_report(teams, tournament, [], odds, title_iters=40, game_iters=40, seed=3)
    doc = render_dashboard(rep)
    assert doc.startswith("<!DOCTYPE html>")
    assert "{{BODY}}" not in doc and "{{TITLE}}" not in doc
    assert "Title race" in doc and "Group standings" in doc
    assert tournament.name in doc
