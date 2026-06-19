"""The match simulator: glue between the factor system and the Poisson model."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from ..factors import FactorRegistry, MatchContext, TeamMatchState, default_registry
from ..models import Lineup, MatchResult, Team
from . import live, poisson

# Group games allow draws; knockout games must produce a winner.
KNOCKOUT_STAGES = {"R32", "R16", "QF", "SF", "F"}


@dataclass
class MatchOdds:
    """Monte Carlo summary of a single fixture played many times.

    ``home_win``/``draw``/``away_win`` are decided by :attr:`MatchResult.winner`,
    so in a knockout stage they fold in extra time and penalties (``draw`` is then
    ~0 and a "win" means advancing). Goal/xG averages and the scoreline tally use
    the final score, which includes extra-time goals for knockout games.
    """

    home: str
    away: str
    iterations: int
    stage: str
    home_win: float
    draw: float
    away_win: float
    avg_home_goals: float
    avg_away_goals: float
    avg_home_xg: float
    avg_away_xg: float
    # Probability the match has more than 2.5 total goals (the "over").
    over25: float = 0.0
    # Probability both teams score at least one goal (BTTS "yes").
    btts: float = 0.0
    # Most common scorelines, each ((home_goals, away_goals), probability).
    scorelines: list[tuple[tuple[int, int], float]] = field(default_factory=list)
    # Per-player scoring, populated only when ``monte_carlo(track_scorers=True)``
    # (which runs the live engine). Each entry is
    # ``(player, goals_per_match, assists_per_match)``, ranked by goals then
    # assists. Empty when tracking is off / the aggregate engine is used.
    home_scorers: list[tuple[str, float, float]] = field(default_factory=list)
    away_scorers: list[tuple[str, float, float]] = field(default_factory=list)


class MatchSimulator:
    """Simulates a single match end to end.

    Construct once (optionally with a custom :class:`FactorRegistry`) and reuse
    across many matches / Monte Carlo iterations. Pass an explicit ``rng`` for
    reproducible runs.
    """

    def __init__(self, registry: Optional[FactorRegistry] = None, rng: Optional[random.Random] = None) -> None:
        self.registry = registry if registry is not None else default_registry()
        self.rng = rng or random.Random()

    def _build_context(
        self,
        home: Team,
        away: Team,
        *,
        stage: str,
        neutral: bool,
        home_lineup: Optional[Lineup],
        away_lineup: Optional[Lineup],
        home_extras: Optional[dict],
        away_extras: Optional[dict],
        meta: Optional[dict],
    ) -> MatchContext:
        return MatchContext(
            home=TeamMatchState.from_team(home, is_home=True, lineup=home_lineup, **(home_extras or {})),
            away=TeamMatchState.from_team(away, is_home=False, lineup=away_lineup, **(away_extras or {})),
            stage=stage,
            neutral=neutral,
            rng=self.rng,
            meta=meta or {},
        )

    def simulate(
        self,
        home: Team,
        away: Team,
        *,
        stage: str = "group",
        neutral: bool = True,
        home_lineup: Optional[Lineup] = None,
        away_lineup: Optional[Lineup] = None,
        home_extras: Optional[dict] = None,
        away_extras: Optional[dict] = None,
        meta: Optional[dict] = None,
    ) -> MatchResult:
        """Simulate one match. World Cup games default to ``neutral=True``; the
        home-advantage factor only fires when ``neutral`` is False (host games)."""
        ctx = self._build_context(
            home, away, stage=stage, neutral=neutral,
            home_lineup=home_lineup, away_lineup=away_lineup,
            home_extras=home_extras, away_extras=away_extras, meta=meta,
        )
        self.registry.apply(ctx)

        home_xg = poisson.expected_goals(ctx.home.attack, ctx.away.defense)
        away_xg = poisson.expected_goals(ctx.away.attack, ctx.home.defense)
        hg, ag = poisson.sample_score(home_xg, away_xg, self.rng)

        result = MatchResult(
            home=home.name, away=away.name, home_goals=hg, away_goals=ag,
            stage=stage, home_xg=home_xg, away_xg=away_xg,
        )

        if stage in KNOCKOUT_STAGES and hg == ag:
            self._resolve_knockout_tie(result, ctx, home_xg, away_xg)
        return result

    def simulate_live(
        self,
        home: Team,
        away: Team,
        *,
        stage: str = "group",
        neutral: bool = True,
        home_lineup: Optional[Lineup] = None,
        away_lineup: Optional[Lineup] = None,
        home_extras: Optional[dict] = None,
        away_extras: Optional[dict] = None,
        meta: Optional[dict] = None,
    ) -> MatchResult:
        """Simulate one match with the time-segmented engine (:mod:`live`).

        The pre-match strengths are computed exactly once via the factor registry
        (identical to :meth:`simulate`'s baseline), then the live engine layers
        progressive fatigue, automatic substitutions and — when ``meta`` carries a
        hot ``temperature`` — hydration breaks on top, segment by segment.

        With no fatigue and no subs this reduces to the aggregate model, so the
        two engines agree on average; the live one adds in-match dynamics and an
        event log (substitutions, cooling breaks) on the returned result.
        """
        ctx = self._build_context(
            home, away, stage=stage, neutral=neutral,
            home_lineup=home_lineup, away_lineup=away_lineup,
            home_extras=home_extras, away_extras=away_extras, meta=meta,
        )
        self.registry.apply(ctx)

        # Starting XI: an explicit lineup, else the team's strongest available.
        home_xi = (home_lineup.players if home_lineup and home_lineup.players
                   else home.best_lineup().players)
        away_xi = (away_lineup.players if away_lineup and away_lineup.players
                   else away.best_lineup().players)
        home_side = live.LiveSide.create(home, home_xi, ctx.home.attack, ctx.home.defense)
        away_side = live.LiveSide.create(away, away_xi, ctx.away.attack, ctx.away.defense)

        outcome = live.run_live_match(
            home_side, away_side, self.rng,
            knockout=stage in KNOCKOUT_STAGES,
            temperature=(meta or {}).get("temperature"),
        )

        # Pre-match (full-strength) expected goals, for reference/display.
        home_xg = poisson.expected_goals(ctx.home.attack, ctx.away.defense)
        away_xg = poisson.expected_goals(ctx.away.attack, ctx.home.defense)
        result = MatchResult(
            home=home.name, away=away.name,
            home_goals=outcome.home_goals, away_goals=outcome.away_goals,
            stage=stage, home_xg=home_xg, away_xg=away_xg,
            extra_time=outcome.extra_time,
            home_subs=outcome.home_subs, away_subs=outcome.away_subs,
            home_scorers=outcome.home_scorers, away_scorers=outcome.away_scorers,
            cooling_breaks=outcome.cooling_breaks,
        )

        # A knockout tie that survived extra time goes to penalties.
        if stage in KNOCKOUT_STAGES and result.home_goals == result.away_goals:
            result.penalties = True
            result.home_pens, result.away_pens = self._shootout(ctx)
        return result

    def monte_carlo(
        self,
        home: Team,
        away: Team,
        iterations: int,
        *,
        stage: str = "group",
        neutral: bool = True,
        home_lineup: Optional[Lineup] = None,
        away_lineup: Optional[Lineup] = None,
        home_extras: Optional[dict] = None,
        away_extras: Optional[dict] = None,
        meta: Optional[dict] = None,
        top_scorelines: int = 5,
        track_scorers: bool = False,
    ) -> MatchOdds:
        """Replay one fixture ``iterations`` times and tally the outcomes.

        Each iteration is an independent :meth:`simulate` call, so any random
        factors (intangibles, etc.) and the scoreline draw vary run to run.

        With ``track_scorers=True`` each iteration is run through the live engine
        instead, so the result also carries per-player goal/assist tallies (in
        :attr:`MatchOdds.home_scorers` / ``away_scorers``). That is slower — it
        plays every match out segment by segment — so it is opt-in.
        """
        if iterations < 1:
            raise ValueError("iterations must be >= 1")

        wins = draws = losses = 0
        over25 = btts = 0
        sum_hg = sum_ag = sum_hxg = sum_axg = 0.0
        scores: Counter = Counter()
        h_goals: Counter = Counter()
        h_assists: Counter = Counter()
        a_goals: Counter = Counter()
        a_assists: Counter = Counter()
        for _ in range(iterations):
            run = self.simulate_live if track_scorers else self.simulate
            r = run(
                home, away, stage=stage, neutral=neutral,
                home_lineup=home_lineup, away_lineup=away_lineup,
                home_extras=home_extras, away_extras=away_extras, meta=meta,
            )
            if track_scorers:
                for _m, scorer, assist in r.home_scorers:
                    h_goals[scorer] += 1
                    if assist:
                        h_assists[assist] += 1
                for _m, scorer, assist in r.away_scorers:
                    a_goals[scorer] += 1
                    if assist:
                        a_assists[assist] += 1
            winner = r.winner
            if winner == home.name:
                wins += 1
            elif winner == away.name:
                losses += 1
            else:
                draws += 1
            sum_hg += r.home_goals
            sum_ag += r.away_goals
            sum_hxg += r.home_xg
            sum_axg += r.away_xg
            if r.home_goals + r.away_goals > 2:  # > 2.5 goals
                over25 += 1
            if r.home_goals > 0 and r.away_goals > 0:
                btts += 1
            scores[(r.home_goals, r.away_goals)] += 1

        n = float(iterations)
        scorelines = [
            (score, count / n) for score, count in scores.most_common(top_scorelines)
        ]

        def rank(goals: Counter, assists: Counter) -> list[tuple[str, float, float]]:
            rows = [(name, goals[name] / n, assists[name] / n)
                    for name in set(goals) | set(assists)]
            rows.sort(key=lambda r: (r[1], r[2]), reverse=True)
            return rows

        return MatchOdds(
            home=home.name, away=away.name, iterations=iterations, stage=stage,
            home_win=wins / n, draw=draws / n, away_win=losses / n,
            avg_home_goals=sum_hg / n, avg_away_goals=sum_ag / n,
            avg_home_xg=sum_hxg / n, avg_away_xg=sum_axg / n,
            over25=over25 / n, btts=btts / n,
            scorelines=scorelines,
            home_scorers=rank(h_goals, h_assists) if track_scorers else [],
            away_scorers=rank(a_goals, a_assists) if track_scorers else [],
        )

    def _resolve_knockout_tie(self, result: MatchResult, ctx: MatchContext, home_xg: float, away_xg: float) -> None:
        """Add extra time (one third of regulation xG) then penalties if needed."""
        result.extra_time = True
        et_h = poisson.sample_poisson(home_xg / 3.0, self.rng)
        et_a = poisson.sample_poisson(away_xg / 3.0, self.rng)
        result.home_goals += et_h
        result.away_goals += et_a
        if result.home_goals != result.away_goals:
            return
        result.penalties = True
        result.home_pens, result.away_pens = self._shootout(ctx)

    def _shootout(self, ctx: MatchContext) -> tuple[int, int]:
        """Best-of-five then sudden death. Conversion edge scales with attack."""

        def conversion(state: TeamMatchState) -> float:
            return max(0.60, min(0.88, 0.70 + (state.attack - 78.0) / 200.0))

        ph, pa = conversion(ctx.home), conversion(ctx.away)
        h = a = 0
        for _ in range(5):
            h += self.rng.random() < ph
            a += self.rng.random() < pa
        while h == a:  # sudden death
            h += self.rng.random() < ph
            a += self.rng.random() < pa
        return h, a
