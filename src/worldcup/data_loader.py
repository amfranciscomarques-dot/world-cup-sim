"""Load teams and tournament definitions from the bundled JSON data files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .fm_rating import rating_from_attributes
from .models import Player, PlayedResult, Team

# data/ lives at the repo root, two levels up from this file (src/worldcup/).
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DEFAULT_TEAMS = DATA_DIR / "teams_2026.json"
DEFAULT_TOURNAMENT = DATA_DIR / "tournament_2026.json"
DEFAULT_RESULTS = DATA_DIR / "results_2026.json"


@dataclass
class TournamentDef:
    """Static tournament definition: format rules + the group draw."""

    name: str
    groups: dict[str, list[str]]          # group letter -> ordered team names
    num_groups: int
    teams_per_group: int
    advance_top_n_per_group: int
    best_third_placed_advance: int
    knockout_rounds: list[str]
    host_team_names: list[str]

    @property
    def team_names(self) -> list[str]:
        return [name for teams in self.groups.values() for name in teams]


def load_teams(path: Path | str = DEFAULT_TEAMS) -> dict[str, Team]:
    """Return a ``{team_name: Team}`` map. Group is attached later by the loader."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    teams: dict[str, Team] = {}
    for entry in raw["teams"]:
        squad = []
        for p in entry.get("players", []):
            attrs = p.get("attributes") or {}
            # Prefer FM attributes when present; fall back to the curated rating.
            rating = rating_from_attributes(p["pos"], attrs) if attrs else float(p["rating"])
            squad.append(Player(name=p["name"], pos=p["pos"], rating=rating,
                                attributes=attrs, club=p.get("club", ""),
                                age=int(p.get("age", 0) or 0), xfactor=p.get("xfactor") or {}))
        teams[entry["name"]] = Team(name=entry["name"], rating=float(entry["rating"]),
                                    squad=squad, coach=entry.get("coach") or {},
                                    condition=float(entry.get("condition", 1.0)),
                                    injuries=entry.get("_injuries", ""))
    return teams


def load_tournament(path: Path | str = DEFAULT_TOURNAMENT) -> TournamentDef:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    fmt = raw["format"]
    return TournamentDef(
        name=raw["name"],
        groups=raw["groups"],
        num_groups=fmt["num_groups"],
        teams_per_group=fmt["teams_per_group"],
        advance_top_n_per_group=fmt["advance_top_n_per_group"],
        best_third_placed_advance=fmt["best_third_placed_advance"],
        knockout_rounds=fmt["knockout_rounds"],
        host_team_names=raw.get("host_team_names", []),
    )


def load_results(path: Path | str = DEFAULT_RESULTS) -> list[PlayedResult]:
    """Load already-played fixtures, or ``[]`` if no snapshot file exists yet.

    The file is produced by ``scripts/update_results.py`` from Polymarket. A
    missing file is not an error — it just means "simulate everything".
    """
    p = Path(path)
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    return [
        PlayedResult(
            home=r["home"], away=r["away"],
            home_goals=int(r["home_goals"]), away_goals=int(r["away_goals"]),
            stage=r.get("stage", "group"), date=r.get("date", ""),
            home_pens=int(r.get("home_pens", 0) or 0),
            away_pens=int(r.get("away_pens", 0) or 0),
        )
        for r in raw.get("results", [])
    ]


def refresh_results(
    teams: Optional[dict] = None,
    tournament: "Optional[TournamentDef]" = None,
    *,
    path: Path | str = DEFAULT_RESULTS,
    log=None,
) -> dict:
    """Fetch played fixtures from Polymarket and (re)write the results snapshot.

    This is the importable core of ``scripts/update_results.py`` — also called by
    the web UI's "Update data" action — so the offline ``results_2026.json`` can
    be refreshed from the live feed without shelling out. Requires network
    (read-only, no key). Returns a summary dict; raises
    :class:`worldcup.polymarket.PolymarketError` on a fetch failure.
    """
    from datetime import date

    from . import polymarket  # lazy: network code only needed when refreshing

    def say(msg: str) -> None:
        if log:
            log(msg)

    if teams is None or tournament is None:
        teams, tournament = load_world_cup()
    group_of = {name: letter for letter, names in tournament.groups.items() for name in names}

    say("fetching played World Cup fixtures from Polymarket...")
    games = polymarket.fetch_games(teams, closed=True)

    rows: list[dict] = []
    skipped: list[str] = []
    for g in games:
        # Need both sides mapped to our squads and a clean "H-A" score.
        score = g.score or ""
        parts = score.split("-")
        if not g.mapped or g.home is None or g.away is None or len(parts) != 2:
            skipped.append(g.title or g.slug)
            continue
        try:
            h, a = int(parts[0]), int(parts[1])
        except ValueError:
            skipped.append(g.title or g.slug)
            continue
        home, away = g.home, g.away
        same_group = group_of.get(home) is not None and group_of.get(home) == group_of.get(away)
        rows.append({
            "date": g.date,
            "stage": "group" if same_group else "knockout",
            "home": home,
            "away": away,
            "home_goals": h,
            "away_goals": a,
        })

    rows.sort(key=lambda r: (r["date"], r["home"]))
    fetched = date.today().isoformat()
    payload = {
        "_about": (
            "Played 2026 World Cup results, fetched from Polymarket per-game markets. "
            "Read by the tournament simulator to seed real group standings and only "
            "simulate the remaining fixtures. Regenerate with scripts/update_results.py."
        ),
        "_fetched": fetched,
        "results": rows,
    }
    out = Path(path)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    groups = sum(1 for r in rows if r["stage"] == "group")
    say(f"wrote {len(rows)} played results ({groups} group, {len(rows) - groups} knockout) to {out.name}")
    return {
        "path": str(out), "fetched": fetched, "total": len(rows),
        "group": groups, "knockout": len(rows) - groups, "skipped": skipped,
    }


def load_world_cup(
    teams_path: Path | str = DEFAULT_TEAMS,
    tournament_path: Path | str = DEFAULT_TOURNAMENT,
) -> tuple[dict[str, Team], TournamentDef]:
    """Load both files and cross-link: every team gets its ``group`` set, and we
    verify the draw references only teams that exist in the squad data."""
    teams = load_teams(teams_path)
    tournament = load_tournament(tournament_path)

    missing: list[str] = []
    for letter, names in tournament.groups.items():
        for name in names:
            team = teams.get(name)
            if team is None:
                missing.append(f"{name} (group {letter})")
            else:
                team.group = letter
    if missing:
        raise ValueError(
            "Tournament draw references teams with no squad data: " + ", ".join(missing)
        )
    return teams, tournament
