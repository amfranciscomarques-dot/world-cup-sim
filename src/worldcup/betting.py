"""Compare our simulation against Polymarket prices and simulate value bets.

The flow:

1. Pull a Polymarket event's per-team ``Yes`` prices (implied probabilities).
2. Monte-Carlo the tournament with our engine to get *our* probabilities.
3. Compare them, flag value (where our model > the market), size stakes with
   fractional Kelly, and replay the tournament to get a P&L distribution.

A caveat worth stating plainly: the bets are *settled using the same simulation
that priced them*, so the realized ROI necessarily tracks the analytic edge.
This is not an independent backtest — it tells you the **variance/drawdown** of
your edge *if the model is right*, which is what Kelly sizing actually needs. The
comparison table (where we disagree with the market, and by how much) is the part
that stands on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .polymarket import CHAMPION, GROUP_WINNER, PolyEvent
from .tournament import TournamentSimulator
from .tournament.simulator import STAGES, TournamentOutcome


def settles(mode: str, team: str, outcome: TournamentOutcome) -> bool:
    """Did ``team`` satisfy a market of ``mode`` in this tournament run?"""
    if mode == CHAMPION:
        return outcome.champion == team
    if mode == GROUP_WINNER:
        return any(g.winner.team == team for g in outcome.groups)
    if mode.startswith("reach:"):
        target = mode.split(":", 1)[1]
        deepest = outcome.deepest_stage()
        if team not in deepest:
            return False
        return STAGES.index(deepest[team]) >= STAGES.index(target)
    raise ValueError(f"unknown settlement mode {mode!r}")


def kelly_fraction(model_prob: float, price: float) -> float:
    """Full-Kelly fraction of bankroll for a YES share bought at ``price`` that
    pays $1. Cleanly reduces to ``(p - price) / (1 - price)``; 0 if no edge."""
    if not 0.0 < price < 1.0:
        return 0.0
    return max(0.0, (model_prob - price) / (1.0 - price))


@dataclass
class Comparison:
    team: str
    market_prob: float
    model_prob: float

    @property
    def edge(self) -> float:
        """Probability edge: how much higher our model rates the team."""
        return self.model_prob - self.market_prob

    @property
    def ev(self) -> float:
        """Expected return per $1 staked buying YES at the market price."""
        return self.model_prob / self.market_prob - 1.0 if self.market_prob > 0 else 0.0


@dataclass
class Bet:
    team: str
    price: float            # market YES price paid per share
    model_prob: float
    stake: float

    @property
    def shares(self) -> float:
        return self.stake / self.price if self.price > 0 else 0.0

    @property
    def edge(self) -> float:
        return self.model_prob - self.price

    @property
    def ev(self) -> float:
        return self.model_prob / self.price - 1.0 if self.price > 0 else 0.0


@dataclass
class BettingReport:
    iterations: int
    event_title: str
    mode: str
    bankroll: float
    comparisons: list[Comparison]
    bets: list[Bet]
    pnl: list[float] = field(default_factory=list)   # realized profit per run
    unmatched: list[str] = field(default_factory=list)

    @property
    def total_staked(self) -> float:
        return sum(b.stake for b in self.bets)

    @property
    def mean_pnl(self) -> float:
        return sum(self.pnl) / len(self.pnl) if self.pnl else 0.0

    @property
    def roi(self) -> float:
        return self.mean_pnl / self.total_staked if self.total_staked > 0 else 0.0

    @property
    def win_rate(self) -> float:
        """Fraction of simulated tournaments where the bet slate finished green."""
        if not self.pnl:
            return 0.0
        return sum(1 for p in self.pnl if p > 0) / len(self.pnl)

    def percentile(self, q: float) -> float:
        if not self.pnl:
            return 0.0
        s = sorted(self.pnl)
        return s[min(len(s) - 1, max(0, int(q * len(s))))]


def select_bets(
    comparisons: list[Comparison],
    bankroll: float,
    kelly_mult: float = 0.5,
    min_edge: float = 0.02,
) -> list[Bet]:
    """Pick positive-edge teams and size them with fractional Kelly.

    Stakes are scaled down proportionally if the combined Kelly fractions would
    exceed the bankroll, so the slate never stakes more than you have."""
    cands = [c for c in comparisons if c.edge >= min_edge and 0.0 < c.market_prob < 1.0]
    fracs = {c.team: kelly_fraction(c.model_prob, c.market_prob) * kelly_mult for c in cands}
    total = sum(fracs.values())
    if total > 1.0:
        fracs = {k: v / total for k, v in fracs.items()}

    bets: list[Bet] = []
    for c in cands:
        stake = bankroll * fracs[c.team]
        if stake <= 1e-9:
            continue
        bets.append(Bet(team=c.team, price=c.market_prob, model_prob=c.model_prob, stake=stake))
    bets.sort(key=lambda b: b.edge, reverse=True)
    return bets


def run_betting(
    sim: TournamentSimulator,
    event: PolyEvent,
    iterations: int,
    *,
    bankroll: float = 100.0,
    kelly_mult: float = 0.5,
    min_edge: float = 0.02,
    lineups: Optional[dict] = None,
    extras: Optional[dict] = None,
) -> BettingReport:
    """One Monte-Carlo pass that yields both our probabilities and, given the
    market prices, the realized P&L distribution of the selected value bets."""
    market = {ln.team: ln.yes_price for ln in event.matched}
    teams = list(market)
    if not teams:
        raise ValueError("no Polymarket teams mapped to our squads for this event")

    hits = {t: 0 for t in teams}
    per_iter: list[set] = []
    for _ in range(iterations):
        outcome = sim.run_once(lineups=lineups, extras=extras)
        won = {t for t in teams if settles(event.mode, t, outcome)}
        for t in won:
            hits[t] += 1
        per_iter.append(won)

    comparisons = [
        Comparison(team=t, market_prob=market[t], model_prob=hits[t] / iterations)
        for t in teams
    ]
    comparisons.sort(key=lambda c: c.edge, reverse=True)

    bets = select_bets(comparisons, bankroll, kelly_mult, min_edge)
    pnl = [
        sum((b.shares if b.team in won else 0.0) - b.stake for b in bets)
        for won in per_iter
    ]
    return BettingReport(
        iterations=iterations, event_title=event.title, mode=event.mode,
        bankroll=bankroll, comparisons=comparisons, bets=bets, pnl=pnl,
        unmatched=event.unmatched,
    )
