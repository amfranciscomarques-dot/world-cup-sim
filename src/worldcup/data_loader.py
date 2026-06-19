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
        )
        for r in raw.get("results", [])
    ]


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
