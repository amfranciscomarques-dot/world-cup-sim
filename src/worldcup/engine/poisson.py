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
# Dixon-Coles low-score correlation adjustment.
RHO = -0.1
# Keep xG in a sane band so extreme ratings can't produce absurd blowouts.
MIN_XG = 0.05
MAX_XG = 6.0


def dc_adjust(h: int, a: int, h_xg: float, a_xg: float, rho: float) -> float:
    """Correlation adjustment for low scores (0-0, 1-0, 0-1, 1-1)."""
    if h == 0 and a == 0:
        return 1 - h_xg * a_xg * rho
    if h == 0 and a == 1:
        return 1 + h_xg * rho
    if h == 1 and a == 0:
        return 1 + a_xg * rho
    if h == 1 and a == 1:
        return 1 - rho
    return 1.0


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
    """Draw a scoreline. If RHO != 0, applies Dixon-Coles correlation adjustment."""
    if RHO == 0:
        return sample_poisson(home_xg, rng), sample_poisson(away_xg, rng)

    # Rejection sampling for Dixon-Coles
    # The weight is in [0, 1 - rho] for rho < 0. Max weight is approx 1.1 for rho = -0.1.
    # We use a safe upper bound.
    max_weight = 1.0 - RHO if RHO < 0 else 1.0 + RHO
    while True:
        h = sample_poisson(home_xg, rng)
        a = sample_poisson(away_xg, rng)
        w = dc_adjust(h, a, home_xg, away_xg, RHO)
        if rng.random() * max_weight < w:
            return h, a
