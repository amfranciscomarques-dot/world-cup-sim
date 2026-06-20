"""Read the offline SofaScore ratings snapshot (``data/sofascore_2026.json``).

Mirrors :mod:`worldcup.odds_store`. The snapshot holds, per played fixture, each
side's average SofaScore match rating (and, optionally, the per-player ratings
behind it — kept for a future per-player granularity). :func:`form_extras`
collapses it into the per-team ``extras`` the simulation consumes, so the engine
reads in-tournament player form with no network round-trips.

Produced/refreshed by ``scripts/update_sofascore.py`` or the web UI's "Update
data" action (both call :func:`refresh_sofascore`). A missing file is not an
error — it simply means "no SofaScore form signal".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .data_loader import DATA_DIR

DEFAULT_SOFASCORE = DATA_DIR / "sofascore_2026.json"


@dataclass
class PlayerRating:
    name: str
    rating: float


@dataclass
class SofaGame:
    """One played fixture's SofaScore team ratings, as snapshotted."""

    date: str
    home: str
    away: str
    home_rating: Optional[float]
    away_rating: Optional[float]
    score: Optional[str] = None
    home_players: list[PlayerRating] = field(default_factory=list)
    away_players: list[PlayerRating] = field(default_factory=list)


@dataclass
class SofaSnapshot:
    fetched: str
    games: list[SofaGame] = field(default_factory=list)

    def game(self, home: str, away: str) -> Optional[SofaGame]:
        for g in self.games:
            if {g.home, g.away} == {home, away}:
                return g
        return None


def team_ratings(snapshot: SofaSnapshot) -> dict[str, float]:
    """Average each team's SofaScore match ratings over the games it has played.

    A team rated in multiple games is the mean of those per-match ratings,
    regardless of whether it played home or away. Games where a side has no
    rating are skipped. Returns ``{team_name: average_rating}``.
    """
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for g in snapshot.games:
        for team, rating in ((g.home, g.home_rating), (g.away, g.away_rating)):
            if not team or rating is None:
                continue
            totals[team] = totals.get(team, 0.0) + float(rating)
            counts[team] = counts.get(team, 0) + 1
    return {team: round(totals[team] / counts[team], 3) for team in totals}


def form_extras(snapshot: Optional[SofaSnapshot]) -> dict[str, dict]:
    """Per-team ``extras`` for the simulation: ``{team: {"sofa_rating": avg}}``.

    This is what gets handed to the simulators (which apply it to every *future*
    match a team plays). An empty dict — no snapshot, or no rated games — means
    the :class:`~worldcup.factors.builtin.SofaScoreFactor` no-ops everywhere.
    """
    if snapshot is None:
        return {}
    return {team: {"sofa_rating": avg} for team, avg in team_ratings(snapshot).items()}


def load_sofascore(path: Path | str = DEFAULT_SOFASCORE) -> Optional[SofaSnapshot]:
    """Load the SofaScore snapshot, or ``None`` if no file exists yet."""
    p = Path(path)
    if not p.exists():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))

    def players(rows) -> list[PlayerRating]:
        out = []
        for r in rows or []:
            try:
                out.append(PlayerRating(name=r["name"], rating=float(r["rating"])))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def num(v) -> Optional[float]:
        return float(v) if v is not None else None

    games = [
        SofaGame(
            date=g.get("date", ""), home=g["home"], away=g["away"],
            home_rating=num(g.get("home_rating")), away_rating=num(g.get("away_rating")),
            score=g.get("score"),
            home_players=players(g.get("home_players")),
            away_players=players(g.get("away_players")),
        )
        for g in raw.get("games", [])
    ]
    return SofaSnapshot(fetched=raw.get("_fetched", ""), games=games)


def load_form_extras(path: Path | str = DEFAULT_SOFASCORE) -> dict[str, dict]:
    """Convenience: load the snapshot and collapse it to per-team form extras
    in one call. ``{}`` when there is no snapshot."""
    return form_extras(load_sofascore(path))


def refresh_sofascore(
    teams: Optional[dict] = None,
    tournament=None,
    *,
    path: Path | str = DEFAULT_SOFASCORE,
    log=None,
) -> dict:
    """Fetch SofaScore ratings for played fixtures and (re)write the snapshot.

    The importable core of ``scripts/update_sofascore.py`` — also called by the
    web UI's "Update data" action. Requires network (read-only). Because the
    public API is fragile, this is protective: it raises
    :class:`worldcup.sofascore.SofaScoreError` (and leaves any existing snapshot
    untouched) rather than overwriting good data with an empty fetch.
    """
    from datetime import date

    from . import sofascore  # lazy: network code only needed when refreshing
    from .data_loader import load_world_cup

    def say(msg: str) -> None:
        if log:
            log(msg)

    if teams is None or tournament is None:
        teams, tournament = load_world_cup()

    say("fetching SofaScore ratings for played World Cup fixtures...")
    matches = sofascore.fetch_finished_matches(teams, log=say)

    rows: list[dict] = []
    skipped: list[str] = []
    for m in matches:
        if not m.mapped or (m.home_rating is None and m.away_rating is None):
            skipped.append(f"{m.home or '?'} vs {m.away or '?'}")
            continue
        rows.append({
            "date": m.date, "home": m.home, "away": m.away,
            "home_rating": m.home_rating, "away_rating": m.away_rating,
            "score": m.score,
            "home_players": [{"name": p.name, "rating": p.rating} for p in m.home_players],
            "away_players": [{"name": p.name, "rating": p.rating} for p in m.away_players],
        })

    if not rows:
        raise sofascore.SofaScoreError(
            "fetched no usable SofaScore ratings; leaving the snapshot unchanged"
        )

    rows.sort(key=lambda r: (r["date"], r["home"]))
    fetched = date.today().isoformat()
    payload = {
        "_about": (
            "SofaScore match ratings for played 2026 World Cup fixtures: each "
            "side's average player rating (and the per-player ratings behind it). "
            "Read offline by SofaScoreFactor as an in-tournament form signal. "
            "Regenerate with scripts/update_sofascore.py."
        ),
        "_fetched": fetched,
        "games": rows,
    }
    out = Path(path)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rated_teams = len(team_ratings(SofaSnapshot(fetched=fetched, games=[
        SofaGame(date=r["date"], home=r["home"], away=r["away"],
                 home_rating=r["home_rating"], away_rating=r["away_rating"])
        for r in rows
    ])))
    say(f"wrote {len(rows)} games ({rated_teams} teams rated) to {out.name}")
    return {
        "path": str(out), "fetched": fetched, "games": len(rows),
        "teams_rated": rated_teams, "skipped": skipped,
    }
