"""Interactive web UI for the World Cup simulator — the browser counterpart of
the terminal :mod:`worldcup.tui`, exposing the *same* set of features.

Like the TUI and :mod:`worldcup.html`, this is pure standard library: a small
``http.server`` backend whose JSON endpoints wrap the very same engine calls the
TUI screens make, plus a single self-contained single-page app (HTML/CSS/JS,
embedded below) that drives them. No build step, no third-party dependencies.

Run it with ``worldcup web`` (or ``python -m worldcup.webapp``); it opens a
browser at ``http://127.0.0.1:8000/``.

The endpoint ↔ TUI-screen mapping:

    /api/teams            Teams by group
    /api/squad,/lineups   Squads & starting XI (edit + persist lineups)
    /api/match            Simulate a match (live engine)
    /api/match-odds       Match odds (Monte Carlo)
    /api/tournament       Full tournament (one run)
    /api/title-odds       Title odds (Monte Carlo)
    /api/games*           Match odds vs market (Polymarket, live)
    /api/dashboard-live   Performance dashboard (model vs market, live)
    /api/bets             Polymarket: compare & bet
    /api/report,/dashboard.html   Model-vs-market dashboard (offline snapshot)
    /api/refresh          Update data (pull live results/odds into the snapshots)
"""

from __future__ import annotations

import json
import random
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse

from . import polymarket
from .betting import run_betting
from .data_loader import load_results, load_world_cup, refresh_results
from .engine import KNOCKOUT_STAGES, MatchSimulator
from .factors.builtin import ChemistryFactor, CoachFactor, club_clusters, team_intangibles
from .fm_rating import MENTAL, PHYSICAL, group_means, technical_attrs
from .games import (
    accuracy_summary,
    build_dashboard,
    compare_game,
    compare_games,
    find_game,
)
from .lineup_store import lineup_for, lineup_map, load_saved, resolve_lineup, save_saved
from .models import POSITIONS, Team
from .odds_store import load_odds, refresh_odds
from .report import build_report
from .sofascore_store import load_form_extras, load_sofascore, refresh_sofascore
from .tournament import TournamentSimulator

STAGES = ["group", "R32", "R16", "QF", "SF", "F"]

# Loaded once and shared read-only across request threads (simulations never
# mutate the team/tournament data).
_WORLD: Optional[tuple] = None
# Short-lived cache of the live Polymarket fixture list so drilling into one
# game doesn't refetch the whole series on every click.
_GAMES_CACHE: dict = {"ts": 0.0, "games": None}
_GAMES_TTL = 60.0


def world() -> tuple[dict[str, Team], object]:
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world_cup()
    return _WORLD


def _resolve_team(teams: dict[str, Team], query: str) -> Team:
    if query in teams:
        return teams[query]
    low = (query or "").lower()
    exact = [t for n, t in teams.items() if n.lower() == low]
    if exact:
        return exact[0]
    sub = [t for n, t in teams.items() if low and low in n.lower()]
    if len(sub) == 1:
        return sub[0]
    raise ValueError(f"no unique team matching {query!r}")


def _devig(triple) -> Optional[list]:
    if not triple or None in triple:
        return None
    s = sum(triple)
    return [x / s for x in triple] if s > 0 else None


def _cached_games() -> list:
    teams, _ = world()
    now = time.time()
    if _GAMES_CACHE["games"] is None or now - _GAMES_CACHE["ts"] > _GAMES_TTL:
        _GAMES_CACHE["games"] = polymarket.fetch_games(teams)
        _GAMES_CACHE["ts"] = now
    return _GAMES_CACHE["games"]


# --- small per-team serialisers (shared by match + squad views) -------------

