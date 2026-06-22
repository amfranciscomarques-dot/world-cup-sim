"""User bet tracker: record real-money bets, project ROI via Monte Carlo.

The tracker is a thin layer on top of the existing simulation engine — every
saved bet is replayed through the same engine that prices it, so the realised
P&L distribution tracks the analytic edge. That isn't an independent backtest
(see :mod:`worldcup.betting` for the same caveat on the futures side), but it
*is* the variance / drawdown distribution of your edge if the model is right,
which is what Kelly sizing needs.

The model-vs-market comparison per leg is the part that stands on its own:
``implied_probability(decimal_odds)`` gives the bookmaker's implied probability
(vig-included), and ``compare_selection_to_model`` subtracts the model's
probability to surface the edge on each selection.

Persistence is a plain JSON file (``data/user_bets_2026.json`` by default).
Each :class:`Bet` carries a unique ``id``, the date placed, the stake in
EUR/USD, the combined decimal odds of the slip, and its selections. A Bet
settles only when *every* selection hits; partial cash-out is out of scope.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .data_loader import DATA_DIR

DEFAULT_BETS_PATH = DATA_DIR / "user_bets_2026.json"

# Selection types we know how to price. Unknown types fail loudly on load —
# silent fallbacks would let a typo swallow a bet.
KNOWN_SELECTION_TYPES = frozenset({
    "match_result",   # team ML: home/draw/away, encoded by ``team`` + ``side``
    "over25",         # total goals > 2.5
    "under25",        # total goals < 2.5
    "btts",           # both teams to score, ``side`` = "yes" / "no"
    "player_goal",    # any-time goalscorer, ``player`` = canonical player name
    "outright",       # tournament-winner, ``team`` = the backed team
})


@dataclass(frozen=True)
class BetSelection:
    """One leg of a bet.

    ``match_id`` ties a fixture-level selection to a fixture in
    ``data/fixtures_2026.json``; outright selections leave it blank. ``odds``
    is the *decimal* price paid (1.78, 5.80, …). The model side uses ``type``
    to look up the right probability — ``match_result`` is the one where the
    side of the bet (home/away) is encoded by ``side``.
    """

    type: str
    label: str
    odds: float
    match_id: str = ""
    team: str = ""            # match_result / outright
    player: str = ""          # player_goal
    side: str = ""            # match_result: "home" | "away" | "draw"; btts: "yes" | "no"

    def __post_init__(self) -> None:
        if self.type not in KNOWN_SELECTION_TYPES:
            raise ValueError(
                f"unknown selection type {self.type!r}; "
                f"expected one of {sorted(KNOWN_SELECTION_TYPES)}"
            )

    @property
    def implied_probability(self) -> float:
        """Bookmaker's implied probability from the decimal price (vig-included)."""
        return implied_probability(self.odds)


@dataclass
class Bet:
    """A complete bet slip: stake, combined odds, and one or more selections."""

    id: str
    date_placed: str          # ISO date "YYYY-MM-DD"
    stake: float
    total_odds: float
    selections: list[BetSelection]

    def settles(self, hits: dict[str, bool]) -> bool:
        """Did every selection in this bet hit?

        ``hits`` maps ``selection.match_id`` → ``True``/``False``. A bet wins
        only when every selection on it wins — there is no partial cash-out.
        Selections without a ``match_id`` (outrights) are always considered
        to be live: the caller is responsible for adding them to ``hits`` if
        they should count.
        """
        for sel in self.selections:
            if not sel.match_id:
                continue
            if not hits.get(sel.match_id, False):
                return False
        return True


@dataclass
class TrackerPerformance:
    """Aggregate P&L over ``iterations`` Monte-Carlo runs."""

    iterations: int
    bets: list[Bet]
    total_staked: float
    pnl: list[float]            # one entry per run; total P&L that run
    per_bet_pnl: dict[str, list[float]] = field(default_factory=dict)

    @property
    def mean_pnl(self) -> float:
        return sum(self.pnl) / len(self.pnl) if self.pnl else 0.0

    @property
    def roi(self) -> float:
        return self.mean_pnl / self.total_staked if self.total_staked > 0 else 0.0

    @property
    def win_rate(self) -> float:
        if not self.pnl:
            return 0.0
        return sum(1 for p in self.pnl if p > 0) / len(self.pnl)

    def percentile(self, q: float) -> float:
        if not self.pnl:
            return 0.0
        s = sorted(self.pnl)
        idx = min(len(s) - 1, max(0, int(q * len(s))))
        return s[idx]


# --- Helpers ---------------------------------------------------------------


