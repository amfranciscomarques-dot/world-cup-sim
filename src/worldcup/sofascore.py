"""Read-only SofaScore client for World Cup player/team match ratings.

Stdlib-only (``urllib``), matching the rest of the package's no-dependencies
ethos and mirroring :mod:`worldcup.polymarket`. We only ever *read* public data.

SofaScore publishes, for every finished match, an average performance rating per
player (~6.0 = anonymous, ~10.0 = perfect). The mean of a side's player ratings
is its team match rating. We aggregate those across the games a team has played
so far into an in-tournament "form" signal that feeds the simulation
(see :class:`worldcup.factors.builtin.SofaScoreFactor`).

The public API is bot-protected and its endpoints/IDs can shift, so every call
is best-effort: failures raise :class:`SofaScoreError`, partial data is fine, and
the caller (``sofascore_store.refresh_sofascore``) never destroys a good offline
snapshot on a failed fetch. Today the simulation reads the offline snapshot; this
module is only exercised when explicitly refreshing it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from . import polymarket

API = "https://api.sofascore.com/api/v1"
# SofaScore's unique-tournament id for the FIFA World Cup. The 2026 season id is
# discovered at runtime from /seasons (it changes every edition), so only this
# stable id is hard-coded.
WORLD_CUP_UNIQUE_TOURNAMENT = 16
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (worldcup-sim/1.0)",
    "Accept": "application/json",
}

# SofaScore spellings that differ from our data/teams_2026.json names *and* from
# Polymarket's aliases (we fall back to polymarket.map_team first, which already
# handles e.g. "United States" -> "USA").
_ALIASES = {
    "usa": "USA",
    "south korea": "South Korea",
    "türkiye": "Turkiye",
    "turkey": "Turkiye",
    "ivory coast": "Ivory Coast",
    "côte d'ivoire": "Ivory Coast",
    "cape verde": "Cape Verde",
    "dr congo": "DR Congo",
    "czechia": "Czechia",
}


class SofaScoreError(RuntimeError):
    """Network / parsing failure talking to the SofaScore API."""


@dataclass
class PlayerRating:
    name: str
    rating: float


@dataclass
class MatchRatings:
    """One finished fixture's SofaScore ratings, mapped to our team names."""

    date: str                       # YYYY-MM-DD (kickoff, UTC)
    home: Optional[str]             # our Team name, or None if unmapped
    away: Optional[str]
    home_rating: Optional[float]    # mean of the side's player ratings
    away_rating: Optional[float]
    score: Optional[str] = None     # "2-1" if available
    home_players: list[PlayerRating] = field(default_factory=list)
    away_players: list[PlayerRating] = field(default_factory=list)

    @property
    def mapped(self) -> bool:
        return self.home is not None and self.away is not None


def _get(path: str) -> object:
    url = f"{API}{path}"
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.load(resp)
    except Exception as exc:  # urllib raises a zoo of errors; wrap them all
        raise SofaScoreError(f"SofaScore request failed ({url}): {exc}") from exc


def map_team(raw_name: str, our_teams: dict) -> Optional[str]:
    """Map a SofaScore team label to one of our Team names, or None.

    Reuses Polymarket's normaliser/alias table first (so the bulk of the
    spellings are already covered), then applies a few SofaScore-specific ones.
    """
    mapped = polymarket.map_team(raw_name, our_teams)
    if mapped is not None:
        return mapped
    return _ALIASES.get((raw_name or "").strip().lower())


def _season_id(year: str = "2026") -> int:
    """Discover the World Cup season id for ``year`` from the seasons list."""
    data = _get(f"/unique-tournament/{WORLD_CUP_UNIQUE_TOURNAMENT}/seasons")
    seasons = data.get("seasons", []) if isinstance(data, dict) else []
    for s in seasons:
        if str(s.get("year", "")).strip() == year or year in str(s.get("year", "")):
            return int(s["id"])
    raise SofaScoreError(f"no SofaScore World Cup season found for {year!r}")


def _iso_date(ts: Optional[int]) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def _team_ratings(event_id: int) -> tuple[list[PlayerRating], list[PlayerRating]]:
    """Per-player ratings for both sides of one event (empty lists if absent)."""
    data = _get(f"/event/{event_id}/lineups")
    if not isinstance(data, dict):
        return [], []

    def side(key: str) -> list[PlayerRating]:
        out: list[PlayerRating] = []
        for entry in (data.get(key) or {}).get("players", []) or []:
            stats = entry.get("statistics") or {}
            rating = stats.get("rating")
            name = (entry.get("player") or {}).get("name", "")
            if rating is None:
                continue
            try:
                out.append(PlayerRating(name=name, rating=float(rating)))
            except (TypeError, ValueError):
                continue
        return out

    return side("home"), side("away")


def _mean(players: list[PlayerRating]) -> Optional[float]:
    rated = [p.rating for p in players if p.rating > 0]
    if not rated:
        return None
    return round(sum(rated) / len(rated), 2)


def fetch_finished_matches(our_teams: dict, *, year: str = "2026", max_pages: int = 6,
                           log=None) -> list[MatchRatings]:
    """Fetch every finished World Cup fixture's SofaScore ratings.

    Walks the tournament's "last events" pages, then pulls per-player ratings for
    each finished game and folds them into a team match average. Best-effort: a
    game whose lineup/ratings can't be read is skipped, not fatal.
    """
    def say(msg: str) -> None:
        if log:
            log(msg)

    season = _season_id(year)
    say(f"resolved World Cup {year} season id {season}")

    events: list[dict] = []
    for page in range(max_pages):
        try:
            data = _get(f"/unique-tournament/{WORLD_CUP_UNIQUE_TOURNAMENT}"
                        f"/season/{season}/events/last/{page}")
        except SofaScoreError:
            break
        page_events = data.get("events", []) if isinstance(data, dict) else []
        if not page_events:
            break
        events.extend(page_events)
        if not data.get("hasNextPage"):
            break

    out: list[MatchRatings] = []
    for ev in events:
        if (ev.get("status") or {}).get("type") != "finished":
            continue
        home_raw = (ev.get("homeTeam") or {}).get("name", "")
        away_raw = (ev.get("awayTeam") or {}).get("name", "")
        hp, ap = _team_ratings(int(ev["id"]))
        hs = (ev.get("homeScore") or {}).get("current")
        as_ = (ev.get("awayScore") or {}).get("current")
        score = f"{hs}-{as_}" if hs is not None and as_ is not None else None
        out.append(MatchRatings(
            date=_iso_date(ev.get("startTimestamp")),
            home=map_team(home_raw, our_teams),
            away=map_team(away_raw, our_teams),
            home_rating=_mean(hp), away_rating=_mean(ap),
            score=score, home_players=hp, away_players=ap,
        ))
        say(f"  {home_raw} vs {away_raw}: "
            f"{'ok' if (hp or ap) else 'no ratings'}")
    return out