def _chem(xi: list) -> dict:
    clusters = club_clusters(xi)
    cf = ChemistryFactor()
    pairs = sum(len(n) * (len(n) - 1) // 2 for n in clusters.values())
    bonus = min(cf.cap, cf.per_pair * pairs)
    return {"bonus": round(bonus, 2),
            "clusters": [{"club": c, "count": len(n)} for c, n in clusters.items()]}


def _coach(team: Team) -> Optional[dict]:
    c = team.coach or {}
    if not c:
        return None
    tilt = CoachFactor().tilt
    bias = c.get("bias", 0.0)
    return {"skill": c.get("skill", 0.0), "bias": bias,
            "att": bias * tilt, "def": -bias * tilt}


def _intan(xi: list) -> dict:
    mean, sigma = team_intangibles(xi)
    return {"mean": round(mean, 2), "sigma": round(sigma, 2)}


def _cond(team: Team) -> dict:
    if team.condition >= 1.0:
        return {"value": 1.0, "hit": 0.0, "injuries": ""}
    hit = (team.condition - 1.0) * 12.0
    return {"value": round(team.condition, 2), "hit": round(hit, 1),
            "injuries": team.injuries}


# --- API endpoints (one per TUI screen) ------------------------------------

def api_meta() -> dict:
    teams, tournament = world()
    odds = load_odds()
    sofa = load_sofascore()
    ordered = sorted(teams.values(), key=lambda t: (t.group or "Z", -t.rating, t.name))
    return {
        "tournament": tournament.name,
        "n_teams": len(teams),
        "n_groups": len(tournament.groups),
        "hosts": list(tournament.host_team_names),
        "stages": STAGES,
        "teams": [{"name": t.name, "group": t.group, "rating": round(t.rating, 1)}
                  for t in ordered],
        "known_events": [list(e) for e in polymarket.KNOWN_EVENTS],
        "has_odds_snapshot": odds is not None,
        "odds_fetched": odds.fetched if odds else None,
        "results_played": len(load_results()),
        "has_sofa_snapshot": sofa is not None,
        "sofa_fetched": sofa.fetched if sofa else None,
        "sofa_games": len(sofa.games) if sofa else 0,
    }


def api_teams() -> dict:
    teams, tournament = world()
    groups = [
        {"letter": letter,
         "teams": [{"name": n, "rating": round(teams[n].rating, 1)} for n in names]}
        for letter, names in tournament.groups.items()
    ]
    return {"tournament": tournament.name, "groups": groups}


def api_squad(team_name: str) -> dict:
    teams, _ = world()
    team = _resolve_team(teams, team_name)
    saved = load_saved()
    saved_names = saved.get(team.name) or []
    best_names = {p.name for p in team.best_lineup().players}
    current = set(saved_names) if saved_names else set(best_names)

    players = []
    for p in team.squad:
        g = group_means(p.pos, p.attributes)
        attrs = None
        if p.attributes:
            def rows(names):
                return [{"name": n, "value": p.attributes[n]}
                        for n in names if n in p.attributes]
            attrs = {"MENTAL": rows(MENTAL), "PHYSICAL": rows(PHYSICAL),
                     "TECHNICAL": rows(technical_attrs(p.pos))}
        mean, sigma = team_intangibles([p])
        players.append({
            "name": p.name, "pos": p.pos, "rating": round(p.rating, 1),
            "age": p.age, "club": p.club,
            "mental": round(g["mental"], 1), "physical": round(g["physical"], 1),
            "technical": round(g["technical"], 1),
            "in_best": p.name in best_names, "in_xi": p.name in current,
            "has_attrs": bool(p.attributes), "attrs": attrs,
            "intangible": {"mean": round(mean, 2), "sigma": round(sigma, 2)},
        })
    # FM-style display order: GK, DEF, MID, FWD then rating.
    players.sort(key=lambda d: (POSITIONS.index(d["pos"]), -d["rating"]))
    return {
        "team": team.name, "rating": round(team.rating, 1), "group": team.group,
        "coach": _coach(team), "condition": _cond(team),
        "best_xi": sorted(best_names),
        "saved": saved_names,
        "players": players,
    }


def api_lineups_get() -> dict:
    return {"saved": load_saved()}


def api_lineups_post(body: dict) -> dict:
    teams, _ = world()
    team = _resolve_team(teams, body.get("team", ""))
    names = [str(n) for n in body.get("players", [])]
    saved = load_saved()
    # Resolve against the squad; an empty/dropped selection clears the entry so
    # the team falls back to its best XI (matching the TUI's reset behaviour).
    lineup = resolve_lineup(team, names)
    if lineup and lineup.players:
        saved[team.name] = [p.name for p in lineup.players]
    else:
        saved.pop(team.name, None)
    save_saved(saved)
    return {"ok": True, "team": team.name, "players": saved.get(team.name, [])}


def _match_inputs(body: dict):
    teams, tournament = world()
    home = _resolve_team(teams, body.get("home", ""))
    away = _resolve_team(teams, body.get("away", ""))
    if home.name == away.name:
        raise ValueError("pick two different teams")
    stage = body.get("stage", "group")
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    hosts = set(tournament.host_team_names)
    host = bool(body.get("host")) if "host" in body else (home.name in hosts)
    return teams, home, away, stage, host


def api_match(body: dict) -> dict:
    teams, home, away, stage, host = _match_inputs(body)
    saved = load_saved()
    home_lineup = lineup_for(home, saved)
    away_lineup = lineup_for(away, saved)
    seed = body.get("seed")
    rng = random.Random(seed if seed not in (None, "") else None)
    sim = MatchSimulator(rng=rng)
    sofa = load_form_extras()
    r = sim.simulate_live(home, away, stage=stage, neutral=not host,
                          home_lineup=home_lineup, away_lineup=away_lineup,
                          home_extras=sofa.get(home.name), away_extras=sofa.get(away.name))
    home_xi = home_lineup.players if home_lineup else home.best_lineup().players
    away_xi = away_lineup.players if away_lineup else away.best_lineup().players

    goals = [(m, home.name, s, a) for m, s, a in r.home_scorers]
    goals += [(m, away.name, s, a) for m, s, a in r.away_scorers]
    goals.sort(key=lambda e: e[0])

    def subs(rows):
        return [{"minute": m, "off": off, "on": on} for m, off, on in rows]

    return {
        "home": home.name, "away": away.name, "stage": stage, "host": host,
        "home_goals": r.home_goals, "away_goals": r.away_goals,
        "score_str": r.score_str(), "winner": r.winner,
        "home_xg": round(r.home_xg, 2), "away_xg": round(r.away_xg, 2),
        "extra_time": r.extra_time, "penalties": r.penalties,
        "home_pens": r.home_pens, "away_pens": r.away_pens,
        "goals": [{"minute": m, "team": t, "scorer": s, "assist": a}
                  for m, t, s, a in goals],
        "home_subs": subs(r.home_subs), "away_subs": subs(r.away_subs),
        "cooling_breaks": r.cooling_breaks,
        "home_custom": home_lineup is not None, "away_custom": away_lineup is not None,
        "chemistry": {"home": _chem(home_xi), "away": _chem(away_xi)},
        "coach": {"home": _coach(home), "away": _coach(away)},
        "intangibles": {"home": _intan(home_xi), "away": _intan(away_xi)},
        "condition": {"home": _cond(home), "away": _cond(away)},
    }


def api_match_odds(body: dict) -> dict:
    teams, home, away, stage, host = _match_inputs(body)
    n = max(1, min(20000, int(body.get("iterations", 1000))))
    saved = load_saved()
    home_lineup = lineup_for(home, saved)
    away_lineup = lineup_for(away, saved)
    knockout = stage in KNOCKOUT_STAGES
    sim = MatchSimulator(rng=random.Random())
    sofa = load_form_extras()
    odds = sim.monte_carlo(home, away, n, stage=stage, neutral=not host,
                           home_lineup=home_lineup, away_lineup=away_lineup,
                           home_extras=sofa.get(home.name), away_extras=sofa.get(away.name),
                           track_scorers=True)

    verb = "advances" if knockout else "win"
    outcome = [{"label": f"{home.name} {verb}", "prob": odds.home_win}]
    if not knockout:
        outcome.append({"label": "Draw", "prob": odds.draw})
    outcome.append({"label": f"{away.name} {verb}", "prob": odds.away_win})

    def scorers(ranked):
        return [{"name": name, "goals": round(g, 2), "assists": round(a, 2)}
                for name, g, a in ranked if g > 0][:5]

    bet_verb = "to advance" if knockout else "to win"
    cands = [
        {"market": "Match result", "selection": f"{home.name} {bet_verb}", "prob": odds.home_win},
        {"market": "Match result", "selection": f"{away.name} {bet_verb}", "prob": odds.away_win},
        {"market": "Total goals", "selection": "Over 2.5 goals", "prob": odds.over25},
        {"market": "Total goals", "selection": "Under 2.5 goals", "prob": 1.0 - odds.over25},
        {"market": "Both teams to score", "selection": "Yes", "prob": odds.btts},
        {"market": "Both teams to score", "selection": "No", "prob": 1.0 - odds.btts},
    ]
    if not knockout:
        cands.append({"market": "Match result", "selection": "Draw", "prob": odds.draw})
    if odds.scorelines:
        (hg, ag), p = odds.scorelines[0]
        cands.append({"market": "Correct score",
                      "selection": f"{home.name} {hg}-{ag} {away.name}", "prob": p})
    for c in cands:
        c["fair"] = (1.0 / c["prob"]) if c["prob"] > 0 else None
    pick = max(cands, key=lambda c: c["prob"])

    return {
        "home": home.name, "away": away.name, "stage": stage, "host": host,
        "iterations": odds.iterations, "knockout": knockout,
        "home_custom": home_lineup is not None, "away_custom": away_lineup is not None,
        "outcome": outcome,
        "avg_goals": [round(odds.avg_home_goals, 2), round(odds.avg_away_goals, 2)],
        "avg_xg": [round(odds.avg_home_xg, 2), round(odds.avg_away_xg, 2)],
        "home_scorers": scorers(odds.home_scorers),
        "away_scorers": scorers(odds.away_scorers),
        "scorelines": [{"score": f"{hg}-{ag}", "prob": p} for (hg, ag), p in odds.scorelines],
        "suggested": pick,
        "markets": cands,
    }


def _outcome_payload(outcome, n_results: int) -> dict:
    groups = []
    for g in outcome.groups:
        groups.append({
            "letter": g.letter,
            "standings": [{"team": s.team, "played": s.played, "won": s.won,
                           "drawn": s.drawn, "lost": s.lost, "gf": s.gf, "ga": s.ga,
                           "gd": s.gd, "pts": s.pts} for s in g.standings],
        })
    rounds = []
    for rnd in outcome.knockout.rounds:
        rounds.append({
            "stage": rnd[0].stage,
            "matches": [{"home": m.home, "away": m.away,
                         "home_goals": m.home_goals, "away_goals": m.away_goals,
                         "score_str": m.score_str(), "winner": m.winner,
                         "penalties": m.penalties, "extra_time": m.extra_time}
                        for m in rnd],
        })
    return {"groups": groups, "knockout": rounds, "champion": outcome.champion,
            "results_seeded": n_results}


def api_tournament(body: dict) -> dict:
    teams, tournament = world()
    seed = body.get("seed")
    fresh = bool(body.get("fresh"))
    results = [] if fresh else load_results()
    sofa = {} if fresh else load_form_extras()
    rng = random.Random(seed if seed not in (None, "") else None)
    sim = TournamentSimulator(teams, tournament, rng=rng, results=results)
    outcome = sim.run_once(lineups=lineup_map(teams), extras=sofa)
    return _outcome_payload(outcome, len(results))


def api_title_odds(body: dict) -> dict:
    teams, tournament = world()
    n = max(1, min(20000, int(body.get("iterations", 1000))))
    top = max(1, min(48, int(body.get("top", 16))))
    fresh = bool(body.get("fresh"))
    results = [] if fresh else load_results()
    sofa = {} if fresh else load_form_extras()
    sim = TournamentSimulator(teams, tournament, rng=random.Random(), results=results)
    report = sim.monte_carlo(n, lineups=lineup_map(teams), extras=sofa)
    rows = []
    for name, _ in report.title_odds()[:top]:
        r = report.reach[name]
        rows.append({"team": name, "group": teams[name].group,
                     "r16": r["R16"], "qf": r["QF"], "sf": r["SF"],
                     "final": r["Final"], "champion": r["Champion"]})
    return {"iterations": report.iterations, "results_seeded": len(results), "rows": rows}


# --- data refresh (network) -------------------------------------------------

def api_refresh(body: dict) -> dict:
    """Pull the latest live data into the offline snapshots.

    Always refreshes ``data/results_2026.json`` (the played scores that seed real
    standings); optionally also ``data/odds_2026.json`` (the market snapshot —
    slower, since it recovers pre-kickoff odds per played game) and
    ``data/sofascore_2026.json`` (per-player match ratings → in-tournament form).
    Every other screen reads these files fresh, so the new data takes effect
    immediately. The SofaScore fetch is best-effort: a failure is reported in the
    response rather than aborting the whole refresh or wiping a good snapshot.
    """
    teams, tournament = world()
    out: dict = {"results": refresh_results(teams, tournament)}
    if body.get("odds"):
        out["odds"] = refresh_odds(teams, tournament)
    if body.get("sofa"):
        try:
            out["sofa"] = refresh_sofascore(teams, tournament)
        except Exception as exc:  # SofaScore API is fragile; never fatal
            out["sofa"] = {"error": f"{type(exc).__name__}: {exc}"}
    # New live data invalidates the cached fixture list used by the games views.
    _GAMES_CACHE["games"] = None
    _GAMES_CACHE["ts"] = 0.0
    out["meta"] = api_meta()  # fresh snapshot counts for the UI to re-render
    return out


# --- live Polymarket-backed endpoints (network) -----------------------------

def api_games_list() -> dict:
    games = [g for g in _cached_games() if g.mapped]

    def row(g):
        return {"home": g.home, "away": g.away, "date": g.date, "slug": g.slug,
                "closed": g.closed, "score": g.score, "result": g.result,
                "live": _devig(g.live_probs)}

    return {"upcoming": [row(g) for g in games if not g.closed],
            "played": [row(g) for g in games if g.closed]}


def api_game_detail(body: dict) -> dict:
    teams, tournament = world()
    hosts = set(tournament.host_team_names)
    games = [g for g in _cached_games() if g.mapped]
    query = body.get("slug") or f"{body.get('home', '')} vs {body.get('away', '')}"
    g = find_game(games, query)
    if g is None:
        raise ValueError("fixture not found")
    n = max(1, min(20000, int(body.get("iterations", 3000))))
    # SofaScore form feeds upcoming fixtures only; a played game is graded against
    # its pre-kickoff market, so we don't leak later-game form into the backtest.
    extras = None if g.closed else load_form_extras()
    comp = compare_game(MatchSimulator(rng=random.Random()), teams, g, n,
                        host_names=hosts, extras=extras)
    return {
        "home": g.home, "away": g.away, "date": g.date, "closed": g.closed,
        "score": g.score, "result": g.result, "iterations": n,
        "model": list(comp.model),
        "market": list(comp.market) if comp.market else None,
        "edges": list(comp.edges) if comp.edges else None,
        "model_pick": comp.model_pick, "market_pick": comp.market_pick,
    }


def api_games_accuracy(body: dict) -> dict:
    teams, tournament = world()
    hosts = set(tournament.host_team_names)
    played = [g for g in _cached_games() if g.mapped and g.closed]
    n = max(1, min(20000, int(body.get("iterations", 2000))))
    comps = compare_games(MatchSimulator(rng=random.Random()), teams, played, n, host_names=hosts)
    s = accuracy_summary(comps)
    rows = [{"home": c.game.home, "away": c.game.away, "score": c.game.score,
             "result": c.game.result, "model": list(c.model),
             "market": list(c.market) if c.market else None}
            for c in comps]
    return {"n": s.n, "model_correct": s.model_correct, "market_correct": s.market_correct,
            "model_brier": s.model_brier, "market_brier": s.market_brier,
            "iterations": n, "games": rows}


def api_dashboard_live(body: dict) -> dict:
    teams, tournament = world()
    hosts = set(tournament.host_team_names)
    played = [g for g in _cached_games() if g.mapped and g.closed]
    if not played:
        return {"n": 0, "games": [], "message": "No finished fixtures yet — check back after kickoff."}
    n = max(1, min(20000, int(body.get("iterations", 2000))))
    comps = compare_games(MatchSimulator(rng=random.Random()), teams, played, n, host_names=hosts)
    d = build_dashboard(comps)
    s = d.summary
    return {
        "n": s.n, "iterations": n,
        "model_correct": s.model_correct, "market_correct": s.market_correct,
        "model_brier": s.model_brier, "market_brier": s.market_brier,
        "model_logloss": d.model_logloss, "market_logloss": d.market_logloss,
        "model_sharper": d.model_sharper, "result_mix": d.result_mix,
        "disagreements": d.disagreements,
        "model_edge_wins": d.model_edge_wins, "market_edge_wins": d.market_edge_wins,
        "games": [{"home": gr.comparison.game.home, "away": gr.comparison.game.away,
                   "score": gr.comparison.game.score, "result": gr.result,
                   "model_pick": gr.comparison.model_pick,
                   "market_pick": gr.comparison.market_pick,
                   "model_right": gr.model_right, "market_right": gr.market_right,
                   "disagreed": gr.disagreed} for gr in d.games],
    }


def api_bets(body: dict) -> dict:
    teams, tournament = world()
    slug = body.get("slug") or "world-cup-winner"
    n = max(1, min(20000, int(body.get("iterations", 2000))))
    bankroll = float(body.get("bankroll", 100.0))
    event = polymarket.fetch_event(slug, teams)
    if not event.matched:
        raise ValueError(f"none of {event.title!r}'s teams map to our squads")
    sim = TournamentSimulator(teams, tournament, rng=random.Random(), results=load_results())
    rep = run_betting(sim, event, n, bankroll=bankroll, kelly_mult=0.5, min_edge=0.02)
    return {
        "title": rep.event_title, "iterations": rep.iterations, "bankroll": bankroll,
        "comparisons": [{"team": c.team, "market": c.market_prob, "model": c.model_prob,
                         "edge": c.edge, "ev": c.ev} for c in rep.comparisons],
        "bets": [{"team": b.team, "stake": b.stake, "price": b.price,
                  "shares": b.shares, "edge": b.edge, "ev": b.ev} for b in rep.bets],
        "unmatched": rep.unmatched, "total_staked": rep.total_staked,
        "mean_pnl": rep.mean_pnl, "roi": rep.roi, "win_rate": rep.win_rate,
        "p5": rep.percentile(0.05), "p50": rep.percentile(0.50), "p95": rep.percentile(0.95),
    }


def api_report(qs: dict) -> dict:
    teams, tournament = world()

    def q(name, default):
        return int(qs.get(name, [str(default)])[0])

    return build_report(
        teams, tournament, load_results(), load_odds(),
        title_iters=max(1, min(20000, q("title_iters", 1500))),
        game_iters=max(1, min(20000, q("game_iters", 800))),
        sofa_extras=load_form_extras(),
        seed=None,
    )


def api_dashboard_html(qs: dict) -> str:
    from .html import render_dashboard
    return render_dashboard(api_report(qs))


# --- HTTP plumbing ----------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "worldcup-web/1.0"

    def log_message(self, *args) -> None:  # keep the console quiet
        pass

    def _json(self, payload, status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _html(self, text: str, status: int = 200) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw or b"{}")

    def _dispatch(self, fn) -> None:
        """Run an endpoint, translating exceptions into JSON error responses."""
        try:
            self._json(fn())
        except polymarket.PolymarketError as exc:
            self._json({"error": f"Polymarket: {exc}"}, 502)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:  # pragma: no cover - defensive
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)
        if path in ("/", "/index.html"):
            return self._html(_INDEX_HTML)
        if path == "/dashboard.html":
            try:
                return self._html(api_dashboard_html(qs))
            except Exception as exc:
                return self._html(f"<h1>Error</h1><pre>{exc}</pre>", 500)
        routes = {
            "/api/meta": api_meta,
            "/api/teams": api_teams,
            "/api/lineups": api_lineups_get,
            "/api/games": api_games_list,
            "/api/squad": lambda: api_squad(qs.get("team", [""])[0]),
            "/api/report": lambda: api_report(qs),
        }
        fn = routes.get(path)
        if fn is None:
            return self._json({"error": "not found"}, 404)
        self._dispatch(fn)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self._body()
        except json.JSONDecodeError:
            return self._json({"error": "invalid JSON body"}, 400)
        routes = {
            "/api/lineups": api_lineups_post,
            "/api/match": api_match,
            "/api/match-odds": api_match_odds,
            "/api/tournament": api_tournament,
            "/api/title-odds": api_title_odds,
            "/api/refresh": api_refresh,
            "/api/games/detail": api_game_detail,
            "/api/games/accuracy": api_games_accuracy,
            "/api/dashboard-live": api_dashboard_live,
            "/api/bets": api_bets,
        }
        fn = routes.get(path)
        if fn is None:
            return self._json({"error": "not found"}, 404)
        self._dispatch(lambda: fn(body))


