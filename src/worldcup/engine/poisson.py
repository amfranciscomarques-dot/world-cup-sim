"""Turning effective strengths into goals.

The model is a bivariate-independent Poisson: each side's expected goals (xG)
is an exponential function of its attack rating minus the opponent's defense
rating, anchored at a league-average baseline. Goals are then independent
Poisson draws. This is the standard, well-behaved approach for football Monte
Carlo — it yields realistic scorelines, natural draws, and sane tail behaviour.
"""

from __future__ import annotations

import math
import random

# League-average goals per team per match (both sides equal strength).
BASE_GOALS = 1.35
# Sensitivity of xG to a one-point rating edge. ~0.035 gives a ~24-point gap
# (e.g. 90 vs 66) an xG of ~3.1 against ~0.6 — a believable mismatch.
# Tuned on 2026-06-22 (N=1000, historical WC set): RATING_COEFF 0.035 -> 0.030.
# Brier: 0.60271 -> 0.56633, Log-loss: 1.01205 -> 0.96272.
RATING_COEFF = 0.030
# Keep xG in a sane band so extreme ratings can't produce absurd blowouts.
MIN_XG = 0.05
MAX_XG = 6.0


def expected_goals(attack: float, opponent_defense: float) -> float:
    """xG for a side with the given attack against the given opponent defense."""
    xg = BASE_GOALS * math.exp(RATING_COEFF * (attack - opponent_defense))
    return max(MIN_XG, min(MAX_XG, xg))


def sample_poisson(lam: float, rng: random.Random) -> int:
    """Draw a Poisson(lam) count using Knuth's algorithm (lam is small here)."""
    if lam <= 0:
        return 0
    target = math.exp(-lam)
    k = 0
    product = 1.0
    while True:
        product *= rng.random()
        if product <= target:
            return k
        k += 1


def sample_score(home_xg: float, away_xg: float, rng: random.Random) -> tuple[int, int]:
    return sample_poisson(home_xg, rng), sample_poisson(away_xg, rng)
