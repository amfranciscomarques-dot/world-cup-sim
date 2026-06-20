"""Assemble the full dashboard payload — one serialisable dict that drives both
the HTML dashboard and any other front end.

This is the single place that joins the three data sources together:

  * the **model** (Monte Carlo tournament + per-fixture simulations),
  * the **results** so far (``data/results_2026.json``), and
  * the **market** (the offline Polymarket odds snapshot, ``data/odds_2026.json``).

Keeping it framework-agnostic (plain dict in, plain dict out) means the renderer
stays dumb and the numbers stay testable.
"""

from __future__ import annotations

import itertools
import math
import random
from datetime import datetime, timezone
from typing import Callable, Optional

from .betting import Comparison, select_bets
from .data_loader import TournamentDef
from .engine import MatchSimulator
from .games import OUTCOMES, model_triple
from .models import PlayedResult, Team
from .odds_store import GameOdds, OddsSnapshot
from .tournament import TournamentSimulator, standings_from_results

Progress = Callable[[str, int, int], None]

# Total fixtures in a 48-team, 12-group World Cup group stage (12 * 6) plus the
# 32-team knockout (16 + 8 + 4 + 2 + 1 + the third-place play-off is not used).
GROUP_FIXTURES = 72


def _devig(triple: Optional[tuple]) -> Optional[tuple]:
    if triple is None or None in triple:
        return None
    total = sum(triple)
    return tuple(x / total for x in triple) if total > 0 else None


def _pick(triple: tuple) -> str:
    return OUTCOMES[max(range(3), key=lambda i: triple[i])]


def _brier(probs: tuple, result_idx: int) -> float:
    return sum((probs[i] - (1.0 if i == result_idx else 0.0)) ** 2 for i in range(3))


def _logloss(probs: tuple, result_idx: int) -> float:
    return -math.log(max(probs[result_idx], 1e-9))


def _fixtures(
    odds: Optional[OddsSnapshot],
    tournament: TournamentDef,
    results: list[PlayedResult],
) -> tuple[list[GameOdds], list[GameOdds]]:
    """Return (played, upcoming) fixtures as :class:`GameOdds`.

    Prefer the odds snapshot (it carries dates + market prices); otherwise
    synthesise from the played results and the group draw, so the dashboard is
    still useful with no market data — played games from results, upcoming games
    from the as-yet-unplayed group pairings (no market column)."""
    if odds:
        played = [g for g in odds.games if g.closed and g.score]
        upcoming = [g for g in odds.games if not g.closed]
        return played, upcoming

    played = [
        GameOdds(date=r.date, home=r.home, away=r.away, closed=True,
                 score=f"{r.home_goals}-{r.away_goals}", live=None, pregame=None)
        for r in results if r.stage == "group"
    ]
    seen = {r.pair for r in results}
    upcoming = []
    for names in tournament.groups.values():
        for home, away in itertools.combinations(names, 2):
            if frozenset((home, away)) not in seen:
                upcoming.append(GameOdds(date="", home=home, away=away, closed=False,
                                         score=None, live=None, pregame=None))
    return played, upcoming


def _current_standings(tournament: TournamentDef, results: list[PlayedResult],
                       teams: dict[str, Team], rng: random.Random) -> list[dict]:
    """Real group tables from played results, with a provisional qualification
    zone (top two = auto, third = best-third contention, fourth = out)."""
    zones = ["auto", "auto", "third", "out"]
    groups: list[dict] = []
    for letter, names in tournament.groups.items():
        gr = standings_from_results(letter, names, results, rng)
        rows = []
        for i, s in enumerate(gr.standings):
            rows.append({
                "team": s.team, "rating": round(teams[s.team].rating, 1),
                "pld": s.played, "w": s.won, "d": s.drawn, "l": s.lost,
                "gf": s.gf, "ga": s.ga, "gd": s.gd, "pts": s.pts,
                "zone": zones[i] if i < len(zones) else "out",
            })
        groups.append({"letter": letter, "rows": rows})
    return groups


def _title_odds(report, teams, market_winner: dict[str, float], top: int) -> list[dict]:
    ranked = report.title_odds()
    rows = []
    for name, champ in ranked[:top]:
        r = report.reach[name]
        mk = market_winner.get(name)
        rows.append({
            "team": name, "group": teams[name].group, "rating": round(teams[name].rating, 1),
            "r16": r["R16"], "qf": r["QF"], "sf": r["SF"], "final": r["Final"],
            "champion": champ,
            "market": mk,
            "edge": (champ - mk) if mk is not None else None,
        })
    return rows


def _value_bets(report, market_winner: dict[str, float], bankroll: float) -> dict:
    """Reuse the title-odds Monte Carlo to compare our champion probabilities to
    the winner market and size positive-edge value bets (half-Kelly)."""
    champ = {name: report.reach[name]["Champion"] for name in report.reach}
    comparisons = [
        Comparison(team=t, market_prob=p, model_prob=champ.get(t, 0.0))
        for t, p in market_winner.items()
    ]
    comparisons.sort(key=lambda c: c.edge, reverse=True)
    bets = select_bets(comparisons, bankroll, kelly_mult=0.5, min_edge=0.02)
    return {
        "bankroll": bankroll,
        "comparisons": [
            {"team": c.team, "market": c.market_prob, "model": c.model_prob,
             "edge": c.edge, "ev": c.ev}
            for c in comparisons
        ],
        "bets": [
            {"team": b.team, "stake": b.stake, "price": b.price,
             "shares": b.shares, "edge": b.edge, "ev": b.ev}
            for b in bets
        ],
        "total_staked": sum(b.stake for b in bets),
    }