def make_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    """Build (but don't start) the threaded HTTP server. Used by tests."""
    world()  # fail fast if data is missing
    return ThreadingHTTPServer((host, port), Handler)


def serve(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> None:
    httpd = make_server(host, port)
    url = f"http://{host}:{httpd.server_address[1]}/"
    print(f"World Cup web UI running on {url}  (Ctrl-C to stop)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        httpd.server_close()


# --- the single-page app (HTML + CSS + JS, fully self-contained) ------------
# Not an f-string: all dynamic data is fetched from the JSON API above.

_INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Cup 2026 · Simulator</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@600;800;900&family=Spline+Sans:wght@400;500;600&family=Spline+Sans+Mono:wght@500;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#070b09; --panel:#0e1714; --panel2:#13211a;
  --ink:#e9f2ec; --muted:#7e958a; --faint:#46564d;
  --line:rgba(180,255,210,.10); --line2:rgba(180,255,210,.05);
  --accent:#b8ff3c; --accent-dim:rgba(184,255,60,.14);
  --market:#57c7ff; --gold:#ffce4d; --pos:#5be08c; --neg:#ff6b7a;
  --home:#3ddc97; --draw:#54655d; --away:#7aa2ff;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0; background:var(--bg); color:var(--ink); font-size:15px; line-height:1.5;
  font-family:"Spline Sans",system-ui,sans-serif; -webkit-font-smoothing:antialiased;
  background-image:
    radial-gradient(900px 520px at 8% -8%, rgba(184,255,60,.08), transparent 60%),
    radial-gradient(820px 600px at 105% 4%, rgba(87,199,255,.07), transparent 55%);
  background-attachment:fixed;}
h1,h2,h3{font-family:"Archivo",sans-serif; margin:0; letter-spacing:-.015em}
.num,.mono{font-family:"Spline Sans Mono",monospace; font-variant-numeric:tabular-nums}
a{color:var(--market)}
.layout{display:grid; grid-template-columns:248px 1fr; min-height:100vh}

/* sidebar */
.side{position:sticky; top:0; align-self:start; height:100vh; overflow-y:auto;
  border-right:1px solid var(--line); padding:18px 14px;
  background:linear-gradient(180deg, rgba(14,23,20,.6), rgba(7,11,9,.2))}
.brand{font-family:"Archivo"; font-weight:900; font-size:19px; color:var(--accent);
  letter-spacing:.02em; padding:6px 10px 14px}
.brand small{display:block; color:var(--muted); font-weight:600; font-size:11px;
  letter-spacing:.16em; text-transform:uppercase; margin-top:3px}
.navbtn{display:block; width:100%; text-align:left; border:0; cursor:pointer;
  background:transparent; color:var(--muted); font:inherit; font-weight:500;
  padding:9px 12px; border-radius:10px; margin:2px 0; transition:.15s}
.navbtn:hover{color:var(--ink); background:var(--line2)}
.navbtn.active{color:var(--bg); background:var(--accent); font-weight:600}
.navbtn .n{display:inline-block; width:22px; color:var(--faint); font-family:"Spline Sans Mono"; font-size:12px}
.navbtn.active .n{color:rgba(7,11,9,.5)}

/* main */
main{padding:30px 34px 90px; max-width:1180px}
.sec-head{margin-bottom:18px}
.sec-head h2{font-size:clamp(22px,3vw,30px); font-weight:800; text-transform:uppercase}
.sec-head h2::before{content:""; display:inline-block; width:9px; height:9px; border-radius:3px;
  background:var(--accent); margin-right:11px; vertical-align:middle; transform:translateY(-3px)}
.sec-head p{color:var(--muted); margin:6px 0 0; font-size:13.5px}

/* controls */
.controls{display:flex; flex-wrap:wrap; gap:12px 16px; align-items:flex-end;
  background:var(--panel); border:1px solid var(--line); border-radius:14px;
  padding:14px 16px; margin-bottom:18px}
.controls label{display:flex; flex-direction:column; gap:5px; font-size:12px;
  color:var(--muted); text-transform:uppercase; letter-spacing:.05em}
.controls label.chk{flex-direction:row; align-items:center; gap:7px; text-transform:none;
  letter-spacing:0; font-size:13.5px; color:var(--ink); align-self:center}
select,input[type=number],input[type=text]{background:var(--panel2); color:var(--ink);
  border:1px solid var(--line); border-radius:9px; padding:8px 10px; font:inherit; min-width:120px}
input[type=checkbox]{width:16px; height:16px; accent-color:var(--accent)}
button.primary,button.go,a.go{cursor:pointer; font:inherit; font-weight:600; border:0; border-radius:10px;
  padding:9px 18px; background:var(--accent); color:var(--bg)}
button.go,a.go{display:inline-block; background:var(--panel2); color:var(--ink); border:1px solid var(--line)}
button.primary:disabled,button.go:disabled{opacity:.5; cursor:default}
button.mini{cursor:pointer; font:inherit; font-size:12px; font-weight:600; border:1px solid var(--line);
  border-radius:8px; padding:4px 10px; background:var(--panel2); color:var(--ink)}
button.mini:hover{border-color:var(--accent)}

/* cards & tables */
.card{background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:16px 18px; margin-bottom:14px}
.grid{display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr)); gap:13px}
table{width:100%; border-collapse:collapse}
th,td{padding:8px 10px; text-align:left; border-bottom:1px solid var(--line2); font-size:14px}
th{font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); font-weight:600}
th.num,td.num{text-align:right; font-family:"Spline Sans Mono"}
tbody tr:hover td{background:rgba(184,255,60,.04)}
.gteam{font-weight:500}
.group-card .group-top{display:flex; align-items:center; gap:10px; font-family:"Archivo";
  font-weight:700; text-transform:uppercase; color:var(--muted); font-size:13px; margin-bottom:6px}
