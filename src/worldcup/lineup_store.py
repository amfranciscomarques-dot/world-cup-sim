"""Persisted custom starting XIs.

The point: lock in a team's real starting eleven (e.g. once official lineups are
announced) and have *every* simulation screen pick it up, instead of always
falling back to the curated best XI. Stored as ``{team_name: [player_name, ...]}``
in ``data/lineups_2026.json``.

Player names are resolved against each team's squad at load time and unknown
names are silently skipped (squad data can change), so the file is always
advisory — a stale or hand-edited entry can never crash a run.
"""

from __future__ import annotations

import json
from typing import Optional

from .data_loader import DATA_DIR
from .models import Lineup, Team

LINEUPS_PATH = DATA_DIR / "lineups_2026.json"


def load_saved() -> dict[str, list[str]]:
    """Return the raw ``{team: [player names]}`` map, or ``{}`` if none/broken."""
    if not LINEUPS_PATH.exists():
        return {}
    try:
        data = json.loads(LINEUPS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {str(k): list(v) for k, v in data.items() if isinstance(v, list)}


def save_saved(saved: dict[str, list[str]]) -> None:
    """Persist the ``{team: [player names]}`` map to disk."""
    LINEUPS_PATH.write_text(
        json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def resolve_lineup(team: Team, names: list[str]) -> Optional[Lineup]:
    """Build a :class:`Lineup` for ``team`` from saved player names.

    Unknown names are dropped. Returns ``None`` if nothing resolves, which the
    engine treats as "use the team's best XI".
    """
    by_name = {p.name: p for p in team.squad}
    players = [by_name[n] for n in names if n in by_name]
    return Lineup(team=team.name, players=players) if players else None


def lineup_for(team: Team, saved: Optional[dict[str, list[str]]] = None) -> Optional[Lineup]:
    """Custom starting XI for one team, or ``None`` if none is saved."""
    if saved is None:
        saved = load_saved()
    names = saved.get(team.name)
    if not names:
        return None
    return resolve_lineup(team, names)


def lineup_map(
    teams: dict[str, Team], saved: Optional[dict[str, list[str]]] = None
) -> dict[str, Lineup]:
    """All saved lineups as ``{team: Lineup}``, ready for the tournament sims.

    Teams without a saved XI are omitted, so the simulators fall back to best XI.
    """
    if saved is None:
        saved = load_saved()
    out: dict[str, Lineup] = {}
    for name in saved:
        team = teams.get(name)
        if team is None:
            continue
        lu = lineup_for(team, saved)
        if lu is not None:
            out[name] = lu
    return out