def implied_probability(decimal_odds: float) -> float:
    """Return 1 / decimal_odds, the bookmaker's implied probability.

    Decimal odds include the stake, so 1.78 ↔ ~0.562 (vig-included).
    """
    if decimal_odds <= 0:
        raise ValueError(f"decimal odds must be > 0, got {decimal_odds!r}")
    return 1.0 / decimal_odds


@dataclass(frozen=True)
class SelectionComparison:
    """One bet selection priced by the model vs priced by the bookmaker."""

    selection: BetSelection
    model_prob: float
    implied_prob: float

    @property
    def edge(self) -> float:
        """Model probability − bookmaker implied probability (positive = edge)."""
        return self.model_prob - self.implied_prob

    @property
    def fair_odds(self) -> float:
        """Decimal fair price derived from the model probability (1 / model_prob)."""
        return 1.0 / self.model_prob if 0.0 < self.model_prob <= 1.0 else float("inf")

    @property
    def bookmaker_overround(self) -> float:
        """The vig baked into the price; 0 = no vig."""
        # A fair price would be 1 / model_prob; the bookmaker's is selection.odds.
        # Compare both sides to the model's fair: bookmaker odds vs fair odds.
        return (self.implied_prob / self.model_prob - 1.0) if self.model_prob > 0 else 0.0


def compare_selection_to_model(
    selection: BetSelection, model_prob: float,
) -> SelectionComparison:
    """Pair a bookmaker-priced selection with the model's probability.

    ``model_prob`` is whatever the engine produces for this selection
    (``over25`` probability, the home-win probability, a player's per-match
    goal expectation, etc.).  The selection's ``odds`` carries the
    bookmaker's price; we derive its implied probability and surface the
    edge.
    """
    return SelectionComparison(
        selection=selection,
        model_prob=float(model_prob),
        implied_prob=implied_probability(selection.odds),
    )


# --- Selection pricing -----------------------------------------------------


def price_selection(
    selection: BetSelection,
    *,
    sim=None,
    teams: Optional[dict] = None,
    fixtures: Optional[dict[str, dict]] = None,
    odds=None,
    game_iterations: int = 400,
    scorer_iterations: int = 300,
) -> SelectionComparison:
    """Price one :class:`BetSelection` against the model.

    Routes by ``selection.type``:

    - ``match_result`` → ``MatchOdds.{home_win|draw|away_win}``
    - ``over25`` / ``under25`` → ``MatchOdds.over25`` (or its complement)
    - ``btts`` → ``MatchOdds.btts`` (``side`` selects yes/no)
    - ``player_goal`` → per-player scoring rate from the live engine
    - ``outright`` → ``odds.winner[team]`` (the model probability from the
      Monte-Carlo title race, already cached in the odds snapshot)

    For fixture-level selections, ``fixtures`` is a ``match_id -> record``
    map and ``teams`` the loaded squad dict from
    :func:`worldcup.data_loader.load_world_cup`.
    """
    if selection.type == "outright":
        if odds is None or not odds.winner:
            raise ValueError("outright pricing requires an OddsSnapshot with .winner")
        if selection.team not in odds.winner:
            raise KeyError(f"team {selection.team!r} not in odds snapshot winner map")
        return compare_selection_to_model(selection, odds.winner[selection.team])

    # Fixture-level types below all need fixtures + teams + a simulator.
    if sim is None or teams is None or fixtures is None:
        raise ValueError(
            f"{selection.type!r} pricing requires sim, teams, and fixtures"
        )
    if selection.match_id not in fixtures:
        raise KeyError(f"unknown match_id {selection.match_id!r}")
    fx = fixtures[selection.match_id]
    home, away = teams[fx["home"]], teams[fx["away"]]
    if selection.type in ("match_result", "over25", "under25", "btts"):
        mo = sim.monte_carlo(home, away, game_iterations, stage="group",
                             neutral=True)
    elif selection.type == "player_goal":
        # Player scoring needs the live engine so per-player rates are
        # produced. Smaller N is fine; scorer rates converge quickly.
        mo = sim.monte_carlo(home, away, scorer_iterations, stage="group",
                             neutral=True, track_scorers=True)
    else:
        raise ValueError(f"unhandled selection type {selection.type!r}")

    if selection.type == "match_result":
        side = selection.side or ("home" if selection.team == home.name else "away")
        model_prob = {
            "home": mo.home_win,
            "draw": mo.draw,
            "away": mo.away_win,
        }[side]
    elif selection.type == "over25":
        model_prob = mo.over25
    elif selection.type == "under25":
        model_prob = 1.0 - mo.over25
    elif selection.type == "btts":
        model_prob = mo.btts if selection.side == "yes" else 1.0 - mo.btts
    elif selection.type == "player_goal":
        # Pick the player's scoring rate from the home or away list, whichever
        # side they're on. Default 0 if the player isn't in either XI track.
        rate = 0.0
        target = selection.player.lower()
        for (pname, g_per, _a) in mo.home_scorers + mo.away_scorers:
            if pname.lower() == target:
                rate = g_per
                break
        model_prob = rate
    else:
        raise ValueError(f"unhandled selection type {selection.type!r}")

    return compare_selection_to_model(selection, model_prob)


