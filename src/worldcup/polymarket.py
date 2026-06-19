"""Read-only Polymarket (Gamma API) client for World Cup markets.

Stdlib-only (``urllib``), matching the rest of the package's no-dependencies
ethos. We only ever *read* public market data — nothing here trades or needs an
API key. See https://docs.polymarket.com/api-reference/introduction.

Each World Cup futures market on Polymarket is a Yes/No question per team
("Will Spain win the 2026 FIFA World Cup?"). The ``Yes`` price is the market's
implied probability, which is exactly what we compare our simulation against.
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

GAMMA_API = "https://gamma-api.polymarket.com"
# The CLOB exposes price *history*, which is how we recover what a played game's
# market thought before kickoff (the live price has collapsed to 0/1 by then).
CLOB_API = "https://clob.polymarket.com"
_HEADERS = {"User-Agent": "worldcup-sim/1.0 (+https://github.com/)"}

# Polymarket groups every World Cup fixture under this single "series". Each
# fixture is its own event with three Yes/No markets (home win / draw / away win).
FIFWC_SERIES_ID = "11433"
# Canonical per-game event slug, e.g. ``fifwc-mex-rsa-2026-06-11``. Excludes the
# derived side-markets (``...-halftime-result``, ``...-exact-score``, ...).
_GAME_SLUG = re.compile(r"^fifwc-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2}$")

# How a market settles, given a finished TournamentOutcome. ``reach:<STAGE>``
# means "reached that stage or deeper" (STAGES from the tournament package).
CHAMPION = "champion"
GROUP_WINNER = "group_winner"

# Curated World Cup events worth comparing, newest format (slug -> label).
# These are the markets our engine can settle from a single tournament run.
KNOWN_EVENTS: list[tuple[str, str]] = [
    ("world-cup-winner", "Tournament winner"),
    ("world-cup-nation-to-reach-round-of-16", "Reach Round of 16"),
    ("world-cup-nation-to-reach-quarterfinals", "Reach Quarterfinals"),
    ("world-cup-group-a-winner", "Group A winner"),
    ("world-cup-group-b-winner", "Group B winner"),
    ("world-cup-group-c-winner", "Group C winner"),
    ("world-cup-group-d-winner", "Group D winner"),
    ("world-cup-group-e-winner", "Group E winner"),
    ("world-cup-group-f-winner", "Group F winner"),
    ("world-cup-group-g-winner", "Group G winner"),
    ("world-cup-group-h-winner", "Group H winner"),
    ("world-cup-group-i-winner", "Group I winner"),
    ("world-cup-group-j-winner", "Group J winner"),
    ("world-cup-group-k-winner", "Group K winner"),
    ("world-cup-group-l-winner", "Group L winner"),
]

# Polymarket spellings that differ from our data/teams_2026.json names.
_ALIASES = {
    "united states": "USA",
    "us": "USA",
    "korea": "South Korea",
    "turkey": "Turkiye",
    "czech republic": "Czechia",
    "congo dr": "DR Congo",
    "cote divoire": "Ivory Coast",
    "bosnia": "Bosnia-Herzegovina",
    "bosnia and herzegovina": "Bosnia-Herzegovina",
    # Per-game (fifwc) feed spellings.
    "korea republic": "South Korea",
    "cabo verde": "Cape Verde",
    "ir iran": "Iran",
    "dr congo": "DR Congo",
}


class PolymarketError(RuntimeError):
    """Network / parsing failure talking to the Gamma API."""


@dataclass
class MarketLine:
    """One team's Yes/No market within an event."""

    raw_name: str             # name as Polymarket writes it ("Türkiye")
    team: Optional[str]       # mapped to our Team name, or None if unmatched
    yes_price: float          # implied probability in [0, 1]
    question: str = ""


@dataclass
class PolyEvent:
    slug: str
    title: str
    mode: str                 # CHAMPION / GROUP_WINNER / "reach:R16" / "reach:QF"
    lines: list[MarketLine] = field(default_factory=list)

    @property
    def matched(self) -> list[MarketLine]:
        return [ln for ln in self.lines if ln.team is not None]

    @property
    def unmatched(self) -> list[str]:
        return [ln.raw_name for ln in self.lines if ln.team is None]


# --- HTTP ------------------------------------------------------------------

def _get(path: str, params: dict) -> object:
    url = f"{GAMMA_API}{path}?{urllib.parse.urlencode(params, doseq=True)}"
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.load(resp)
    except Exception as exc:  # urllib raises a zoo of errors; wrap them all
        raise PolymarketError(f"Polymarket request failed ({url}): {exc}") from exc


