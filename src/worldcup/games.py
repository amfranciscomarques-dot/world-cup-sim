"""Per-game odds comparison: our model's match probabilities vs Polymarket's
three-way fixture markets.

Unlike the futures :mod:`worldcup.betting` layer (whose simulated P&L settles on
the same model that prices it), played games have *real* results, so comparing
the model and the market against those outcomes is a genuine backtest. We score
both with a multiclass Brier score and a simple pick-accuracy.

The market side uses live prices for upcoming games and, for played games, the
pre-kickoff price recovered from CLOB history (see :func:`polymarket.pregame_odds`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

from . import polymarket
from .engine import MatchSimulator
from .models import Team
from .polymarket import GameMarket

# Outcome order shared everywhere: home win / draw / away win.
OUTCOMES = ("H", "D", "A")


@dataclass
class GameComparison:
    game: GameMarket
    model: tuple[float, float, float]            # (H, D, A), sums to 1
    market: Optional[tuple[float, float, float]] # (H, D, A) normalized, or None
    iterations: int

    @property
    def edges(self) -> Optional[tuple[float, float, float]]:
        """model - market per outcome (positive = we rate it higher)."""
        if self.market is None:
            return None
        return tuple(m - k for m, k in zip(self.model, self.market))  # type: ignore[return-value]

    @property
    def model_pick(self) -> str:
        return OUTCOMES[max(range(3), key=lambda i: self.model[i])]

    @property
    def market_pick(self) -> Optional[str]:
        if self.market is None:
            return None
        return OUTCOMES[max(range(3), key=lambda i: self.market[i])]

    @property
    def best_edge(self) -> Optional[tuple[str, float]]:
        """The outcome where we most disagree upward with the market."""
        e = self.edges
        if e is None:
            return None
        i = max(range(3), key=lambda j: e[j])
        return OUTCOMES[i], e[i]


def _model_probs(
    sim: MatchSimulator,
    teams: dict[str, Team],
    game: GameMarket,
    iterations: int,
    host_names: set[str],
) -> tuple[float, float, float]:
    """Monte Carlo (home, draw, away) win probabilities for a fixture, applying
    host advantage exactly like the group stage (a host plays at home; otherwise
    the venue is neutral)."""
    home, away = teams[game.home], teams[game.away]   # type: ignore[index]
    home_host = game.home in host_names and game.away not in host_names
    away_host = game.away in host_names and game.home not in host_names
    if away_host:
        # Give the host the home slot, then map probabilities back to game order.
        odds = sim.monte_carlo(away, home, iterations, stage="group", neutral=False)
        return (odds.away_win, odds.draw, odds.home_win)
    odds = sim.monte_carlo(home, away, iterations, stage="group", neutral=not home_host)
    return (odds.home_win, odds.draw, odds.away_win)


def _market_probs(game: GameMarket, market: str) -> Optional[tuple[float, float, float]]:
    """Normalized (H, D, A) market probabilities, de-vigged to sum to 1.

    ``market`` is ``"live"`` (current prices), ``"pregame"`` (CLOB history at
    kickoff), or ``"auto"`` (pregame for played games, live otherwise)."""
    if market == "pregame" or (market == "auto" and game.closed):
        triple = polymarket.pregame_odds(game)
    else:
        triple = game.live_probs
    if triple is None or None in triple:
        return None
    total = sum(triple)
    if total <= 0:
        return None
    return tuple(x / total for x in triple)  # type: ignore[return-value]


def compare_game(
    sim: MatchSimulator,
    teams: dict[str, Team],
    game: GameMarket,
    iterations: int,
    *,
    host_names: set[str],
    market: str = "auto",
) -> GameComparison:
    """Compare the model and the market for one fixture (must be team-mapped)."""
    return GameComparison(
        game=game,
        model=_model_probs(sim, teams, game, iterations, host_names),
        market=_market_probs(game, market),
        iterations=iterations,
    )


def compare_games(
    sim: MatchSimulator,
    teams: dict[str, Team],
    games: list[GameMarket],
    iterations: int,
    *,
    host_names: set[str],
    market: str = "auto",
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> list[GameComparison]:
    """Compare every team-mapped fixture in ``games``."""
    mapped = [g for g in games if g.mapped]
    out: list[GameComparison] = []
    for i, game in enumerate(mapped, 1):
        out.append(compare_game(sim, teams, game, iterations, host_names=host_names, market=market))
        if on_progress:
            on_progress(i, len(mapped))
    return out


@dataclass
class AccuracySummary:
    """How model and market fared against the *actual* results of played games."""

    n: int
    model_correct: int
    market_correct: int
    model_brier: float       # mean multiclass Brier (0 best, 2 worst)
    market_brier: float


def _scored(comparisons: list[GameComparison]) -> list[GameComparison]:
    """The comparisons that can be graded: a known result *and* market odds."""
    return [c for c in comparisons if c.game.result and c.market is not None]


def accuracy_summary(comparisons: list[GameComparison]) -> AccuracySummary:
    """Score the played games among ``comparisons`` (those with a known result
    and market probabilities). A lower Brier / higher accuracy is better."""
    rows = _scored(comparisons)
    n = len(rows)
    if n == 0:
        return AccuracySummary(0, 0, 0, 0.0, 0.0)
    model_correct = market_correct = 0
    model_brier = market_brier = 0.0
    for c in rows:
        res = c.game.result
        ri = OUTCOMES.index(res)               # type: ignore[arg-type]
        model_correct += c.model_pick == res
        market_correct += c.market_pick == res
        model_brier += sum((c.model[i] - (1.0 if i == ri else 0.0)) ** 2 for i in range(3))
        market_brier += sum((c.market[i] - (1.0 if i == ri else 0.0)) ** 2 for i in range(3))  # type: ignore[index]
    return AccuracySummary(n, model_correct, market_correct, model_brier / n, market_brier / n)


def _logloss(probs: tuple[float, float, float], result_idx: int) -> float:
    """Negative log-likelihood the model/market assigned to what actually
    happened. Lower is better; the probability is clamped off 0 so a confident
    miss is penalised heavily but never infinitely."""
    return -math.log(max(probs[result_idx], 1e-9))


@dataclass
class GameGrade:
    """One finished game, graded for the dashboard's per-game strip."""

    comparison: GameComparison
    result: str                  # H / D / A
    model_right: bool            # model's favourite matched the result
    market_right: bool
    model_brier: float           # this game's contribution (0 best, 2 worst)
    market_brier: float
    disagreed: bool              # model and market named different favourites


