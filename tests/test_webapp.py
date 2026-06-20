"""Tests for the web UI backend (worldcup.webapp).

Exercises the offline JSON endpoints both directly (as functions) and through a
real in-process HTTP server roundtrip, so routing + serialisation are covered.
Network-backed endpoints (Polymarket games/perf/bets) are not hit here.
"""

import json
import threading
import urllib.request
from types import SimpleNamespace

import pytest

from worldcup import webapp
from worldcup.data_loader import load_results, refresh_results
from worldcup.lineup_store import load_saved


# --- direct function-level tests (no socket) -------------------------------

def test_api_meta_and_teams():
    meta = webapp.api_meta()
    assert meta["n_teams"] == 48
    assert meta["n_groups"] == 12
    assert len(meta["teams"]) == 48
    assert meta["stages"][0] == "group"
    assert meta["known_events"]  # at least the winner market

    teams = webapp.api_teams()
    assert len(teams["groups"]) == 12
    assert all(len(g["teams"]) == 4 for g in teams["groups"])


def test_api_squad_shape():
    name = webapp.api_meta()["teams"][0]["name"]
    sq = webapp.api_squad(name)
    assert sq["team"] == name
    assert sq["players"]
    xi = [p for p in sq["players"] if p["in_xi"]]
    assert len(xi) == 11
    assert sum(1 for p in xi if p["pos"] == "GK") >= 1
    # A starter with FM attributes exposes the grouped inspector data.
    starters = [p for p in sq["players"] if p["has_attrs"]]
    assert starters and "MENTAL" in starters[0]["attrs"]


def test_api_match_is_self_consistent():
    teams = webapp.api_meta()["teams"]
    out = webapp.api_match({"home": teams[0]["name"], "away": teams[1]["name"],
                            "stage": "group", "seed": 7})
    assert out["home"] == teams[0]["name"]
    assert out["home_goals"] == sum(1 for g in out["goals"] if g["team"] == out["home"])
    assert out["away_goals"] == sum(1 for g in out["goals"] if g["team"] == out["away"])
    assert set(out["chemistry"]) == {"home", "away"}


def test_api_match_rejects_same_team():
    n = webapp.api_meta()["teams"][0]["name"]
    with pytest.raises(ValueError):
        webapp.api_match({"home": n, "away": n})


def test_api_match_odds_probabilities_sum_to_one():
    teams = webapp.api_meta()["teams"]
    out = webapp.api_match_odds({"home": teams[0]["name"], "away": teams[1]["name"],
                                "stage": "group", "iterations": 60})
    assert out["iterations"] == 60
    total = sum(o["prob"] for o in out["outcome"])
    assert abs(total - 1.0) < 1e-9
    assert out["suggested"]["selection"]
    assert all("fair" in c for c in out["markets"])


def test_api_match_odds_knockout_has_no_draw():
    teams = webapp.api_meta()["teams"]
    out = webapp.api_match_odds({"home": teams[0]["name"], "away": teams[1]["name"],
                                "stage": "R16", "iterations": 40})
    assert out["knockout"] is True
    assert all(o["label"] != "Draw" for o in out["outcome"])


def test_api_tournament_produces_champion():
    out = webapp.api_tournament({"fresh": True, "seed": 1})
    assert len(out["groups"]) == 12
    assert out["champion"]
    assert out["knockout"][0]["stage"] == "R32"


def test_api_title_odds_table():
    out = webapp.api_title_odds({"iterations": 20, "top": 8, "fresh": True})
    assert len(out["rows"]) == 8
    assert all(0.0 <= r["champion"] <= 1.0 for r in out["rows"])
    # Sorted by champion probability, descending.
    champs = [r["champion"] for r in out["rows"]]
    assert champs == sorted(champs, reverse=True)