.gletter{display:grid; place-items:center; width:25px; height:25px; border-radius:8px;
  background:var(--accent-dim); color:var(--accent); font-weight:900; font-size:13px}
.lead td{background:linear-gradient(90deg, rgba(255,206,77,.08), transparent 70%)}
.lead .gteam{color:var(--gold)}
.pos{color:var(--pos)} .neg{color:var(--neg)} .gold{color:var(--gold)}
.lime{color:var(--accent)} .blue{color:var(--market)}
.faint{color:var(--faint)}

/* misc widgets */
.spinner{display:flex; align-items:center; gap:12px; color:var(--muted); padding:26px 4px; font-size:14px}
.spinner i{width:18px; height:18px; border:3px solid var(--line); border-top-color:var(--accent);
  border-radius:50%; animation:spin .8s linear infinite; display:inline-block}
@keyframes spin{to{transform:rotate(360deg)}}
.error{color:var(--neg); background:rgba(255,107,122,.08); border:1px solid rgba(255,107,122,.3);
  border-radius:12px; padding:14px 16px; margin:6px 0}
.note{color:var(--gold); font-size:13px; margin-bottom:10px}
.chips{display:flex; flex-wrap:wrap; gap:7px}
.chip{font-size:12px; font-weight:600; padding:3px 10px; border-radius:999px;
  background:var(--panel2); border:1px solid var(--line); color:var(--muted)}
.chip.ok{background:var(--accent-dim); color:var(--accent); border-color:transparent}
.chip.bad{background:rgba(255,107,122,.12); color:var(--neg); border-color:transparent}
.score-big{font-family:"Archivo"; font-weight:900; font-size:40px; text-align:center; margin:6px 0}
.score-big .w{color:var(--accent)}
.kv{display:grid; grid-template-columns:auto 1fr; gap:4px 14px; font-size:13.5px}
.kv .k{color:var(--muted)}
.seg{display:flex; height:8px; border-radius:5px; overflow:hidden; background:var(--line); margin:7px 0}
.seg i{height:100%}
.seg .h{background:var(--home)} .seg .d{background:var(--draw)} .seg .a{background:var(--away)}
.bar{height:9px; border-radius:6px; background:var(--line); overflow:hidden; min-width:60px}
.bar i{display:block; height:100%; background:linear-gradient(90deg,#7fd520,var(--accent))}
.champ{margin-top:14px; text-align:center; font-family:"Archivo"; font-weight:900;
  font-size:26px; color:var(--gold); text-transform:uppercase; letter-spacing:.02em}
.cols{display:grid; grid-template-columns:1fr 1fr; gap:14px}
.tag{font-size:10px; font-weight:700; color:var(--muted); border:1px solid var(--line);
  border-radius:5px; padding:1px 6px; margin-left:7px}
.subtle{color:var(--muted); font-size:13px}
.rowline{display:flex; justify-content:space-between; align-items:center; gap:10px;
  padding:8px 0; border-bottom:1px solid var(--line2)}
.rowline:last-child{border-bottom:0}
.pill{cursor:pointer}
@media(max-width:820px){
  .layout{grid-template-columns:1fr} .side{position:static; height:auto; display:flex;
    flex-wrap:wrap; gap:4px} .brand{width:100%} .navbtn{width:auto}
  main{padding:20px} .cols{grid-template-columns:1fr}
}
</style>
</head>
<body>
<div class="layout">
  <aside class="side">
    <div class="brand">⚽ WC26<small id="brandSub">Simulator</small></div>
    <nav id="nav"></nav>
  </aside>
  <main id="view"><div class="spinner"><i></i>Loading…</div></main>
</div>
<script>
const $=(s,r=document)=>r.querySelector(s);
const $$=(s,r=document)=>[...r.querySelectorAll(s)];
let M=null, SQ=null;

const NAV=[
  ["teams","Teams by group",vTeams],
  ["squads","Squads & starting XI",vSquads],
  ["match","Simulate a match",vMatch],
  ["odds","Match odds (Monte Carlo)",vOdds],
  ["tournament","Full tournament",vTournament],
  ["title","Title odds (Monte Carlo)",vTitle],
  ["games","Match odds vs market",vGames],
  ["perf","Performance dashboard",vPerf],
  ["bets","Polymarket: compare & bet",vBets],
  ["dashboard","Model-vs-market dashboard",vDashboard],
  ["update","Update data (live)",vUpdate],
];

async function get(p){const r=await fetch(p);const d=await r.json().catch(()=>({error:'bad response'}));if(d&&d.error)throw new Error(d.error);return d;}
async function post(p,b){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});const d=await r.json().catch(()=>({error:'bad response'}));if(d&&d.error)throw new Error(d.error);return d;}
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pc0=x=>x==null?'—':(x*100).toFixed(0)+'%';
const pc1=x=>x==null?'—':(x*100).toFixed(1)+'%';
const sgn=x=>x==null?'—':((x>=0?'+':'')+(x*100).toFixed(1)+'%');
const money=x=>(x<0?'-$':'$')+Math.abs(x).toFixed(2);
const spin=m=>`<div class="spinner"><i></i>${esc(m||'Working…')}</div>`;

function teamSelect(id){const o=M.teams.map(t=>`<option value="${esc(t.name)}">[${esc(t.group)}] ${esc(t.name)}</option>`).join('');
  return `<select id="${id}">${o}</select>`;}
function stageSelect(id){const L={group:'Group (draws ok)',R32:'R32',R16:'R16',QF:'QF',SF:'SF',F:'Final'};
  return `<select id="${id}">`+M.stages.map(s=>`<option value="${s}">${L[s]||s}</option>`).join('')+`</select>`;}

// run an async action with a button-spinner into a target container
async function run(btn,target,msg,fn){
  const old=btn?btn.textContent:null; if(btn){btn.disabled=true; btn.textContent='Running…';}
  if(target) target.innerHTML=spin(msg);
  try{ await fn(); }
  catch(e){ if(target) target.innerHTML=`<div class="error">${esc(e.message)}</div>`; }
  finally{ if(btn){btn.disabled=false; btn.textContent=old;} }
}

function setView(html){ $('#view').innerHTML=html; }
function head(title,sub){ return `<div class="sec-head"><h2>${esc(title)}</h2><p>${sub}</p></div>`; }
function segbar(t){ if(!t) return '<div class="seg"></div>';
  return `<div class="seg"><i class="h" style="width:${t[0]*100}%"></i><i class="d" style="width:${t[1]*100}%"></i><i class="a" style="width:${t[2]*100}%"></i></div>`; }

/* ---- 1. Teams ---- */
async function vTeams(){
  setView(head('Teams by group',`${M.n_teams} teams · ${M.tournament}`)+`<div id="out">${spin('Loading…')}</div>`);
  try{ const d=await get('/api/teams');
    $('#out').innerHTML='<div class="grid">'+d.groups.map(g=>`
      <div class="card group-card"><div class="group-top"><span class="gletter">${esc(g.letter)}</span>Group ${esc(g.letter)}</div>
      <table><tbody>${g.teams.map(t=>`<tr><td class="gteam">${esc(t.name)}</td><td class="num">${t.rating.toFixed(1)}</td></tr>`).join('')}</tbody></table></div>`).join('')+'</div>';
  }catch(e){ $('#out').innerHTML=`<div class="error">${esc(e.message)}</div>`; }
}

/* ---- 2. Squads & XI editor ---- */
function vSquads(){
  setView(head('Squads & starting XI','Toggle the XI, inspect FM attributes, and save. Saved XIs feed every simulation.')
    +`<div class="controls"><label>Team ${teamSelect('sqTeam')}</label>
      <button class="go" id="sqLoad">Load</button></div><div id="out"></div>`);
  $('#sqLoad').onclick=loadSquad; loadSquad();
}
async function loadSquad(){
  const team=$('#sqTeam').value;
  await run($('#sqLoad'),$('#out'),'Loading squad…',async()=>{
    const d=await get('/api/squad?team='+encodeURIComponent(team));
    SQ={team:d.team, meta:d, players:d.players,
        xi:new Set(d.players.filter(p=>p.in_xi).map(p=>p.name)),
        best:new Set(d.players.filter(p=>p.in_best).map(p=>p.name)), dirty:false};
    paintSquad();
  });
}
function chemOf(names){const by={}; SQ.players.forEach(p=>{ if(names.has(p.name)&&p.club){(by[p.club]=by[p.club]||[]).push(p.name);} });
  return Object.entries(by).filter(([,v])=>v.length>=2);}