@dataclass
class Dashboard:
    """Current model-vs-market scoreboard over every finished game.

    Extends :class:`AccuracySummary` with log-loss, the actual-result mix, and a
    head-to-head on the games where the two sides named *different* favourites —
    the cleanest read on whether the model is actually finding an edge."""

    summary: AccuracySummary
    model_logloss: float
    market_logloss: float
    result_mix: dict[str, int]            # actual outcomes: {"H": .., "D": .., "A": ..}
    disagreements: int                    # games where the favourites differed
    model_edge_wins: int                  # of those, the model's pick was right
    market_edge_wins: int                 # of those, the market's pick was right
    games: list[GameGrade]                # chronological, one per finished game

    @property
    def n(self) -> int:
        return self.summary.n

    @property
    def model_sharper(self) -> bool:
        return self.summary.model_brier < self.summary.market_brier


def build_dashboard(comparisons: list[GameComparison]) -> Dashboard:
    """Aggregate finished-game performance into a single :class:`Dashboard`."""
    summary = accuracy_summary(comparisons)
    rows = _scored(comparisons)
    model_ll = market_ll = 0.0
    mix = {"H": 0, "D": 0, "A": 0}
    disagreements = model_edge = market_edge = 0
    grades: list[GameGrade] = []
    for c in rows:
        res = c.game.result
        ri = OUTCOMES.index(res)            # type: ignore[arg-type]
        mix[res] += 1                        # type: ignore[index]
        model_ll += _logloss(c.model, ri)
        market_ll += _logloss(c.market, ri)  # type: ignore[arg-type]
        model_right = c.model_pick == res
        market_right = c.market_pick == res
        disagreed = c.model_pick != c.market_pick
        if disagreed:
            disagreements += 1
            model_edge += model_right
            market_edge += market_right
        grades.append(GameGrade(
            comparison=c,
            result=res,                      # type: ignore[arg-type]
            model_right=model_right,
            market_right=market_right,
            model_brier=sum((c.model[i] - (1.0 if i == ri else 0.0)) ** 2 for i in range(3)),
            market_brier=sum((c.market[i] - (1.0 if i == ri else 0.0)) ** 2 for i in range(3)),  # type: ignore[index]
            disagreed=disagreed,
        ))
    n = summary.n
    return Dashboard(
        summary=summary,
        model_logloss=model_ll / n if n else 0.0,
        market_logloss=market_ll / n if n else 0.0,
        result_mix=mix,
        disagreements=disagreements,
        model_edge_wins=model_edge,
        market_edge_wins=market_edge,
        games=grades,
    )


def find_game(games: list[GameMarket], query: str) -> Optional[GameMarket]:
    """Locate a fixture by slug or a loose ``home vs away`` / team substring."""
    q = query.strip().lower()
    for g in games:
        if g.slug.lower() == q:
            return g
    matches = [
        g for g in games
        if q in g.title.lower()
        or q in f"{g.home} vs {g.away}".lower()
        or (g.home and g.away and all(part.strip() in (g.home.lower(), g.away.lower())
                                      for part in q.split(" vs ")))
    ]
    return matches[0] if len(matches) >= 1 else None
