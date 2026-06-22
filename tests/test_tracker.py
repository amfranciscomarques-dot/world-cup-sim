"""Offline tests for the bet-tracker layer.

The tracker records the user's real bets (decimal-odds multiples, duplas,
simples, outrights) and replays them through the same Monte Carlo engine that
prices them — so the realised P&L tracks the analytic edge. The per-leg
"model vs market" comparison is the part that stands on its own: it shows the
model's probability for each selection alongside the bookmaker's implied
probability, so the user can see where they had edge.

Nothing here touches the network.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path

import pytest

from worldcup.tracker import (
    Bet,
    BetSelection,
    TrackerPerformance,
    compare_selection_to_model,
    implied_probability,
    load_bets,
    save_bets,
    simulate_tracker_performance,
)


# --- Bet / BetSelection dataclasses -----------------------------------------


def test_bet_selection_carries_decimal_odds_and_implied_prob():
    # Decimal odds 1.78 -> implied probability (vig-included) ≈ 0.5618.
    sel = BetSelection(type="over25", match_id="m_J1", label="Argentina vs Austria Over 2.5",
                       odds=1.78)
    assert sel.implied_probability == pytest.approx(1 / 1.78, rel=1e-9)


def test_bet_total_odds_multiplied_across_selections():
    # Dupla: 1.50 * 2.20 = 3.30 (per the user's June 2026 slip).
    bet = Bet(
        id="b1", date_placed="2026-06-22", stake=1.00, total_odds=3.30,
        selections=[
            BetSelection(type="match_result", match_id="m_J1", label="Argentina ML", odds=1.50,
                         team="Argentina"),
            BetSelection(type="match_result", match_id="m_I3", label="Norway ML", odds=2.20,
                         team="Norway"),
        ],
    )
    assert bet.total_odds == pytest.approx(1.50 * 2.20, rel=1e-9)


def test_bet_is_won_only_when_every_selection_hits():
    bet = Bet(id="b", date_placed="2026-06-22", stake=1.0, total_odds=3.30,
              selections=[
                  BetSelection(type="match_result", match_id="m1", label="A", odds=1.5, team="X"),
                  BetSelection(type="match_result", match_id="m2", label="B", odds=2.2, team="Y"),
              ])
    assert bet.settles({"m1": True, "m2": True}) is True
    assert bet.settles({"m1": True, "m2": False}) is False
    assert bet.settles({"m1": False, "m2": True}) is False
    assert bet.settles({}) is False


# --- Persistence ------------------------------------------------------------


def test_save_and_load_roundtrip(tmp_path: Path):
    bets = [
        Bet(
            id="abc", date_placed="2026-06-22", stake=0.50, total_odds=2.90,
            selections=[
                BetSelection(type="over25", match_id="m_J1",
                             label="Argentina vs Austria Over 2.5", odds=1.78),
            ],
        ),
    ]
    out = tmp_path / "bets.json"
    save_bets(bets, path=out)
    loaded = load_bets(path=out)
    assert len(loaded) == 1
    assert loaded[0].id == "abc"
    assert loaded[0].stake == 0.50
    assert loaded[0].selections[0].type == "over25"
    assert loaded[0].selections[0].match_id == "m_J1"
    assert loaded[0].selections[0].odds == 1.78


def test_load_bets_returns_empty_when_file_missing(tmp_path: Path):
    assert load_bets(path=tmp_path / "nope.json") == []


def test_load_bets_rejects_unknown_selection_type(tmp_path: Path):
    bad = {"bets": [{"id": "x", "date_placed": "2026-06-22", "stake": 1.0,
                     "total_odds": 1.0, "selections": [
                         {"type": "totally-unknown", "match_id": "m", "label": "?", "odds": 2.0}]}]}
    p = tmp_path / "bets.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown selection type"):
        load_bets(path=p)


# --- Implied probability ----------------------------------------------------


def test_implied_probability_basic():
    assert implied_probability(2.0) == pytest.approx(0.5, rel=1e-9)
    assert implied_probability(1.5) == pytest.approx(2 / 3, rel=1e-9)
    assert implied_probability(7.10) == pytest.approx(1 / 7.10, rel=1e-9)


def test_implied_probability_rejects_non_positive():
    with pytest.raises(ValueError):
        implied_probability(0.0)
    with pytest.raises(ValueError):
        implied_probability(-1.0)


# --- Simulate tracker performance ------------------------------------------


def test_simulate_tracker_performance_returns_per_bet_pnl_and_aggregate():
    # Two single-leg bets: one always wins, one always loses. The aggregate
    # performance should reflect deterministic outcomes.
    bets = [
        Bet(id="win", date_placed="2026-06-22", stake=1.0, total_odds=2.0,
            selections=[BetSelection(type="match_result", match_id="m1", label="A", odds=2.0,
                                     team="X")]),
        Bet(id="lose", date_placed="2026-06-22", stake=1.0, total_odds=2.0,
            selections=[BetSelection(type="match_result", match_id="m2", label="B", odds=2.0,
                                     team="Y")]),
    ]
    perf = simulate_tracker_performance(
        bets,
        settle_selections=lambda bet, run_idx: {"m1": True, "m2": False},
        n=50,
    )
    # Each iteration: win pays 1 * (2 - 1) = 1, lose pays -1. 50 iters -> mean = 0.
    assert math.isclose(perf.mean_pnl, 0.0, abs_tol=1e-9)
    assert perf.total_staked == 2.0
    assert perf.win_rate == pytest.approx(0.0)  # no iteration had all bets winning


def test_simulate_tracker_performance_models_multi_leg_correctly():
    # Dupla: both must win for the bet to cash.
    bet = Bet(id="d", date_placed="2026-06-22", stake=1.0, total_odds=3.30,
              selections=[
                  BetSelection(type="match_result", match_id="m1", label="A", odds=1.5,
                               team="X"),
                  BetSelection(type="match_result", match_id="m2", label="B", odds=2.2,
                               team="Y"),
              ])

    def settle(b, run_idx):
        # First leg always hits; second hits on even runs.
        return {"m1": True, "m2": run_idx % 2 == 0}

    perf = simulate_tracker_performance([bet], settle, n=100)
    # 50 wins @ payout 1 * (3.30 - 1) = 2.30; 50 losses @ -1. Mean = (50*2.3 - 50)/100 = 0.65
    assert perf.mean_pnl == pytest.approx(0.65, abs=1e-9)
    assert perf.win_rate == pytest.approx(0.5, abs=1e-9)


# --- Comparison helpers -----------------------------------------------------


def test_tracker_performance_percentile_clamps():
    pnl = [float(i) for i in range(101)]      # 0..100
    perf = TrackerPerformance(iterations=101, bets=[], total_staked=0.0, pnl=pnl)
    assert perf.percentile(0.0) == 0.0
    assert perf.percentile(0.5) == 50.0
    assert perf.percentile(1.0) == 100.0


def test_compare_selection_model_vs_market_computes_edge():
    # Model puts 0.55 on the home ML; bookmaker price 1.91 -> implied 0.524.
    # Edge = 0.55 - 0.524 = +0.026 (slight value).
    cmp = compare_selection_to_model(
        selection=BetSelection(type="match_result", match_id="m1", label="Mexico ML",
                               odds=1.91, team="Mexico"),
        model_prob=0.55,
    )
    assert cmp.model_prob == pytest.approx(0.55, abs=1e-9)
    assert cmp.implied_prob == pytest.approx(1 / 1.91, rel=1e-9)
    assert cmp.edge == pytest.approx(0.55 - 1 / 1.91, rel=1e-9)
    # Decimal fair price from model: 1 / 0.55
    assert cmp.fair_odds == pytest.approx(1 / 0.55, rel=1e-9)


def test_serialize_bet_roundtrips_through_json():
    bet = Bet(
        id="x", date_placed="2026-06-22", stake=2.0, total_odds=3.82,
        selections=[
            BetSelection(type="match_result", match_id="m", label="M", odds=1.91,
                         team="Mexico"),
        ],
    )
    d = asdict(bet)
    # Round-trip via dataclasses (the real persistence goes through JSON, but
    # asdict exercises the field set; full JSON round-trip is in test_save_*).
    bet2 = Bet(**{**d, "selections": [BetSelection(**s) for s in d["selections"]]})
    assert bet2 == bet