# --- name / question parsing ----------------------------------------------

def _normalize(name: str) -> str:
    stripped = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", "", stripped.lower()).strip()


def map_team(raw_name: str, our_teams: dict) -> Optional[str]:
    """Map a Polymarket team label to one of our Team names, or None."""
    key = _normalize(raw_name)
    by_norm = {_normalize(n): n for n in our_teams}
    if key in by_norm:
        return by_norm[key]
    if key in _ALIASES:
        return _ALIASES[key]
    return None


def _extract_team(question: str) -> Optional[str]:
    # "Will Spain win the 2026...", "Will USA win Group D...", "Will Croatia reach..."
    m = re.match(r"\s*Will\s+(.+?)\s+(?:win|reach|make)\b", question)
    return m.group(1).strip() if m else None


def settlement_mode(slug: str) -> str:
    """Infer how an event settles from its slug."""
    s = slug.lower()
    if "group" in s and "winner" in s:
        return GROUP_WINNER
    if "quarterfinal" in s:
        return "reach:QF"
    if "round-of-16" in s or "round_of_16" in s:
        return "reach:R16"
    if "semifinal" in s:
        return "reach:SF"
    if "winner" in s or "champion" in s:
        return CHAMPION
    return CHAMPION


# --- public API ------------------------------------------------------------

def fetch_event(slug: str, our_teams: dict) -> PolyEvent:
    """Fetch one event by slug and parse its per-team Yes prices."""
    data = _get("/events", {"slug": slug})
    if not data:
        raise PolymarketError(f"no Polymarket event found for slug {slug!r}")
    raw = data[0]
    event = PolyEvent(slug=slug, title=raw.get("title", slug), mode=settlement_mode(slug))
    for market in raw.get("markets", []):
        question = market.get("question", "")
        name = _extract_team(question)
        if name is None:
            continue
        try:
            outcomes = json.loads(market.get("outcomes", "[]"))
            prices = json.loads(market.get("outcomePrices", "[]"))
            yes_idx = outcomes.index("Yes") if "Yes" in outcomes else 0
            yes_price = float(prices[yes_idx])
        except (ValueError, IndexError, TypeError):
            continue
        event.lines.append(MarketLine(
            raw_name=name, team=map_team(name, our_teams),
            yes_price=yes_price, question=question,
        ))
    event.lines.sort(key=lambda ln: ln.yes_price, reverse=True)
    return event


def search_events(query: str, limit: int = 15) -> list[tuple[str, str]]:
    """Free-text search; returns ``(slug, title)`` pairs."""
    data = _get("/public-search", {"q": query, "limit_per_type": limit})
    events = data.get("events", []) if isinstance(data, dict) else []
    return [(e.get("slug", ""), e.get("title", "")) for e in events if e.get("slug")]


# --- per-game (fixture) markets --------------------------------------------

@dataclass
class GameMarket:
    """One World Cup fixture and its three-way (home / draw / away) market.

    Prices are each side's ``Yes`` implied probability *as the market currently
    stands*. For a finished game (:attr:`closed`) those have collapsed to 0/1, so
    use :func:`pregame_odds` to recover what the market thought before kickoff.
    """

    slug: str
    title: str
    date: str                 # "2026-06-11" (kickoff, UTC date)
    start_ts: int             # kickoff epoch seconds (0 if unknown)
    closed: bool              # True once the game has been played
    home_raw: str             # team name as Polymarket writes it
    away_raw: str
    home: Optional[str]       # mapped to our Team name, or None
    away: Optional[str]
    home_price: Optional[float]   # live "Yes" implied probability for each side
    draw_price: Optional[float]
    away_price: Optional[float]
    home_token: Optional[str] = None   # CLOB "Yes" token ids (for price history)
    draw_token: Optional[str] = None
    away_token: Optional[str] = None
    score: Optional[str] = None        # "2-0" (home-away) for played games

    @property
    def mapped(self) -> bool:
        return self.home is not None and self.away is not None

    @property
    def live_probs(self) -> Optional[tuple[float, float, float]]:
        if None in (self.home_price, self.draw_price, self.away_price):
            return None
        return (self.home_price, self.draw_price, self.away_price)  # type: ignore[return-value]

    @property
    def result(self) -> Optional[str]:
        """Actual outcome of a played game as ``'H'``/``'D'``/``'A'``, else None."""
        if not self.score:
            return None
        try:
            h, a = (int(x) for x in self.score.split("-"))
        except (ValueError, AttributeError):
            return None
        return "H" if h > a else ("A" if a > h else "D")


