"""Time-segmented ("live") match dynamics: fatigue, substitutions, hydration.

The default :class:`~worldcup.engine.match.MatchSimulator.simulate` resolves a
match as a *single* expected-goals draw per side — fast, and all it needs for
big Monte Carlo runs. But a single draw has no notion of *time*, so there is
nowhere for in-match events to live.

This module adds that missing time axis. A match is split into five-minute
segments; over those segments three things happen that the aggregate model
cannot express:

* **Progressive fatigue** — every on-pitch player tires from kickoff to the
  final whistle, and tires *faster* late on (a mildly convex curve). Lower
  ``Stamina`` players fade quicker. Fatigue lowers a player's effective rating,
  which drags the side's attack/defense down as the game wears on.
* **Substitutions (auto-coach)** — at realistic windows (half-time, ~60', ~75',
  and once in extra time) the coach replaces the weakest/most-tired outfielder
  with the best *fresh* bench player of the same position, up to five subs. A
  deep bench (a full 26-man squad) therefore matters.
* **Hydration / cooling breaks** — in hot conditions FIFA mandates a short
  break around 30' and 75'. We model them as partial fatigue recovery, so a
  side with a tired XI in the heat gets some legs back.

Everything *pre-match* (home advantage, coaching, chemistry, intangibles, form,
between-games fatigue, …) is still computed once by the normal factor registry;
this module only layers the in-match dynamics on top of that baseline, so the
two models agree exactly when no one tires and no subs are made.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from typing import Optional

from ..factors.builtin import lineup_indices
from ..models import Lineup, Player, Team
from . import poisson

# --- Tunable constants -----------------------------------------------------

SEGMENT_MIN = 5                 # minutes resolved per simulation step
REG_SEGMENTS = 18               # 90' of regulation
ET_SEGMENTS = 6                 # 30' of extra time (knockout ties)

# How a fatigue-driven drop in lineup quality translates to attack/defense,
# mirroring LineupFactor.sensitivity so the two stay consistent.
FATIGUE_SENS = 0.6
# Rating points an average-stamina player loses by minute 90 at full tilt.
FATIGUE_MAX_POINTS = 7.0
# Convexity of the fatigue curve (>1 => tire faster late). 1.0 = linear.
FATIGUE_EXPONENT = 1.3

# Hydration / cooling breaks.
HOT_THRESHOLD_C = 30.0          # air temperature at/above which breaks happen
HYDRATION_BREAK_MINUTES = (30, 75, 105)
HYDRATION_RECOVERY_MIN = 10.0   # fatigue-minutes refunded per break

# Substitutions. Windows mirror how managers actually act: the first changes
# around the hour, more past 75', plus an extra-time window. (Must be multiples
# of SEGMENT_MIN to land on a segment boundary.)
SUB_WINDOWS = (60, 75, 90, 105)
MAX_SUBS = 5
MAX_SUB_WINDOWS = 5
SUBS_PER_WINDOW = 2
# A fresh bench player must beat the on-pitch player's *current* (fatigued)
# rating by at least this margin before the coach bothers swapping.
SUB_IMPROVEMENT = 1.5

DEFAULT_STAMINA = 13.0          # 1-20 scale, used when no Stamina attribute

# Goal attribution. When a goal is scored we credit one on-pitch scorer and
# (usually) one assister, drawn by weight = position weight x current rating, so
# forwards score most and the best players feature most. Assists skew to
# midfield. These only colour the event log; they never affect who wins.
SCORER_POS_WEIGHT = {"FWD": 1.0, "MID": 0.45, "DEF": 0.12, "GK": 0.0}
ASSIST_POS_WEIGHT = {"FWD": 0.6, "MID": 1.0, "DEF": 0.35, "GK": 0.03}
# Fraction of goals credited with no assist (solo runs, penalties, rebounds).
NO_ASSIST_PROB = 0.22


def stamina_factor(player: Player) -> float:
    """Per-player fatigue multiplier from the FM ``Stamina`` attribute.

    Average stamina (13/20) gives 1.0; a 18-stamina engine fades ~25% slower, an
    8-stamina player ~25% faster. Players with no attributes use the default.
    """
    stamina = float(player.attributes.get("Stamina", DEFAULT_STAMINA)) if player.attributes else DEFAULT_STAMINA
    return max(0.5, 1.0 + (DEFAULT_STAMINA - stamina) * 0.05)


def fatigue_points(fatigue_min: float, sfac: float) -> float:
    """Rating points lost given accumulated fatigue-minutes and a stamina factor.

    The curve is convex (``FATIGUE_EXPONENT`` > 1): the last twenty minutes cost
    more than the first twenty, which is why late subs swing tight games.
    """
    frac = max(0.0, fatigue_min) / 90.0
    return FATIGUE_MAX_POINTS * (frac ** FATIGUE_EXPONENT) * sfac


@dataclass
class OnPitch:
    """A player currently on the field plus their accumulated fatigue."""

    player: Player
    fatigue_min: float = 0.0
    sfac: float = 1.0

    def current_rating(self) -> float:
        return max(20.0, self.player.rating - fatigue_points(self.fatigue_min, self.sfac))

    def fatigued_player(self) -> Player:
        """A copy of the player at their current (fatigued) rating for indexing."""
        return replace(self.player, rating=self.current_rating())


def _weighted_pick(
    on_pitch: list["OnPitch"],
    weights: dict[str, float],
    rng: random.Random,
    exclude: Optional["OnPitch"] = None,
) -> Optional["OnPitch"]:
    """Pick one on-pitch player with probability ~ position weight x rating."""
    pool = [op for op in on_pitch if op is not exclude]
    w = [max(0.0, weights.get(op.player.pos, 0.0)) * op.current_rating() for op in pool]
    total = sum(w)
    if total <= 0:
        return None
    r = rng.random() * total
    upto = 0.0
    for op, wi in zip(pool, w):
        upto += wi
        if r <= upto:
            return op
    return pool[-1]


def bench_by_pos(team: Team, starting: list[Player]) -> dict[str, list[Player]]:
    """Bench (squad minus starters) grouped by position, best-rated first."""
    started = {p.name for p in starting}
    bench: dict[str, list[Player]] = {}
    for p in team.squad:
        if p.name in started:
            continue
        bench.setdefault(p.pos, []).append(p)
    for plist in bench.values():
        plist.sort(key=lambda p: p.rating, reverse=True)
    return bench


@dataclass
class LiveSide:
    """Mutable in-match state for one team."""

    team: Team
    base_attack: float          # pre-match attack from the factor registry
    base_defense: float         # pre-match defense from the factor registry
    on_pitch: list[OnPitch]
    bench: dict[str, list[Player]]
    start_att_idx: float        # lineup_indices of the starting XI (unfatigued)
    start_def_idx: float
    subs: list[tuple[int, str, str]] = field(default_factory=list)  # (minute, off, on)
    scorers: list[tuple[int, str, str]] = field(default_factory=list)  # (minute, scorer, assist)
    windows_used: int = 0

    @classmethod
    def create(cls, team: Team, starting: list[Player], base_attack: float, base_defense: float) -> "LiveSide":
        start_att, start_def = lineup_indices(Lineup(team.name, starting))
        return cls(
            team=team, base_attack=base_attack, base_defense=base_defense,
            on_pitch=[OnPitch(p, sfac=stamina_factor(p)) for p in starting],
            bench=bench_by_pos(team, starting),
            start_att_idx=start_att, start_def_idx=start_def,
        )

    def strengths(self) -> tuple[float, float]:
        """Current (attack, defense), baseline adjusted for on-pitch fatigue.

        Compares the current (fatigued) XI's attack/defense indices to the
        starting XI's; the drop, scaled by ``FATIGUE_SENS``, is subtracted from
        the pre-match baseline. Equal indices (kickoff, no fatigue) => baseline.
        """
        cur_att, cur_def = lineup_indices(Lineup(self.team.name, [op.fatigued_player() for op in self.on_pitch]))
        return (
            self.base_attack + FATIGUE_SENS * (cur_att - self.start_att_idx),
            self.base_defense + FATIGUE_SENS * (cur_def - self.start_def_idx),
        )

    def credit_goal(self, minute: int, rng: random.Random) -> None:
        """Attribute one goal to an on-pitch scorer and (usually) an assister."""
        scorer = _weighted_pick(self.on_pitch, SCORER_POS_WEIGHT, rng)
        if scorer is None:  # only goalkeepers on the pitch — vanishingly unlikely
            scorer = max(self.on_pitch, key=lambda op: op.current_rating())
        assist = None
        if rng.random() > NO_ASSIST_PROB:
            assist = _weighted_pick(self.on_pitch, ASSIST_POS_WEIGHT, rng, exclude=scorer)
        self.scorers.append(
            (minute, scorer.player.name, assist.player.name if assist else "")
        )

    def hydration_recovery(self) -> None:
        """Refund some fatigue to every on-pitch player at a cooling break."""
        for op in self.on_pitch:
            op.fatigue_min = max(0.0, op.fatigue_min - HYDRATION_RECOVERY_MIN)

    def make_subs(self, minute: int) -> None:
        """Auto-coach: swap up to ``SUBS_PER_WINDOW`` tired outfielders for the
        best fresh same-position bench player, within the overall sub budget."""
        if self.windows_used >= MAX_SUB_WINDOWS or len(self.subs) >= MAX_SUBS:
            return
        made_this_window = 0
        while made_this_window < SUBS_PER_WINDOW and len(self.subs) < MAX_SUBS:
            # Weakest on-pitch outfielder by *current* rating (keepers stay on).
            candidates = [op for op in self.on_pitch if op.player.pos != "GK"]
            if not candidates:
                break
            weakest = min(candidates, key=lambda op: op.current_rating())
            options = self.bench.get(weakest.player.pos) or []
            if not options:
                break
            replacement = options[0]
            if replacement.rating < weakest.current_rating() + SUB_IMPROVEMENT:
                break  # no bench upgrade worth making this window
            # Execute the swap.
            self.bench[weakest.player.pos].pop(0)
            self.on_pitch.remove(weakest)
            self.on_pitch.append(OnPitch(replacement, sfac=stamina_factor(replacement)))
            self.subs.append((minute, weakest.player.name, replacement.name))
            made_this_window += 1
        if made_this_window:
            self.windows_used += 1


def _advance_fatigue(side: LiveSide) -> None:
    for op in side.on_pitch:
        op.fatigue_min += SEGMENT_MIN


@dataclass
class LiveOutcome:
    home_goals: int
    away_goals: int
    home_subs: list[tuple[int, str, str]]
    away_subs: list[tuple[int, str, str]]
    home_scorers: list[tuple[int, str, str]]
    away_scorers: list[tuple[int, str, str]]
    cooling_breaks: int
    extra_time: bool


def run_live_match(
    home: LiveSide,
    away: LiveSide,
    rng: random.Random,
    *,
    knockout: bool = False,
    temperature: Optional[float] = None,
) -> LiveOutcome:
    """Play the segmented match and return goals plus the in-match event log.

    Regulation is 18 five-minute segments. For a knockout tie, extra time adds
    six more segments (fatigue keeps climbing). Penalties, if still level, are
    resolved by the caller — this function only produces open-play goals.
    """
    hot = temperature is not None and temperature >= HOT_THRESHOLD_C
    goals = {"home": 0, "away": 0}
    cooling = {"n": 0}
    share = SEGMENT_MIN / 90.0    # each segment is this fraction of a full match

    def play_segment(minute: int) -> None:
        # Events happen at the segment boundary, before the next passage of play.
        if minute in SUB_WINDOWS:
            home.make_subs(minute)
            away.make_subs(minute)
        if hot and minute in HYDRATION_BREAK_MINUTES:
            home.hydration_recovery()
            away.hydration_recovery()
            cooling["n"] += 1
        _advance_fatigue(home)
        _advance_fatigue(away)
        h_att, h_def = home.strengths()
        a_att, a_def = away.strengths()
        hg = poisson.sample_poisson(poisson.expected_goals(h_att, a_def) * share, rng)
        ag = poisson.sample_poisson(poisson.expected_goals(a_att, h_def) * share, rng)
        # Spread each goal across the five minutes of the segment, then credit a
        # scorer/assister off the XI on the pitch at that moment (post-subs).
        for _ in range(hg):
            home.credit_goal(minute + rng.randint(1, SEGMENT_MIN), rng)
        for _ in range(ag):
            away.credit_goal(minute + rng.randint(1, SEGMENT_MIN), rng)
        goals["home"] += hg
        goals["away"] += ag

    # Regulation: absolute minutes 0, 5, ... 85.
    for seg in range(REG_SEGMENTS):
        play_segment(seg * SEGMENT_MIN)

    extra_time = False
    if knockout and goals["home"] == goals["away"]:
        extra_time = True
        for seg in range(ET_SEGMENTS):   # extra time continues at 90, 95, ... 115
            play_segment(90 + seg * SEGMENT_MIN)

    hg, ag = goals["home"], goals["away"]
    return LiveOutcome(
        home_goals=hg, away_goals=ag,
        home_subs=home.subs, away_subs=away.subs,
        home_scorers=home.scorers, away_scorers=away.scorers,
        cooling_breaks=cooling["n"], extra_time=extra_time,
    )