def test_api_lineups_roundtrip(monkeypatch, tmp_path):
    # Redirect persistence to a temp file so the real data/ file is untouched.
    import worldcup.lineup_store as ls
    monkeypatch.setattr(ls, "LINEUPS_PATH", tmp_path / "lineups.json")

    sq = webapp.api_squad(webapp.api_meta()["teams"][0]["name"])
    team, xi = sq["team"], sq["best_xi"]
    res = webapp.api_lineups_post({"team": team, "players": xi})
    assert res["ok"] and set(res["players"]) == set(xi)
    assert set(load_saved()[team]) == set(xi)

    # Empty selection clears the entry (falls back to best XI).
    webapp.api_lineups_post({"team": team, "players": []})
    assert team not in load_saved()


# --- data refresh (offline; Polymarket fetch monkeypatched) -----------------

def _fake_game(home, away, score, *, mapped=True, result="H"):
    return SimpleNamespace(home=home, away=away, score=score, date="2026-06-15",
                           mapped=mapped, result=result, title=f"{home} vs {away}",
                           slug=f"{home}-{away}".lower().replace(" ", "-"))


def test_refresh_results_writes_snapshot(monkeypatch, tmp_path):
    teams, tournament = webapp.world()
    groups = tournament.groups
    a_letter, a_names = next(iter(groups.items()))
    b_names = groups[[k for k in groups if k != a_letter][0]]
    g_home, g_away = a_names[0], a_names[1]          # same group -> group game
    k_home, k_away = a_names[2], b_names[0]          # cross-group -> knockout

    fake = [
        _fake_game(g_home, g_away, "2-1"),
        _fake_game(k_home, k_away, "0-0", result="D"),
        _fake_game("Mystery", "Unknown", None, mapped=False, result=None),
    ]
    monkeypatch.setattr(webapp.polymarket, "fetch_games", lambda teams, closed=None: fake)

    out = tmp_path / "results.json"
    summary = refresh_results(teams, tournament, path=out)
    assert summary["total"] == 2
    assert summary["group"] == 1 and summary["knockout"] == 1
    assert len(summary["skipped"]) == 1

    rows = load_results(out)
    assert {r.stage for r in rows} == {"group", "knockout"}
    by_pair = {(r.home, r.away): r for r in rows}
    assert by_pair[(g_home, g_away)].home_goals == 2


def test_api_refresh_composes_and_busts_cache(monkeypatch):
    monkeypatch.setattr(webapp, "refresh_results",
                        lambda teams, tournament: {"total": 3, "group": 3,
                                                   "knockout": 0, "fetched": "2026-06-20",
                                                   "skipped": []})
    monkeypatch.setattr(webapp, "refresh_odds",
                        lambda teams, tournament: {"winner": 5, "games": 10,
                                                   "pregame": 4, "fetched": "2026-06-20"})
    webapp._GAMES_CACHE["games"] = ["stale"]  # pretend a fixture list is cached

    out = webapp.api_refresh({})
    assert out["results"]["total"] == 3
    assert "odds" not in out                       # not requested
    assert out["meta"]["n_teams"] == 48            # fresh meta echoed back
    assert webapp._GAMES_CACHE["games"] is None    # cache invalidated

    out2 = webapp.api_refresh({"odds": True})
    assert out2["odds"]["winner"] == 5


# --- HTTP server roundtrip --------------------------------------------------

@pytest.fixture(scope="module")
def server():
    httpd = webapp.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, r.read().decode("utf-8")


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def test_index_served(server):
    status, body = _get(server, "/")
    assert status == 200
    assert body.startswith("<!DOCTYPE html>")
    assert "WC26" in body


def test_api_meta_over_http(server):
    status, body = _get(server, "/api/meta")
    assert status == 200
    assert json.loads(body)["n_teams"] == 48


def test_match_over_http(server):
    teams = json.loads(_get(server, "/api/meta")[1])["teams"]
    status, data = _post(server, "/api/match",
                         {"home": teams[0]["name"], "away": teams[1]["name"], "seed": 3})
    assert status == 200
    assert "score_str" in data


def test_bad_team_returns_400(server):
    req = urllib.request.Request(server + "/api/match",
                                 data=json.dumps({"home": "Narnia", "away": "Oz"}).encode(),
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=10)
    assert exc.value.code == 400
    assert "error" in json.loads(exc.value.read().decode())