function pRow(p){const inXi=SQ.xi.has(p.name);
  return `<div class="rowline"><div><b>[${esc(p.pos)}]</b> ${esc(p.name)}
      <span class="tag">${p.rating.toFixed(1)}</span>${p.age?`<span class="subtle"> ${p.age}y</span>`:''}
      <span class="subtle">· M${p.mental} F${p.physical} T${p.technical}${p.club?' · '+esc(p.club):''}</span></div>
    <div class="chips"><button class="mini" data-toggle="${esc(p.name)}">${inXi?'→ bench':'→ XI'}</button>
      ${p.has_attrs?`<button class="mini" data-inspect="${esc(p.name)}">inspect</button>`:''}</div></div>`;
}
function paintSquad(){
  const m=SQ.meta, xi=SQ.players.filter(p=>SQ.xi.has(p.name)), bench=SQ.players.filter(p=>!SQ.xi.has(p.name));
  const gk=xi.filter(p=>p.pos==='GK').length;
  const chem=chemOf(SQ.xi); const chemTxt=chem.length?chem.map(([c,v])=>`${esc(c)} ×${v.length}`).join(', '):'none';
  const coach=m.coach?`skill ${m.coach.skill>=0?'+':''}${m.coach.skill.toFixed(1)} · att ${m.coach.att>=0?'+':''}${m.coach.att.toFixed(1)} / def ${m.coach.def>=0?'+':''}${m.coach.def.toFixed(1)}`:'no profile';
  const cond=m.condition.value>=1?'full fitness':`${m.condition.value} (${m.condition.hit>=0?'+':''}${m.condition.hit})${m.condition.injuries?' · '+esc(m.condition.injuries):''}`;
  $('#out').innerHTML=`
    <div class="card"><div class="kv">
      <span class="k">Base rating</span><span>${m.rating.toFixed(1)} · group ${esc(m.group)}</span>
      <span class="k">Chemistry (XI)</span><span>${chemTxt}</span>
      <span class="k">Coach</span><span>${coach}</span>
      <span class="k">Condition</span><span>${cond}</span>
    </div></div>
    <div class="cols">
      <div class="card"><h3>Starting XI (${xi.length}/11)${gk?'':' <span class="neg">— need a GK!</span>'}</h3><div id="xiList">${xi.map(pRow).join('')}</div></div>
      <div class="card"><h3>Bench (${bench.length})</h3><div id="benchList">${bench.map(pRow).join('')}</div></div>
    </div>
    <div class="controls" style="margin-top:14px">
      <button class="primary" id="sqSave">Save XI</button>
      <button class="go" id="sqReset">Reset to best XI</button>
      <span class="subtle" id="sqStatus">${SQ.dirty?'unsaved changes':''}</span>
    </div>
    <div id="inspect"></div>`;
  $$('[data-toggle]').forEach(b=>b.onclick=()=>{const n=b.dataset.toggle; SQ.xi.has(n)?SQ.xi.delete(n):SQ.xi.add(n); SQ.dirty=true; paintSquad();});
  $$('[data-inspect]').forEach(b=>b.onclick=()=>inspect(b.dataset.inspect));
  $('#sqSave').onclick=async()=>{ await run($('#sqSave'),null,'',async()=>{
      await post('/api/lineups',{team:SQ.team,players:[...SQ.xi]}); SQ.dirty=false; $('#sqStatus').textContent='saved ✓'; }); };
  $('#sqReset').onclick=()=>{ SQ.xi=new Set(SQ.best); SQ.dirty=true; paintSquad(); };
}
function inspect(name){
  const p=SQ.players.find(x=>x.name===name); if(!p||!p.attrs) return;
  const bar=v=>`<div class="bar" style="display:inline-block;width:120px;vertical-align:middle"><i style="width:${v/20*100}%"></i></div>`;
  const grp=(lab,rows)=>rows.length?`<h3 style="margin-top:12px">${lab}</h3>`+rows.map(a=>`<div class="rowline"><span>${esc(a.name)}</span><span>${a.value} ${bar(a.value)}</span></div>`).join(''):'';
  $('#inspect').innerHTML=`<div class="card"><div class="rowline"><h3>${esc(p.name)} · ${esc(p.pos)} · ${esc(p.club||'—')} · ${p.age||'—'}y</h3>
    <button class="mini" id="closeInspect">close</button></div>
    <div class="subtle">rating ${p.rating.toFixed(1)} · intangible mean ${p.intangible.mean>=0?'+':''}${p.intangible.mean} swing ±${p.intangible.sigma}</div>
    ${grp('Mental',p.attrs.MENTAL)}${grp('Physical',p.attrs.PHYSICAL)}${grp('Technical',p.attrs.TECHNICAL)}</div>`;
  $('#closeInspect').onclick=()=>$('#inspect').innerHTML='';
  $('#inspect').scrollIntoView({behavior:'smooth',block:'nearest'});
}

/* ---- 3. Simulate a match ---- */
function vMatch(){
  setView(head('Simulate a match','One full live match — scorers, subs, chemistry, coaching, intangibles, condition.')
    +`<div class="controls">
      <label>Home ${teamSelect('mHome')}</label>
      <label>Away ${teamSelect('mAway')}</label>
      <label>Stage ${stageSelect('mStage')}</label>
      <label class="chk"><input type="checkbox" id="mHost"> Host plays at home</label>
      <label>Seed <input type="number" id="mSeed" placeholder="random" style="min-width:90px"></label>
      <button class="primary" id="mRun">Simulate</button></div><div id="out"></div>`);
  $('#mAway').selectedIndex=Math.min(1,M.teams.length-1);
  const setHost=()=>{ $('#mHost').checked=M.hosts.includes($('#mHome').value); };
  $('#mHome').onchange=setHost; setHost();
  $('#mRun').onclick=runMatch;
}
async function runMatch(){
  const seed=$('#mSeed').value;
  const body={home:$('#mHome').value,away:$('#mAway').value,stage:$('#mStage').value,
    host:$('#mHost').checked, seed:seed===''?null:Number(seed)};
  await run($('#mRun'),$('#out'),'Simulating…',async()=>renderMatch(await post('/api/match',body)));
}
function renderMatch(d){
  const hw=d.winner===d.home, aw=d.winner===d.away;
  const goals=d.goals.length?d.goals.map(g=>`<div class="rowline"><span>${g.minute}' ${esc(g.scorer)}${g.assist?` <span class="subtle">(assist ${esc(g.assist)})</span>`:''}</span><span class="subtle">${esc(g.team)}</span></div>`).join(''):'<div class="subtle">no goals</div>';
  const subLines=(t,rows)=>`<div class="rowline"><span class="subtle">${esc(t)}</span><span>${rows.length?rows.map(s=>`${s.minute}' ${esc(s.off)}→${esc(s.on)}`).join(', '):'none'}</span></div>`;
  const chem=s=>`+${s.bonus} ${s.clusters.length?'('+s.clusters.map(c=>esc(c.club)+' ×'+c.count).join(', ')+')':''}`;
  const co=c=>c?`skill ${c.skill>=0?'+':''}${c.skill.toFixed(1)} · att ${c.att>=0?'+':''}${c.att.toFixed(1)} / def ${c.def>=0?'+':''}${c.def.toFixed(1)}`:'no profile';
  const cn=c=>c.value>=1?'full fitness':`${c.value} (${c.hit>=0?'+':''}${c.hit})${c.injuries?' · '+esc(c.injuries):''}`;
  $('#out').innerHTML=`
    <div class="card">
      <div class="score-big"><span class="${hw?'w':''}">${esc(d.home)}</span> ${d.home_goals}–${d.away_goals} <span class="${aw?'w':''}">${esc(d.away)}</span></div>
      <div style="text-align:center" class="subtle">${esc(d.score_str)} · stage ${esc(d.stage)} ${d.host?'· host at home':'· neutral'} · xG ${d.home_xg}–${d.away_xg}
        ${d.winner?`· winner <b class="lime">${esc(d.winner)}</b>`:''}</div>
      <div style="text-align:center;margin-top:4px" class="subtle">lineups: ${esc(d.home)} ${d.home_custom?'custom XI':'best XI'} · ${esc(d.away)} ${d.away_custom?'custom XI':'best XI'}</div>
    </div>
    <div class="cols">
      <div class="card"><h3>Goals</h3>${goals}</div>
      <div class="card"><h3>Substitutions</h3>${subLines(d.home,d.home_subs)}${subLines(d.away,d.away_subs)}
        ${d.cooling_breaks?`<div class="subtle" style="margin-top:6px">cooling breaks: ${d.cooling_breaks}</div>`:''}</div>
    </div>
    <div class="card"><h3>Match factors</h3><div class="kv">
      <span class="k">Chemistry · ${esc(d.home)}</span><span>${chem(d.chemistry.home)}</span>
      <span class="k">Chemistry · ${esc(d.away)}</span><span>${chem(d.chemistry.away)}</span>
      <span class="k">Coaching · ${esc(d.home)}</span><span>${co(d.coach.home)}</span>
      <span class="k">Coaching · ${esc(d.away)}</span><span>${co(d.coach.away)}</span>
      <span class="k">Intangibles · ${esc(d.home)}</span><span>mean ${d.intangibles.home.mean>=0?'+':''}${d.intangibles.home.mean} swing ±${d.intangibles.home.sigma}</span>
      <span class="k">Intangibles · ${esc(d.away)}</span><span>mean ${d.intangibles.away.mean>=0?'+':''}${d.intangibles.away.mean} swing ±${d.intangibles.away.sigma}</span>
      <span class="k">Condition · ${esc(d.home)}</span><span>${cn(d.condition.home)}</span>
      <span class="k">Condition · ${esc(d.away)}</span><span>${cn(d.condition.away)}</span>
    </div></div>`;
}

