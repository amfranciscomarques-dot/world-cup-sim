"""The factors that ship by default.

Each is small, independent, and a worked example of how to write your own.
They are pure functions of the context plus their own constructor parameters,
so they're trivially unit-testable and safe to reuse across threads/iterations.
"""

from __future__ import annotations

from collections import Counter

from ..models import Lineup, Player
from .base import Factor, MatchContext, register


def club_clusters(players: list[Player]) -> dict[str, list[str]]:
    """Group players by club, keeping only clubs with two or more of them.

    These shared-club groups are where on-pitch chemistry comes from (e.g. the
    PSG core in Portugal's XI). Returns ``{club: [player names]}``.
    """
    by_club: dict[str, list[str]] = {}
    for p in players:
        if p.club:
            by_club.setdefault(p.club, []).append(p.name)
    return {club: names for club, names in by_club.items() if len(names) >= 2}


def club_pairs(players: list[Player]) -> int:
    """Total number of same-club pairs among ``players`` (chemistry links)."""
    counts = Counter(p.club for p in players if p.club)
    return sum(n * (n - 1) // 2 for n in counts.values())


def lineup_indices(lineup: Lineup) -> tuple[float, float]:
    """Collapse a lineup into (attack_index, defense_index) on the rating scale.

    Midfield contributes to both ends; goalkeeping only to defense. Missing
    position groups fall back to the lineup's overall mean so partial lineups
    still produce sensible numbers.
    """
    overall = lineup.mean_rating()

    def line_mean(pos: str) -> float:
        players = lineup.by_position(pos)
        return sum(p.rating for p in players) / len(players) if players else overall

    gk, def_, mid, fwd = line_mean("GK"), line_mean("DEF"), line_mean("MID"), line_mean("FWD")
    attack_index = 0.55 * fwd + 0.35 * mid + 0.10 * def_
    defense_index = 0.45 * def_ + 0.30 * gk + 0.25 * mid
    return attack_index, defense_index


@register
class LineupFactor(Factor):
    """Shift a team's strength based on the selected XI relative to its best XI.

    Selecting the strongest available lineup leaves the team at its base rating;
    resting/rotating players moves attack and defense in proportion to the
    quality drop (scaled by ``sensitivity``). A team with no chosen lineup is
    left untouched — its base rating already represents a full-strength side.
    """

    name = "lineup"

    def __init__(self, sensitivity: float = 0.6) -> None:
        self.sensitivity = sensitivity

    def adjust(self, ctx: MatchContext) -> None:
        for state in ctx.states():
            if state.lineup is None or not state.lineup.players:
                continue
            best = state.team.best_lineup(len(state.lineup.players))
            base_att, base_def = lineup_indices(best)
            cur_att, cur_def = lineup_indices(state.lineup)
            state.attack += self.sensitivity * (cur_att - base_att)
            state.defense += self.sensitivity * (cur_def - base_def)


@register
class HomeAdvantageFactor(Factor):
    """Boost the designated home side. No-op at neutral venues.

    Set ``ctx.neutral = True`` (the tournament does this for non-host group
    games) to disable. Host nations playing in their own country get the boost.
    """

    name = "home_advantage"

    def __init__(self, attack_bonus: float = 3.0, defense_bonus: float = 2.0) -> None:
        self.attack_bonus = attack_bonus
        self.defense_bonus = defense_bonus

    def adjust(self, ctx: MatchContext) -> None:
        if ctx.neutral:
            return
        ctx.home.attack += self.attack_bonus
        ctx.home.defense += self.defense_bonus


@register
class FatigueFactor(Factor):
    """Penalise short rest. Reads ``extras['rest_days']`` per team.

    A team with the reference number of rest days (default 4) is unaffected;
    fewer days erodes attack and defense, more days gives a small lift. Teams
    that don't specify rest days are untouched.
    """

    name = "fatigue"

    def __init__(self, reference_days: float = 4.0, per_day: float = 0.6, cap: float = 4.0) -> None:
        self.reference_days = reference_days
        self.per_day = per_day
        self.cap = cap

    def adjust(self, ctx: MatchContext) -> None:
        for state in ctx.states():
            rest = state.extras.get("rest_days")
            if rest is None:
                continue
            delta = max(-self.cap, min(self.cap, (rest - self.reference_days) * self.per_day))
            state.attack += delta
            state.defense += delta


@register
class ChemistryFactor(Factor):
    """Reward club-mates who play together in the same XI.

    Players who share a club have real on-pitch understanding, so each same-club
    *pair* in a side's lineup adds ``per_pair`` to that side's attack and defense,
    capped at ``cap``. A three-player club core is three pairs; a four-player core
    (e.g. Portugal's PSG block) is six. With no explicit lineup the team's best XI
    is used, so default simulations already feel the effect.
    """

    name = "chemistry"

    def __init__(self, per_pair: float = 0.6, cap: float = 4.0) -> None:
        self.per_pair = per_pair
        self.cap = cap

    def adjust(self, ctx: MatchContext) -> None:
        for state in ctx.states():
            lineup = state.lineup if (state.lineup and state.lineup.players) else state.team.best_lineup()
            bonus = min(self.cap, self.per_pair * club_pairs(lineup.players))
            if bonus:
                state.attack += bonus
                state.defense += bonus


@register
class ConditionFactor(Factor):
    """Match-day physical condition: injuries, illness, sharpness at kickoff.

    Distinct from :class:`FatigueFactor` (which models rest *between* games):
    this is how fit a side actually is when the whistle blows. Reads
    ``extras['condition']`` in ``[0, 1]`` per team, falling back to the team's
    baked ``team.condition`` (a disclosed injury burden). ``1.0`` is peak (no
    effect); lower values scale attack and defense down by ``scale`` rating
    points at zero. Teams at full fitness are untouched.
    """

    name = "condition"

    def __init__(self, scale: float = 12.0) -> None:
        self.scale = scale

    def adjust(self, ctx: MatchContext) -> None:
        for state in ctx.states():
            # A per-match override wins; otherwise fall back to the team's baked
            # fitness (disclosed injury burden). Either being absent => no effect.
            cond = state.extras.get("condition")
            if cond is None:
                cond = getattr(state.team, "condition", 1.0)
            if cond is None:
                continue
            cond = max(0.0, min(1.0, float(cond)))
            delta = (cond - 1.0) * self.scale
            state.attack += delta
            state.defense += delta


@register
class CoachFactor(Factor):
    """Manager quality and tactical posture.

    Reads ``state.team.coach`` (or an ``extras['coach']`` override) as
    ``{"skill": float, "bias": float}``:

    * ``skill`` — rating points the manager adds to *both* attack and defense
      (organisation, in-game management, set-pieces). Roughly ``[-3, 3]``.
    * ``bias`` in ``[-1, 1]`` — tactical tilt: ``+1`` all-out attack (attack up,
      defense down by ``tilt``), ``-1`` parks the bus. Net strength is unchanged;
      only the attack/defense split moves, so a gung-ho side both scores and
      concedes more.
    """

    name = "coaching"

    def __init__(self, tilt: float = 2.5) -> None:
        self.tilt = tilt

    def adjust(self, ctx: MatchContext) -> None:
        for state in ctx.states():
            coach = state.extras.get("coach") or getattr(state.team, "coach", None) or {}
            if not coach:
                continue
            skill = float(coach.get("skill", 0.0))
            bias = max(-1.0, min(1.0, float(coach.get("bias", 0.0))))
            state.attack += skill + bias * self.tilt
            state.defense += skill - bias * self.tilt


def age_xfactor(age: int, rating: float) -> tuple[float, float]:
    """Derive an intangible ``(mean, sigma)`` from a player's age and stature.

    The whole point of this curve is to separate the *knowable* drift from the
    *unknowable* swing:

    * ``mean`` is the directional expectation — what we think we know. A 41-year-
      old's legs are, on average, a small minus.
    * ``sigma`` is the genuine uncertainty — what no one knows. Experience,
      leadership and the dressing-room/morale effect can pull a match either
      way, so a talisman carries a *large spread*, not a large positive mean.

    Veterans (33+) get a modest negative mean that grows with age and a sigma
    that grows with both age and stature (``star``). Wonderkids (21-) are raw on
    average (small negative mean) but volatile (they can explode). Prime-age
    players are ``(0, small)``. Returns values on the rating scale.
    """
    if not age:
        return (0.0, 0.0)
    star = max(0.0, (rating - 78.0) / 12.0)   # 0 ~ squad filler, ~1 = superstar
    if age >= 33:
        over = age - 32
        mean = -0.17 * over
        sigma = 1.0 + 0.25 * over + 1.0 * star
    elif age <= 21:
        under = 22 - age
        mean = -0.20 * under
        sigma = 1.0 + 0.30 * under + 0.6 * star
    else:
        mean, sigma = 0.0, 0.4
    mean = max(-3.0, min(1.0, mean))
    sigma = max(0.0, min(5.0, sigma))
    return (round(mean, 2), round(sigma, 2))


def team_intangibles(players: list[Player], extras: dict | None = None) -> tuple[float, float]:
    """Combine per-player intangibles into a team ``(mean, sigma)``.

    Means add; variances add (independent shocks), so
    ``sigma_team = sqrt(sum sigma_i^2)``. Each player's input is an explicit
    ``player.xfactor`` ``{"mean", "sigma"}`` or, failing that, :func:`age_xfactor`.
    An optional ``extras['intangibles']`` adds a team-level shock. Shared by the
    factor and the TUI/CLI so the displayed numbers match what is simulated.
    """
    mean = 0.0
    var = 0.0
    for p in players:
        xf = p.xfactor or {}
        m = float(xf.get("mean", 0.0))
        s = float(xf.get("sigma", 0.0))
        if not (m or s) and p.age:
            m, s = age_xfactor(p.age, p.rating)
        mean += m
        var += s * s
    team_xf = (extras or {}).get("intangibles") or {}
    mean += float(team_xf.get("mean", 0.0))
    var += float(team_xf.get("sigma", 0.0)) ** 2
    return (mean, var ** 0.5)


@register
class IntangiblesFactor(Factor):
    """The "no one knows" factor: signed expectation plus genuine uncertainty.

    Some influences can't be pinned to a single number because they genuinely
    cut both ways — the textbook case is a 41-year-old talisman whose legs are a
    minus but whose experience and effect on team morale might be a big plus or
    a big minus on the night. We model each such influence as a *random
    variable*: a ``mean`` (the part we believe) and a ``sigma`` (the part nobody
    can call). Each match we draw one shock per side from the context RNG, so
    over a Monte Carlo run the average tends to ``mean`` while individual
    tournaments get the heavy tails — the iconic night and the anonymous one.

    Inputs combine independently across the XI: means add, variances add
    (``sigma_team = sqrt(sum sigma_i^2)``). Per-player input comes from an
    explicit ``player.xfactor`` (``{"mean", "sigma"}``) or, failing that, from
    :func:`age_xfactor`. A team may add its own ``extras['intangibles']`` shock.
    The drawn delta is clamped to ``±cap`` and applied to attack and defense
    (morale lifts or sinks the whole side).
    """

    name = "intangibles"

    def __init__(self, cap: float = 6.0) -> None:
        self.cap = cap

    def adjust(self, ctx: MatchContext) -> None:
        for state in ctx.states():
            lineup = state.lineup if (state.lineup and state.lineup.players) else state.team.best_lineup()
            mean, sigma = team_intangibles(lineup.players, state.extras)
            if mean == 0.0 and sigma == 0.0:
                continue
            delta = ctx.rng.gauss(mean, sigma)
            delta = max(-self.cap, min(self.cap, delta))
            state.attack += delta
            state.defense += delta


@register
class FormFactor(Factor):
    """Apply recent form. Reads ``extras['form']`` in [-1, 1] per team.

    +1 is red-hot, -1 is a slump, 0 (the default when unset) is neutral. Opt-in:
    teams without a form value are unaffected.
    """

    name = "form"

    def __init__(self, scale: float = 3.0) -> None:
        self.scale = scale

    def adjust(self, ctx: MatchContext) -> None:
        for state in ctx.states():
            form = state.extras.get("form")
            if not form:
                continue
            form = max(-1.0, min(1.0, float(form)))
            state.attack += self.scale * form
            state.defense += self.scale * form
