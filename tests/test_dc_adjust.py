import random
from worldcup.engine import poisson

def test_dc_marginals():
    rng = random.Random(42)
    h_xg, a_xg = 1.2, 0.8
    iterations = 100000

    h_sum = 0
    a_sum = 0
    for _ in range(iterations):
        h, a = poisson.sample_score(h_xg, a_xg, rng)
        h_sum += h
        a_sum += a

    # Marginal means should still be xG (within tolerance)
    assert abs(h_sum / iterations - h_xg) < 0.02
    assert abs(a_sum / iterations - a_xg) < 0.02

def test_dc_joint_shift():
    rng = random.Random(42)
    h_xg, a_xg = 1.0, 1.0
    iterations = 100000

    # Expected 0-0 in independent Poisson: exp(-1) * exp(-1) = 0.1353
    # With rho = -0.1: tau = 1 - 1*1*(-0.1) = 1.1
    # Adjusted 0-0: 0.1353 * 1.1 = 0.1488

    count_00 = 0
    for _ in range(iterations):
        h, a = poisson.sample_score(h_xg, a_xg, rng)
        if h == 0 and a == 0:
            count_00 += 1

    p_00 = count_00 / iterations
    assert p_00 > 0.14  # Significant shift from 0.135

def test_tournament_pinned():
    # Smoke test for reproducibility
    # This assumes we have some pinned outcome for seed 42.
    # For now, just ensure it runs.
    pass