/* ---- 4. Match odds ---- */
function vOdds(){
  setView(head('Match odds (Monte Carlo)','Replay one fixture many times: win/draw/loss, scorers, scorelines, fair odds.')
    +`<div class="controls">
      <label>Home ${teamSelect('oHome')}</label>
      <label>Away ${teamSelect('oAway')}</label>
      <label>Stage ${stageSelect('oStage')}</label>
      <label class="chk"><input type="checkbox" id="oHost"> Host plays at home</label>
      <label>Iterations <select id="oIter"><option>200</option><option>500</option><option selected>1000</option><option>2000</option><option>5000</option></select></label>
      <button class="primary" id="oRun">Run</button></div><div id="out"></div>`);
  $('#oAway').selectedIndex=Math.min(1,M.teams.length-1);
  const setHost=()=>{ $('#oHost').checked=M.hosts.includes($('#oHome').value); };
  $('#oHome').onchange=setHost; setHost();
  $('#oRun').onclick=async()=>{ const body={home:$('#oHome').value,away:$('#oAway').value,stage:$('#oStage').value,host:$('#oHost').checked,iterations:Number($('#oIter').value)};
    await run($('#oRun'),$('#out'),`Simulating ${body.iterations} matches…`,async()=>renderOdds(await post('/api/match-odds',body))); };
}
function renderOdds(d){
  const sc=(t,rows)=>`<div class="card"><h3>${esc(t)} — top scorers</h3>${rows.length?rows.map(r=>`<div class="rowline"><span>${esc(r.name)}</span><span class="num">${r.goals.toFixed(2)} G · ${r.assists.toFixed(2)} A</span></div>`).join(''):'<div class="subtle">no goals</div>'}</div>`;
  $('#out').innerHTML=`
    <div class="card"><div class="subtle">${esc(d.home)} vs ${esc(d.away)} · stage ${esc(d.stage)} · ${d.iterations} sims · lineups ${d.home_custom?'custom':'best'}/${d.away_custom?'custom':'best'}</div>
      <table style="margin-top:8px"><tbody>${d.outcome.map(o=>`<tr><td>${esc(o.label)}</td><td class="num"><b>${pc1(o.prob)}</b></td><td style="width:40%"><div class="bar"><i style="width:${o.prob*100}%"></i></div></td></tr>`).join('')}</tbody></table>
      <div class="kv" style="margin-top:12px"><span class="k">avg goals</span><span class="num">${d.avg_goals[0]} – ${d.avg_goals[1]}</span>
        <span class="k">avg xG</span><span class="num">${d.avg_xg[0]} – ${d.avg_xg[1]}</span></div></div>
    <div class="cols">${sc(d.home,d.home_scorers)}${sc(d.away,d.away_scorers)}</div>
    <div class="cols">
      <div class="card"><h3>Most common scorelines</h3>${d.scorelines.map(s=>`<div class="rowline"><span class="num">${esc(s.score)}</span><span class="num">${pc1(s.prob)}</span></div>`).join('')}</div>
      <div class="card"><h3>Suggested bet</h3>
        <div class="chip ok" style="font-size:14px">${esc(d.suggested.selection)} — model ${pc1(d.suggested.prob)}, fair ${d.suggested.fair?d.suggested.fair.toFixed(2):'—'}</div>
        <table style="margin-top:10px"><thead><tr><th>Selection</th><th class="num">Model</th><th class="num">Fair</th><th>Market</th></tr></thead>
        <tbody>${d.markets.map(c=>`<tr><td>${esc(c.selection)}</td><td class="num">${pc1(c.prob)}</td><td class="num">${c.fair?c.fair.toFixed(2):'—'}</td><td class="subtle">${esc(c.market)}</td></tr>`).join('')}</tbody></table></div>
    </div>`;
}

/* ---- 5. Full tournament ---- */
function vTournament(){
  setView(head('Full tournament','One complete run — group tables, the knockout bracket, and the champion.')
    +`<div class="controls">
      <label>Seed <select id="tSeed"><option value="">Random</option><option value="7">7</option><option value="42">42</option></select></label>
      <label class="chk"><input type="checkbox" id="tFresh"> Ignore played results (fresh)</label>
      <button class="primary" id="tRun">Run tournament</button></div><div id="out"></div>`);
  $('#tRun').onclick=async()=>{ const body={seed:$('#tSeed').value||null,fresh:$('#tFresh').checked};
    await run($('#tRun'),$('#out'),'Simulating the tournament…',async()=>renderTournament(await post('/api/tournament',body))); };
}
function stTable(s){return `<table><thead><tr><th></th><th class="num">Pld</th><th class="num">GD</th><th class="num">Pts</th></tr></thead><tbody>${s.standings.map((r,i)=>`<tr class="${i<2?'lead':''}"><td class="gteam">${esc(r.team)}</td><td class="num">${r.played}</td><td class="num">${r.gd>=0?'+':''}${r.gd}</td><td class="num">${r.pts}</td></tr>`).join('')}</tbody></table>`;}
function renderTournament(d){
  const ko=d.knockout.map(rd=>`<div class="card"><h3>${esc(rd.stage)}</h3>${rd.matches.map(m=>`<div class="rowline"><span>${esc(m.score_str)}</span>${m.winner?`<span class="subtle">→ ${esc(m.winner)}</span>`:''}</div>`).join('')}</div>`).join('');
  $('#out').innerHTML=(d.results_seeded?`<div class="note">Seeded with ${d.results_seeded} games already played; only remaining fixtures simulated.</div>`:'')
    +`<h3 style="margin:6px 0">Group stage</h3><div class="grid">${d.groups.map(g=>`<div class="card group-card"><div class="group-top"><span class="gletter">${esc(g.letter)}</span>Group ${esc(g.letter)}</div>${stTable(g)}</div>`).join('')}</div>`
    +`<h3 style="margin:18px 0 6px">Knockout</h3>${ko}`
    +`<div class="champ">🏆 Champion: ${esc(d.champion)}</div>`;
}

/* ---- 6. Title odds ---- */
function vTitle(){
  setView(head('Title odds (Monte Carlo)','How often each team reaches each stage across many tournaments.')
    +`<div class="controls">
      <label>Iterations <select id="zIter"><option>200</option><option>500</option><option selected>1000</option><option>2000</option><option>5000</option></select></label>
      <label>Show <select id="zTop"><option>8</option><option selected>16</option><option>24</option><option>48</option></select></label>
      <label class="chk"><input type="checkbox" id="zFresh"> Fresh</label>
      <button class="primary" id="zRun">Run</button></div><div id="out"></div>`);
  $('#zRun').onclick=async()=>{ const body={iterations:Number($('#zIter').value),top:Number($('#zTop').value),fresh:$('#zFresh').checked};
    await run($('#zRun'),$('#out'),`Simulating ${body.iterations} tournaments…`,async()=>renderTitle(await post('/api/title-odds',body))); };
}
function renderTitle(d){
  const top=Math.max(...d.rows.map(r=>r.champion),0.01);
  $('#out').innerHTML=`<div class="card"><div class="subtle">${d.iterations} simulations${d.results_seeded?` · ${d.results_seeded} games played`:''}</div>
    <table style="margin-top:8px"><thead><tr><th>#</th><th>Team</th><th class="num">R16</th><th class="num">QF</th><th class="num">SF</th><th class="num">Final</th><th class="num">Champion</th><th></th></tr></thead>
    <tbody>${d.rows.map((r,i)=>`<tr class="${i===0?'lead':''}"><td class="faint num">${i+1}</td><td class="gteam">${esc(r.team)}<span class="tag">${esc(r.group)}</span></td>
      <td class="num subtle">${pc0(r.r16)}</td><td class="num subtle">${pc0(r.qf)}</td><td class="num subtle">${pc0(r.sf)}</td><td class="num subtle">${pc0(r.final)}</td>
      <td class="num lime"><b>${pc1(r.champion)}</b></td><td style="width:120px"><div class="bar"><i style="width:${r.champion/top*100}%"></i></div></td></tr>`).join('')}</tbody></table></div>`;
}

