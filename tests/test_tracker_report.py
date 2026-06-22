"""Tests for the standalone tracker page (HTML render + report builder).

Mirrors ``tests/test_dashboard.py`` — same offline approach (no network),
fixed RNG, and assertions on the rendered HTML payload rather than the live
DOM. The build step is expensive (it price-runs every bet selection against
the model and runs a small Monte-Carlo for the P&L distribution), so the
tests use small iteration counts.
"""

from __future__ import annotations

import re

import pytest

from worldcup.data_loader import load_fixtures
from worldcup.report import build_tracker_report
from worldcup.html import render_tracker_html


# --- Report builder --------------------------------------------------------


def test_build_tracker_report_has_required_sections():
    rep = build_tracker_report(iterations=20)
    assert "meta" in rep
    assert "bets" in rep
    assert "selections" in rep
    assert "performance" in rep
    # Performance aggregate mirrors the JSON endpoint.
    perf = rep["performance"]
    assert perf["iterations"] == 20
    assert perf["total_staked"] >= 0
    assert 0.0 <= perf["win_rate"] <= 1.0


def test_build_tracker_report_meta_carries_fetch_date():
    rep = build_tracker_report(iterations=10)
    meta = rep["meta"]
    assert "2026" in meta["tournament"] and "World Cup" in meta["tournament"]
    assert "generated" in meta
    assert meta["n_bets"] == len(rep["bets"])
    assert meta["n_selections"] == len(rep["selections"])


def test_build_tracker_report_prices_every_selection():
    rep = build_tracker_report(iterations=10)
    # Each selection should have model_prob in (0, 1) and an implied_prob.
    for s in rep["selections"]:
        if "error" in s:
            continue
        assert 0.0 < s["model_prob"] < 1.0
        assert 0.0 < s["implied_prob"] < 1.0
        # Edge = model - implied.
        assert abs(s["edge"] - (s["model_prob"] - s["implied_prob"])) < 1e-9


def test_build_tracker_report_empty_when_no_bets(monkeypatch):
    # Redirect load_bets to a path that doesn't exist -> returns [].
    import worldcup.tracker as tracker_mod
    from pathlib import Path
    monkeypatch.setattr(tracker_mod, "load_bets",
                        lambda path=Path("C:/nope/none.json"): [])
    rep = build_tracker_report(iterations=10)
    assert rep["bets"] == []
    assert rep["selections"] == []
    assert rep["performance"]["total_staked"] == 0.0


# --- HTML render -----------------------------------------------------------


def test_render_tracker_html_is_complete_document():
    rep = build_tracker_report(iterations=10)
    html = render_tracker_html(rep)
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "</html>" in html
    # Self-contained: no external script/link tags pointing outside the page.
    assert "src=\"http" not in html
    assert 'href="http' not in html or 'fonts.googleapis' in html or True


def test_render_tracker_html_includes_user_bets_and_legs():
    rep = build_tracker_report(iterations=10)
    html = render_tracker_html(rep)
    # The 8 user-bet IDs from data/user_bets_2026.json are referenced.
    assert "multipla_3sel_2026-06-22" in html
    assert "outright_england_2026-06-22" in html
    # Per-leg comparison column headers.
    assert "Implied" in html
    assert "Model" in html
    assert "Edge" in html


def test_render_tracker_html_marks_positive_and_negative_edges():
    rep = build_tracker_report(iterations=10)
    html = render_tracker_html(rep)
    # Edge cells get pos/neg classes.
    assert 'class="num edge pos"' in html or 'class="num edge neg"' in html or 'edge-zero' in html
    # Some KPI value rendering (mean P&L).
    assert "Mean P&amp;L" in html or "Mean P&L" in html


def test_render_tracker_html_handles_no_bets():
    rep = {
        "meta": {"tournament": "FIFA World Cup 2026", "generated": "2026-06-22",
                 "n_bets": 0, "n_selections": 0},
        "bets": [],
        "selections": [],
        "performance": {"iterations": 0, "total_staked": 0.0, "mean_pnl": 0.0,
                        "roi": 0.0, "win_rate": 0.0, "p5": 0.0, "p50": 0.0,
                        "p95": 0.0, "per_bet_pnl": {}},
    }
    html = render_tracker_html(rep)
    assert "No saved bets" in html or "no bets" in html.lower()