def _iso_to_ts(s: str) -> int:
    if not s:
        return 0
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return 0


def _parse_game(raw: dict, our_teams: dict) -> Optional[GameMarket]:
    home_raw = away_raw = None
    for t in raw.get("teams") or []:
        if t.get("ordering") == "home":
            home_raw = t.get("name")
        elif t.get("ordering") == "away":
            away_raw = t.get("name")
    if not home_raw or not away_raw:   # fall back to the "A vs. B" title
        m = re.match(r"(.+?)\s+vs\.?\s+(.+)", raw.get("title", ""))
        if not m:
            return None
        home_raw = home_raw or m.group(1).strip()
        away_raw = away_raw or m.group(2).strip()

    prices: dict[str, Optional[float]] = {"home": None, "draw": None, "away": None}
    tokens: dict[str, Optional[str]] = {"home": None, "draw": None, "away": None}
    for mk in raw.get("markets", []):
        git = (mk.get("groupItemTitle") or "").strip()
        try:
            outcomes = json.loads(mk.get("outcomes", "[]"))
            yes_idx = outcomes.index("Yes") if "Yes" in outcomes else 0
            yes_price = float(json.loads(mk.get("outcomePrices", "[]"))[yes_idx])
            toks = json.loads(mk.get("clobTokenIds", "[]"))
            yes_token = toks[yes_idx] if yes_idx < len(toks) else None
        except (ValueError, IndexError, TypeError):
            continue
        if git.lower().startswith("draw"):
            key = "draw"
        elif _normalize(git) == _normalize(home_raw):
            key = "home"
        elif _normalize(git) == _normalize(away_raw):
            key = "away"
        else:
            continue
        prices[key], tokens[key] = yes_price, yes_token

    start = raw.get("startTime") or raw.get("endDate") or ""
    return GameMarket(
        slug=raw.get("slug", ""), title=raw.get("title", ""), date=start[:10],
        start_ts=_iso_to_ts(start), closed=bool(raw.get("closed")),
        home_raw=home_raw, away_raw=away_raw,
        home=map_team(home_raw, our_teams), away=map_team(away_raw, our_teams),
        home_price=prices["home"], draw_price=prices["draw"], away_price=prices["away"],
        home_token=tokens["home"], draw_token=tokens["draw"], away_token=tokens["away"],
        score=raw.get("score") or None,
    )


def fetch_games(our_teams: dict, *, closed: Optional[bool] = None) -> list[GameMarket]:
    """Fetch every World Cup fixture market, parsed and team-mapped, sorted by
    kickoff. Pass ``closed=True`` for played games only, ``False`` for upcoming."""
    out: list[GameMarket] = []
    offset = 0
    while True:
        data = _get("/events", {"series_id": FIFWC_SERIES_ID, "limit": 100, "offset": offset})
        if not isinstance(data, list) or not data:
            break
        for raw in data:
            if not _GAME_SLUG.match(raw.get("slug", "")):
                continue
            game = _parse_game(raw, our_teams)
            if game is None or (closed is not None and game.closed != closed):
                continue
            out.append(game)
        if len(data) < 100:
            break
        offset += 100
    out.sort(key=lambda g: g.start_ts)
    return out


def _clob_history(token: str) -> list[dict]:
    url = f"{CLOB_API}/prices-history?" + urllib.parse.urlencode(
        {"market": token, "interval": "max", "fidelity": 60})
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.load(resp)
    except Exception as exc:
        raise PolymarketError(f"CLOB history request failed ({url}): {exc}") from exc
    return data.get("history", []) if isinstance(data, dict) else []


def _pregame_yes(token: Optional[str], start_ts: int) -> Optional[float]:
    """The last traded ``Yes`` price at or before kickoff."""
    if not token:
        return None
    history = _clob_history(token)
    if not history:
        return None
    pre = [p for p in history if p.get("t", 0) <= start_ts] if start_ts else history
    point = pre[-1] if pre else history[0]
    try:
        return float(point.get("p"))
    except (TypeError, ValueError):
        return None


def pregame_odds(game: GameMarket) -> Optional[tuple[float, float, float]]:
    """Recover ``(home, draw, away)`` pre-kickoff implied probabilities for a
    played game from CLOB price history. ``None`` if history is unavailable."""
    triple = (
        _pregame_yes(game.home_token, game.start_ts),
        _pregame_yes(game.draw_token, game.start_ts),
        _pregame_yes(game.away_token, game.start_ts),
    )
    return None if None in triple else triple  # type: ignore[return-value]