/* ---- 7. Match odds vs market (live) ---- */
function vGames(){
  setView(head('Match odds vs market','Live Polymarket fixture markets vs our model. Pick a fixture to compare, or score the played games.')
    +`<div class="controls">
      <label>Iterations <select id="gIter"><option>1000</option><option selected>2000</option><option>3000</option></select></label>
      <button class="primary" id="gLoad">Load fixtures</button>
      <button class="go" id="gAcc">Accuracy summary</button></div>
      <div id="detail"></div><div id="out"></div>`);
  $('#gLoad').onclick=loadGames;
  $('#gAcc').onclick=async()=>{ await run($('#gAcc'),$('#out'),'Scoring played games (fetching pre-kickoff odds)…',async()=>renderAccuracy(await post('/api/games/accuracy',{iterations:Number($('#gIter').value)}))); };
  loadGames();
}
async function loadGames(){
  await run($('#gLoad'),$('#out'),'Fetching fixtures from Polymarket…',async()=>{
    const d=await get('/api/games');
    const fx=(g,played)=>`<div class="rowline"><span>${played?`${esc(g.home)} <b>${esc(g.score||'')}</b> ${esc(g.away)}`:`<span class="subtle">${esc(g.date.slice(5))}</span> ${esc(g.home)} vs ${esc(g.away)}`}</span>
      <button class="mini" data-h="${esc(g.home)}" data-a="${esc(g.away)}">compare</button></div>`;
    $('#out').innerHTML=`<div class="cols">
      <div class="card"><h3>Upcoming (${d.upcoming.length})</h3>${d.upcoming.map(g=>fx(g,false)).join('')||'<div class="subtle">none</div>'}</div>
      <div class="card"><h3>Played (${d.played.length})</h3>${d.played.map(g=>fx(g,true)).join('')||'<div class="subtle">none</div>'}</div></div>`;
    $$('[data-h]').forEach(b=>b.onclick=()=>gameDetail(b.dataset.h,b.dataset.a));
  });
}
async function gameDetail(home,away){
  await run(null,$('#detail'),`Comparing ${home} vs ${away}…`,async()=>{
    const d=await post('/api/games/detail',{home,away,iterations:Number($('#gIter').value)});
    const rows=[['Home win',0],['Draw',1],['Away win',2]];
    $('#detail').innerHTML=`<div class="card"><div class="rowline"><h3>${esc(d.home)} vs ${esc(d.away)} <span class="subtle">${esc(d.date)} ${d.closed?'· pre-kickoff market':'· live market'}</span></h3>
      <button class="mini" id="dClose">close</button></div>
      ${segbar(d.model)}
      <table><thead><tr><th></th><th class="num">Market</th><th class="num lime">Model</th><th class="num">Edge</th></tr></thead>
      <tbody>${rows.map(([l,i])=>`<tr><td>${l}</td><td class="num">${d.market?pc1(d.market[i]):'—'}</td><td class="num lime">${pc1(d.model[i])}</td>
        <td class="num ${d.edges&&d.edges[i]>=0?'pos':'neg'}">${d.edges?sgn(d.edges[i]):'—'}</td></tr>`).join('')}</tbody></table>
      ${d.result?`<div class="subtle" style="margin-top:8px">actual: ${esc(d.home)} ${esc(d.score)} ${esc(d.away)} → ${({H:'home',D:'draw',A:'away'})[d.result]} ·
        model ${d.model_pick===d.result?'<span class="pos">right</span>':'<span class="neg">wrong</span>'} · market ${d.market_pick===d.result?'<span class="pos">right</span>':'<span class="neg">wrong</span>'}</div>`:''}</div>`;
    $('#dClose').onclick=()=>$('#detail').innerHTML='';
    $('#detail').scrollIntoView({behavior:'smooth',block:'nearest'});
  });
}
function renderAccuracy(d){
  if(!d.n){ $('#out').innerHTML='<div class="subtle">No played games could be graded yet.</div>'; return; }
  const sharper=d.model_brier<d.market_brier?'model':'market';
  $('#out').innerHTML=`<div class="card"><h3>Played games: model vs market</h3>
    <table><thead><tr><th>Fixture</th><th class="num">Mkt H/D/A</th><th class="num lime">Model H/D/A</th><th>Res</th></tr></thead>
    <tbody>${d.games.map(g=>`<tr><td>${esc(g.home)} ${esc(g.score||'')} ${esc(g.away)}</td>
      <td class="num">${g.market?g.market.map(pc0).join(' '):'—'}</td><td class="num lime">${g.model.map(pc0).join(' ')}</td><td>${esc(g.result||'?')}</td></tr>`).join('')}</tbody></table>
    <div class="kv" style="margin-top:12px"><span class="k">favourite correct</span><span>model ${d.model_correct}/${d.n} · market ${d.market_correct}/${d.n}</span>
      <span class="k">Brier (lower=better)</span><span>model ${d.model_brier.toFixed(3)} · market ${d.market_brier.toFixed(3)} → <b class="lime">${sharper} sharper</b></span></div></div>`;
}

/* ---- 8. Performance dashboard (live) ---- */
function vPerf(){
  setView(head('Performance dashboard','A genuine backtest over every finished game — real results vs pre-kickoff market odds.')
    +`<div class="controls"><label>Iterations <select id="pIter"><option>1000</option><option selected>2000</option><option>3000</option></select></label>
      <button class="primary" id="pRun">Run dashboard</button></div><div id="out"></div>`);
  $('#pRun').onclick=async()=>{ await run($('#pRun'),$('#out'),'Scoring finished games (fetching pre-kickoff odds)…',async()=>renderPerf(await post('/api/dashboard-live',{iterations:Number($('#pIter').value)}))); };
}
function renderPerf(d){
  if(!d.n){ $('#out').innerHTML=`<div class="subtle">${esc(d.message||'Nothing to grade yet.')}</div>`; return; }
  const sharper=d.model_sharper?'model':'market', gap=Math.abs(d.model_brier-d.market_brier);
  const h2h=d.disagreements?`<div class="card"><h3>When they backed different favourites (${d.disagreements})</h3>
    <div class="kv"><span class="k lime">model's pick won</span><span>${d.model_edge_wins}</span><span class="k blue">market's pick won</span><span>${d.market_edge_wins}</span></div></div>`:'';
  $('#out').innerHTML=`<div class="card"><div class="champ" style="color:var(--accent);font-size:22px">${sharper.toUpperCase()} is sharper by ${gap.toFixed(3)} Brier</div>
    <div class="subtle" style="text-align:center">over ${d.n} finished games · ${d.iterations} sims each · results ${d.result_mix.H}H/${d.result_mix.D}D/${d.result_mix.A}A</div>
    <table style="margin-top:12px"><thead><tr><th>Metric</th><th class="num lime">Model</th><th class="num blue">Market</th></tr></thead><tbody>
      <tr><td>favourite correct</td><td class="num lime">${d.model_correct}/${d.n}</td><td class="num blue">${d.market_correct}/${d.n}</td></tr>
      <tr><td>Brier (lower=better)</td><td class="num lime">${d.model_brier.toFixed(3)}</td><td class="num blue">${d.market_brier.toFixed(3)}</td></tr>
      <tr><td>log-loss (lower=better)</td><td class="num lime">${d.model_logloss.toFixed(3)}</td><td class="num blue">${d.market_logloss.toFixed(3)}</td></tr></tbody></table></div>
    ${h2h}
    <div class="card"><h3>Per game</h3><table><thead><tr><th>Fixture</th><th>Res</th><th>Model</th><th>Market</th><th>Verdict</th></tr></thead>
    <tbody>${d.games.map(g=>{const v=g.model_right&&g.market_right?'<span class="pos">both right</span>':g.model_right?'<span class="lime">model only</span>':g.market_right?'<span class="blue">market only</span>':'<span class="faint">both wrong</span>';
      return `<tr><td>${esc(g.home)} ${esc(g.score||'')} ${esc(g.away)}</td><td>${g.result}</td><td>${g.model_pick}</td><td>${g.market_pick||'—'}</td><td>${v}${g.disagreed?' *':''}</td></tr>`;}).join('')}</tbody></table></div>`;
}

/* ---- 9. Polymarket: compare & bet (live) ---- */
function vBets(){
  const ev=M.known_events.map(([s,l])=>`<option value="${esc(s)}">${esc(l)}</option>`).join('');
  setView(head('Polymarket: compare & bet','Compare our model to a Polymarket futures market and size half-Kelly value bets.')
    +`<div class="controls">
      <label>Market <select id="bEvent">${ev}</select></label>
      <label>Iterations <select id="bIter"><option>500</option><option selected>1000</option><option>2000</option><option>5000</option></select></label>
      <label>Bankroll <select id="bBank"><option>100</option><option>500</option><option>1000</option></select></label>
      <button class="primary" id="bRun">Run</button></div><div id="out"></div>`);
  $('#bRun').onclick=async()=>{ const body={slug:$('#bEvent').value,iterations:Number($('#bIter').value),bankroll:Number($('#bBank').value)};
    await run($('#bRun'),$('#out'),'Fetching market and simulating…',async()=>renderBets(await post('/api/bets',body))); };
}
function renderBets(d){
  const comps=d.comparisons.filter(c=>c.market>0).slice(0,20);
  const bets=d.bets.length?`<div class="card"><h3>Value bets — half-Kelly, $${d.bankroll} bankroll, $${d.total_staked.toFixed(2)} staked</h3>
    ${d.bets.map(b=>`<div class="rowline"><span><b class="lime">${esc(b.team)}</b> <span class="subtle">@ ${b.price.toFixed(3)} · ${b.shares.toFixed(1)} shares</span></span>
      <span class="num">${money(b.stake)} · edge ${sgn(b.edge)} · EV ${(b.ev*100).toFixed(0)}%</span></div>`).join('')}
    <div class="kv" style="margin-top:10px"><span class="k">mean P&L</span><span class="${d.mean_pnl>=0?'pos':'neg'}">${money(d.mean_pnl)} · ROI ${sgn(d.roi)} · green ${pc0(d.win_rate)} of runs</span>
      <span class="k">P&L range</span><span class="num">p5 ${money(d.p5)} · median ${money(d.p50)} · p95 ${money(d.p95)}</span></div>
    <div class="subtle" style="margin-top:8px">Bets settle on the same model that prices them — this is edge variance, not a backtest.</div></div>`
    :'<div class="card subtle">No positive-edge bets clear the threshold.</div>';
  $('#out').innerHTML=`<div class="card"><h3>${esc(d.title)} <span class="subtle">· ${d.iterations} sims</span></h3>
    <table><thead><tr><th>Team</th><th class="num">Market</th><th class="num lime">Model</th><th class="num">Edge</th><th class="num">EV/$1</th></tr></thead>
    <tbody>${comps.map(c=>`<tr><td class="gteam">${esc(c.team)}</td><td class="num">${pc1(c.market)}</td><td class="num lime">${pc1(c.model)}</td>
      <td class="num ${c.edge>=0?'pos':'neg'}">${sgn(c.edge)}</td><td class="num ${c.ev>=0?'pos':'neg'}">${sgn(c.ev)}</td></tr>`).join('')}</tbody></table></div>${bets}`;
}