def build_report(
    teams: dict[str, Team],
    tournament: TournamentDef,
    results: list[PlayedResult],
    odds: Optional[OddsSnapshot],
    *,
    title_iters: int = 2000,
    game_iters: int = 1500,
    max_upcoming: int = 12,
    top_title: int = 16,
    bankroll: float = 100.0,
    seed: Optional[int] = None,
    sofa_extras: Optional[dict[str, dict]] = None,
    progress: Optional[Progress] = None,
) -> dict:
    """Build the complete dashboard payload. ``odds`` may be ``None`` (model-only:
    no market columns). ``progress(stage, done, total)`` reports long phases.

    ``sofa_extras`` (per-team SofaScore form from ``sofascore_store.form_extras``)
    is applied to the title Monte Carlo and the upcoming-fixture odds — the
    *future* games — but never to the played-game grid, which is graded against
    its pre-kickoff market."""
    hosts = set(tournament.host_team_names)
    market_winner = dict(odds.winner) if odds else {}

    def note(stage: str, done: int, total: int) -> None:
        if progress:
            progress(stage, done, total)

    # --- title race ---------------------------------------------------------
    note("title odds", 0, title_iters)
    tsim = TournamentSimulator(teams, tournament, rng=random.Random(seed), results=results)
    report = tsim.monte_carlo(title_iters, extras=sofa_extras)
    note("title odds", title_iters, title_iters)
    title = _title_odds(report, teams, market_winner, top_title)

    # --- per-fixture model vs market ---------------------------------------
    gsim = MatchSimulator(rng=random.Random(seed))
    played_games, all_upcoming = _fixtures(odds, tournament, results)
    upcoming_games = sorted(all_upcoming, key=lambda x: (x.date, x.home))[:max_upcoming]
    total_fx = len(played_games) + len(upcoming_games)
    done = 0

    results_rows: list[dict] = []
    perf = {"n": 0, "model_correct": 0, "market_correct": 0,
            "model_brier": 0.0, "market_brier": 0.0,
            "model_logloss": 0.0, "market_logloss": 0.0,
            "mix": {"H": 0, "D": 0, "A": 0},
            "disagreements": 0, "model_edge_wins": 0, "market_edge_wins": 0}

    for g in sorted(played_games, key=lambda x: (x.date, x.home), reverse=True):
        model = model_triple(gsim, teams, g.home, g.away, game_iters, hosts)
        market = g.market_triple()
        res = g.result
        row = {
            "date": g.date, "home": g.home, "away": g.away, "score": g.score,
            "result": res, "model": list(model),
            "market": list(market) if market else None,
        }
        if res and market:
            ri = OUTCOMES.index(res)
            mp, kp = _pick(model), _pick(market)
            row["model_pick"], row["market_pick"] = mp, kp
            row["model_right"], row["market_right"] = mp == res, kp == res
            perf["n"] += 1
            perf["mix"][res] += 1
            perf["model_correct"] += mp == res
            perf["market_correct"] += kp == res
            perf["model_brier"] += _brier(model, ri)
            perf["market_brier"] += _brier(market, ri)
            perf["model_logloss"] += _logloss(model, ri)
            perf["market_logloss"] += _logloss(market, ri)
            if mp != kp:
                perf["disagreements"] += 1
                perf["model_edge_wins"] += mp == res
                perf["market_edge_wins"] += kp == res
        results_rows.append(row)
        done += 1
        note("grading games", done, total_fx)

    upcoming_rows: list[dict] = []
    for g in upcoming_games:
        model = model_triple(gsim, teams, g.home, g.away, game_iters, hosts, sofa_extras)
        market = _devig(g.live)
        edge = None
        if market:
            i = max(range(3), key=lambda j: model[j] - market[j])
            edge = {"side": OUTCOMES[i], "value": model[i] - market[i]}
        upcoming_rows.append({
            "date": g.date, "home": g.home, "away": g.away,
            "model": list(model), "market": list(market) if market else None,
            "edge": edge,
        })
        done += 1
        note("grading games", done, total_fx)

    n = perf["n"]
    performance = {
        "n": n,
        "model_correct": perf["model_correct"], "market_correct": perf["market_correct"],
        "model_brier": perf["model_brier"] / n if n else 0.0,
        "market_brier": perf["market_brier"] / n if n else 0.0,
        "model_logloss": perf["model_logloss"] / n if n else 0.0,
        "market_logloss": perf["market_logloss"] / n if n else 0.0,
        "model_sharper": (perf["model_brier"] < perf["market_brier"]) if n else False,
        "mix": perf["mix"],
        "disagreements": perf["disagreements"],
        "model_edge_wins": perf["model_edge_wins"],
        "market_edge_wins": perf["market_edge_wins"],
    }

    value = _value_bets(report, market_winner, bankroll) if market_winner else None

    played_count = len([r for r in results if r.stage == "group"])
    return {
        "meta": {
            "tournament": tournament.name,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "title_iters": title_iters, "game_iters": game_iters,
            "games_played": played_count, "games_total": GROUP_FIXTURES,
            "odds_fetched": odds.fetched if odds else None,
            "has_market": bool(market_winner),
        },
        "champion_pick": title[0] if title else None,
        "title": title,
        "groups": _current_standings(tournament, results, teams, random.Random(seed)),
        "results": results_rows,
        "upcoming": upcoming_rows,
        "performance": performance,
        "value_bets": value,
    }
