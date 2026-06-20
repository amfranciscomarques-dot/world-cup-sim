"""Read the offline Polymarket odds snapshot (``data/odds_2026.json``).

Produced by ``scripts/update_odds.py``. Lets the HTML dashboard and CLI render
market prices without hitting the network — fast and reproducible. A missing
file is not an error; it simply means "no market columns".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .data_loader import DATA_DIR

DEFAULT_ODDS = DATA_DIR / "odds_2026.json"

# Outcome order shared with worldcup.games: home win / draw / away win.
Triple = tuple[float, float, float]


@dataclass
class GameOdds:
    """One fixture's three-way market, as snapshotted."""

    date: str
    home: str
    away: str
    closed: bool
    score: Optional[str]
    live: Optional[Triple]        # current (H, D, A) Yes prices
    pregame: Optional[Triple]     # pre-kickoff (H, D, A), played games only

    @property
    def result(self) -> Optional[str]:
        """Actual outcome as 'H'/'D'/'A' for a played game, else None."""
        if not self.score:
            return None
        try:
            h, a = (int(x) for x in self.score.split("-"))
        except (ValueError, AttributeError):
            return None
        return "H" if h > a else ("A" if a > h else "D")

    def market_triple(self) -> Optional[Triple]:
        """De-vigged (H, D, A) market probabilities: pre-kickoff odds for a
        played game, otherwise the live line. ``None`` if unavailable."""
        raw = self.pregame if (self.closed and self.pregame) else self.live
        if raw is None or None in raw:
            return None
        total = sum(raw)
        if total <= 0:
            return None
        return tuple(x / total for x in raw)  # type: ignore[return-value]


@dataclass
class OddsSnapshot:
    fetched: str
    winner: dict[str, float] = field(default_factory=dict)            # team -> Yes price
    reach: dict[str, dict[str, float]] = field(default_factory=dict)  # stage -> {team: price}
    group_winners: dict[str, dict[str, float]] = field(default_factory=dict)
    games: list[GameOdds] = field(default_factory=list)

    def game(self, home: str, away: str) -> Optional[GameOdds]:
        """Lookup a fixture by team pair, regardless of home/away orientation."""
        for g in self.games:
            if {g.home, g.away} == {home, away}:
                return g
        return None


def _as_map(rows: list) -> dict[str, float]:
    return {r["team"]: float(r["price"]) for r in rows if r.get("team")}


def _triple(v) -> Optional[Triple]:
    if not v or len(v) != 3 or any(x is None for x in v):
        return None
    return (float(v[0]), float(v[1]), float(v[2]))


def refresh_odds(
    teams: Optional[dict] = None,
    tournament=None,
    *,
    path: Path | str = DEFAULT_ODDS,
    log=None,
) -> dict:
    """Fetch the Polymarket odds snapshot and (re)write ``data/odds_2026.json``.

    The importable core of ``scripts/update_odds.py`` — also called by the web
    UI's "Update data" action. Captures the winner / reach / group futures plus
    every fixture's three-way market (live prices, and pre-kickoff odds recovered
    from CLOB history for played games — one request per played-game side, so this
    is the slow half of a refresh). Requires network. Returns a summary dict.
    """
    from datetime import date

    from . import polymarket  # lazy: network code only needed when refreshing
    from .data_loader import load_world_cup

    def say(msg: str) -> None:
        if log:
            log(msg)

    if teams is None or tournament is None:
        teams, tournament = load_world_cup()

    def event_prices(slug: str) -> list[dict]:
        try:
            event = polymarket.fetch_event(slug, teams)
        except polymarket.PolymarketError as exc:
            say(f"  warn: {slug}: {exc}")
            return []
        return [{"team": ln.team, "price": round(ln.yes_price, 4)} for ln in event.matched]

    say("fetching futures markets (winner / reach / groups)...")
    winner = event_prices("world-cup-winner")
    reach = {
        "R16": event_prices("world-cup-nation-to-reach-round-of-16"),
        "QF": event_prices("world-cup-nation-to-reach-quarterfinals"),
    }
    group_winners = {
        letter: event_prices(f"world-cup-group-{letter.lower()}-winner")
        for letter in tournament.groups
    }

    say("fetching per-game fixture markets...")
    games = polymarket.fetch_games(teams)
    closed = [g for g in games if g.mapped and g.closed]
    say(f"recovering pre-kickoff odds for {len(closed)} played games (CLOB history)...")

    rows: list[dict] = []
    for g in games:
        if not g.mapped:
            continue
        live = g.live_probs
        pregame = None
        if g.closed:
            try:
                pregame = polymarket.pregame_odds(g)
            except polymarket.PolymarketError:
                pregame = None
            say(f"    {g.home} vs {g.away}: {'ok' if pregame else 'no history'}")
        rows.append({
            "date": g.date, "home": g.home, "away": g.away, "closed": g.closed,
            "score": g.score,
            "live": [round(x, 4) for x in live] if live else None,
            "pregame": [round(x, 4) for x in pregame] if pregame else None,
        })

    rows.sort(key=lambda r: (r["date"], r["home"]))
    fetched = date.today().isoformat()
    payload = {
        "_about": (
            "Polymarket odds snapshot for the 2026 World Cup: tournament-winner / "
            "reach / group-winner futures and every fixture's three-way market "
            "(live prices + pre-kickoff odds for played games). Read offline by the "
            "HTML dashboard. Regenerate with scripts/update_odds.py."
        ),
        "_fetched": fetched,
        "winner": winner,
        "reach": reach,
        "group_winners": group_winners,
        "games": rows,
    }
    out = Path(path)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    graded = sum(1 for r in rows if r["pregame"])
    say(f"wrote {out.name}: {len(winner)} winner lines, {len(rows)} fixtures "
        f"({graded} with pre-kickoff history)")
    return {
        "path": str(out), "fetched": fetched, "winner": len(winner),
        "games": len(rows), "pregame": graded,
    }


def load_odds(path: Path | str = DEFAULT_ODDS) -> Optional[OddsSnapshot]:
    """Load the odds snapshot, or ``None`` if no file exists yet."""
    p = Path(path)
    if not p.exists():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))
    games = [
        GameOdds(
            date=g.get("date", ""), home=g["home"], away=g["away"],
            closed=bool(g.get("closed")), score=g.get("score"),
            live=_triple(g.get("live")), pregame=_triple(g.get("pregame")),
        )
        for g in raw.get("games", [])
    ]
    return OddsSnapshot(
        fetched=raw.get("_fetched", ""),
        winner=_as_map(raw.get("winner", [])),
        reach={k: _as_map(v) for k, v in raw.get("reach", {}).items()},
        group_winners={k: _as_map(v) for k, v in raw.get("group_winners", {}).items()},
        games=games,
    )