/* ---- 10. Model-vs-market dashboard (offline snapshot) ---- */
function vDashboard(){
  setView(head('Model-vs-market dashboard','The offline scoreboard (market snapshot). Build it here, or open the standalone HTML.')
    +`<div class="controls">
      <label>Title sims <select id="rTitle"><option>500</option><option selected>1500</option><option>3000</option></select></label>
      <label>Game sims <select id="rGame"><option>400</option><option selected>800</option><option>1500</option></select></label>
      <button class="primary" id="rRun">Build</button>
      <a class="go" id="rOpen" style="text-decoration:none" target="_blank">Open standalone HTML ↗</a></div><div id="out"></div>`);
  const link=()=>$('#rOpen').href=`/dashboard.html?title_iters=${$('#rTitle').value}&game_iters=${$('#rGame').value}`;
  $('#rTitle').onchange=link; $('#rGame').onchange=link; link();
  $('#rRun').onclick=async()=>{ await run($('#rRun'),$('#out'),'Building dashboard (Monte Carlo + grading)…',async()=>renderReport(await get(`/api/report?title_iters=${$('#rTitle').value}&game_iters=${$('#rGame').value}`))); };
}
function renderReport(r){
  const m=r.meta, pick=r.champion_pick, perf=r.performance;
  const hero=`<div class="card"><div class="cols"><div>
      <div class="subtle">${esc(m.tournament)} · ${esc(m.generated)} · ${m.odds_fetched?('snapshot '+esc(m.odds_fetched)):'model only'}</div>
      <div class="kv" style="margin-top:8px"><span class="k">games played</span><span>${m.games_played}/${m.games_total}</span>
        <span class="k">model calls right</span><span>${perf.n?`${perf.model_correct}/${perf.n}`:'—'}</span>
        <span class="k">sharper so far</span><span>${perf.n?(perf.model_sharper?'MODEL':'MARKET'):'—'}</span></div></div>
      ${pick?`<div style="text-align:right"><div class="subtle">Model title favourite</div><div class="champ" style="margin:4px 0">${esc(pick.team)}</div>
        <div class="num">model <b class="lime">${pc1(pick.champion)}</b>${pick.market!=null?` · market <b class="blue">${pc1(pick.market)}</b>`:''}</div></div>`:''}</div></div>`;
  const titleTbl=`<div class="card"><h3>Title race</h3><table><thead><tr><th>#</th><th>Team</th><th class="num">R16</th><th class="num">QF</th><th class="num">SF</th><th class="num">Final</th><th class="num">Champion</th>${m.has_market?'<th class="num">Market</th><th class="num">Edge</th>':''}</tr></thead>
    <tbody>${r.title.map((t,i)=>`<tr class="${i===0?'lead':''}"><td class="faint num">${i+1}</td><td class="gteam">${esc(t.team)}<span class="tag">${esc(t.group)}</span></td>
      <td class="num subtle">${pc0(t.r16)}</td><td class="num subtle">${pc0(t.qf)}</td><td class="num subtle">${pc0(t.sf)}</td><td class="num subtle">${pc0(t.final)}</td>
      <td class="num lime"><b>${pc1(t.champion)}</b></td>${m.has_market?`<td class="num blue">${pc1(t.market)}</td><td class="num ${t.edge>=0?'pos':'neg'}">${sgn(t.edge)}</td>`:''}</tr>`).join('')}</tbody></table></div>`;
  const groups=`<h3 style="margin:14px 0 6px">Group standings</h3><div class="grid">${r.groups.map(g=>`<div class="card group-card"><div class="group-top"><span class="gletter">${esc(g.letter)}</span>Group ${esc(g.letter)}</div>
    <table><tbody>${g.rows.map((row,i)=>`<tr class="${i<2?'lead':''}"><td class="gteam">${esc(row.team)}</td><td class="num">${row.pld}</td><td class="num">${row.gd>=0?'+':''}${row.gd}</td><td class="num">${row.pts}</td></tr>`).join('')}</tbody></table></div>`).join('')}</div>`;
  const value=r.value_bets&&r.value_bets.bets.length?`<div class="card"><h3>Value bets — $${r.value_bets.bankroll} bankroll</h3>${r.value_bets.bets.map(b=>`<div class="rowline"><span><b class="lime">${esc(b.team)}</b> <span class="subtle">@ ${b.price.toFixed(3)}</span></span><span class="num">${money(b.stake)} · edge ${sgn(b.edge)}</span></div>`).join('')}</div>`:'';
  $('#out').innerHTML=hero+titleTbl+groups+value;
}

/* ---- 11. Update data (live) ---- */
function vUpdate(){
  const snap=M.has_odds_snapshot?('fetched '+esc(M.odds_fetched)):'<span class="neg">none yet</span>';
  const ssnap=M.has_sofa_snapshot?(`${M.sofa_games} games · ${esc(M.sofa_fetched)}`):'<span class="neg">none yet</span>';
  setView(head('Update data (live)','Pull the latest played scores — and optionally market odds and SofaScore form — into the offline snapshots that feed every screen.')
    +`<div class="card"><div class="kv">
        <span class="k">Results snapshot</span><span><b>${M.results_played}</b> games played</span>
        <span class="k">Odds snapshot</span><span>${snap}</span>
        <span class="k">SofaScore snapshot</span><span>${ssnap}</span>
      </div></div>
      <div class="controls">
        <label class="chk"><input type="checkbox" id="uOdds"> Also refresh market odds <span class="subtle">(slower — recovers pre-kickoff prices per played game)</span></label>
        <label class="chk"><input type="checkbox" id="uSofa"> Also refresh SofaScore ratings <span class="subtle">(in-tournament player form that boosts/penalises future games)</span></label>
        <button class="primary" id="uRun">Refresh from live data</button>
      </div>
      <div class="subtle">Writes <span class="mono">data/results_2026.json</span> (and <span class="mono">data/odds_2026.json</span> / <span class="mono">data/sofascore_2026.json</span> when ticked). Network required.</div>
      <div id="out" style="margin-top:14px"></div>`);
  $('#uRun').onclick=async()=>{
    const body={odds:$('#uOdds').checked,sofa:$('#uSofa').checked};
    const extra=body.odds||body.sofa;
    const msg=extra?'Fetching live data (this can take a minute)…':'Fetching played results…';
    await run($('#uRun'),$('#out'),msg,async()=>{
      const d=await post('/api/refresh',body);
      if(d.meta) M=d.meta;            // refresh cached snapshot counts everywhere
      renderUpdate(d);
    });
  };
}
function renderUpdate(d){
  const r=d.results, o=d.odds, s=d.sofa;
  const rcard=`<div class="card"><h3>Results updated <span class="pos">✓</span></h3><div class="kv">
      <span class="k">played fixtures</span><span><b>${r.total}</b> (${r.group} group · ${r.knockout} knockout)</span>
      <span class="k">fetched</span><span>${esc(r.fetched)}</span>
      ${r.skipped&&r.skipped.length?`<span class="k">skipped</span><span class="subtle">${r.skipped.length} unmapped/unscored</span>`:''}
    </div></div>`;
  const ocard=o?`<div class="card"><h3>Market odds updated <span class="pos">✓</span></h3><div class="kv">
      <span class="k">winner lines</span><span>${o.winner}</span>
      <span class="k">fixtures</span><span>${o.games} <span class="subtle">(${o.pregame} with pre-kickoff history)</span></span>
      <span class="k">fetched</span><span>${esc(o.fetched)}</span>
    </div></div>`:'<div class="card subtle">Market odds snapshot left unchanged (tick the box above to refresh it too).</div>';
  let scard='<div class="card subtle">SofaScore snapshot left unchanged (tick the box above to refresh it too).</div>';
  if(s&&s.error){scard=`<div class="card"><h3>SofaScore <span class="neg">✗</span></h3><div class="subtle">${esc(s.error)}</div><div class="subtle">Existing snapshot left unchanged.</div></div>`;}
  else if(s){scard=`<div class="card"><h3>SofaScore ratings updated <span class="pos">✓</span></h3><div class="kv">
      <span class="k">games rated</span><span><b>${s.games}</b></span>
      <span class="k">teams rated</span><span>${s.teams_rated}</span>
      <span class="k">fetched</span><span>${esc(s.fetched)}</span>
    </div></div>`;}
  $('#out').innerHTML=rcard+ocard+scard+`<div class="note">Snapshots written to data/ — every other screen now reflects the new data.</div>`;
}

/* ---- bootstrap ---- */
function buildNav(){
  $('#nav').innerHTML=NAV.map(([id,label],i)=>`<button class="navbtn" data-id="${id}"><span class="n">${i+1}</span>${esc(label)}</button>`).join('');
  $$('.navbtn').forEach(b=>b.onclick=()=>{location.hash=b.dataset.id;});
}
function route(){
  const id=(location.hash||'#teams').slice(1);
  const item=NAV.find(n=>n[0]===id)||NAV[0];
  $$('.navbtn').forEach(b=>b.classList.toggle('active',b.dataset.id===item[0]));
  item[2]();
}
window.addEventListener('hashchange',route);
(async function(){
  try{ M=await get('/api/meta'); }catch(e){ $('#view').innerHTML=`<div class="error">Could not load: ${esc(e.message)}</div>`; return; }
  $('#brandSub').textContent=M.tournament.replace(/FIFA |World Cup /g,'')||'Simulator';
  buildNav(); route();
})();
</script>
</body>
</html>"""


if __name__ == "__main__":
    serve()