# --- Persistence -----------------------------------------------------------


def _bet_to_dict(bet: Bet) -> dict:
    return {
        "id": bet.id,
        "date_placed": bet.date_placed,
        "stake": bet.stake,
        "total_odds": bet.total_odds,
        "selections": [s.__dict__ for s in bet.selections],
    }


def _bet_from_dict(d: dict) -> Bet:
    sels = [BetSelection(**s) for s in d.get("selections", [])]
    return Bet(
        id=str(d["id"]),
        date_placed=str(d.get("date_placed", "")),
        stake=float(d["stake"]),
        total_odds=float(d["total_odds"]),
        selections=sels,
    )


def load_bets(path: Path | str = DEFAULT_BETS_PATH) -> list[Bet]:
    """Load saved bets from JSON, or return ``[]`` if the file doesn't exist.

    A missing file is not an error — the user has simply placed no bets yet.
    Validation errors (unknown selection type, malformed odds, etc.) propagate
    loudly so a typo can't quietly swallow a bet.
    """
    p = Path(path)
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    rows = raw.get("bets", []) if isinstance(raw, dict) else raw
    return [_bet_from_dict(r) for r in rows]


def save_bets(bets: list[Bet], path: Path | str = DEFAULT_BETS_PATH) -> None:
    """Persist ``bets`` to JSON, overwriting any existing file.

    The schema is stable: a top-level ``{"bets": [...]}`` object so future
    fields (bankroll snapshot, tags, …) can be added without breaking older
    readers.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"bets": [_bet_to_dict(b) for b in bets]}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --- Simulation ------------------------------------------------------------


# A settle-callback returns a {match_id: hit?} mapping for one run. It is
# injected so :func:`simulate_tracker_performance` is testable without the
# engine — the engine-driven path threads through `tournament.simulator`.
SettleFn = Callable[[Bet, int], dict[str, bool]]


def _default_settle_from_engine(
    sim_settles_per_run: Callable[[int], dict[str, bool]],
) -> SettleFn:
    """Wrap a "per-run engine settle" function into the (Bet, run_idx) shape.

    ``sim_settles_per_run(run_idx)`` returns the *match_id → hit* mapping the
    tournament simulator produced for that run (one entry per fixture the
    tracker knows about). The returned closure ignores ``Bet`` and forwards
    ``run_idx`` — every bet sees the same tournament outcome in a given run.
    """

    def _settle(_bet: Bet, run_idx: int) -> dict[str, bool]:
        return sim_settles_per_run(run_idx)

    return _settle


def simulate_tracker_performance(
    bets: list[Bet],
    settle_selections: SettleFn,
    *,
    n: int = 1000,
) -> TrackerPerformance:
    """Run ``n`` Monte-Carlo tournaments and settle every bet each run.

    ``settle_selections(bet, run_idx)`` returns the per-fixture hits for that
    run. P&L per run is the sum of ``(stake * (total_odds - 1))`` for every
    winning bet minus the losing stakes. Returns an aggregate
    :class:`TrackerPerformance` with the full P&L distribution.
    """
    if not bets:
        return TrackerPerformance(iterations=n, bets=[], total_staked=0.0,
                                  pnl=[0.0] * n, per_bet_pnl={})

    total_staked = sum(b.stake for b in bets)
    pnl: list[float] = [0.0] * n
    per_bet_pnl: dict[str, list[float]] = {b.id: [0.0] * n for b in bets}

    for run_idx in range(n):
        for bet in bets:
            hits = settle_selections(bet, run_idx)
            if bet.settles(hits):
                # Net = stake * (decimal_odds - 1); stake returned + winnings.
                ret = bet.stake * (bet.total_odds - 1.0)
                pnl[run_idx] += ret
                per_bet_pnl[bet.id][run_idx] = ret
            else:
                pnl[run_idx] -= bet.stake
                per_bet_pnl[bet.id][run_idx] = -bet.stake

    return TrackerPerformance(
        iterations=n,
        bets=bets,
        total_staked=total_staked,
        pnl=pnl,
        per_bet_pnl=per_bet_pnl,
    )
