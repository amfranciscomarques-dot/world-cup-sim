import argparse
import json
import math
import os
import random
from collections import Counter
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple

from worldcup.data_loader import load_world_cup
from worldcup.engine.match import MatchSimulator
from worldcup.engine.poisson import expected_goals, sample_score
from worldcup.models import Team

@dataclass
class GraderResult:
    brier_1x2: float
    log_loss_1x2: float
    brier_ou25: float
    rmse_goals: float
    chi2_scoreline: float
    count: int

def calculate_metrics(fixtures: List[Dict[str, Any]], teams: Dict[str, Team], n: int, seed: int) -> GraderResult:
    rng = random.Random(seed)
    simulator = MatchSimulator()

    total_brier_1x2 = 0.0
    total_log_loss_1x2 = 0.0
    total_brier_ou25 = 0.0
    total_sq_error_goals = 0.0

    # chi2 scorelines: track empirical vs model distribution
    score_empirical: Counter[Tuple[int, int]] = Counter()
    score_model_f: Dict[Tuple[int, int], float] = {}

    valid_fixtures = 0

    for f in fixtures:
        home_name = f["home"]
        away_name = f["away"]

        if home_name not in teams or away_name not in teams:
            continue

        valid_fixtures += 1
        home_team = teams[home_name]
        away_team = teams[away_name]

        # Empirical outcome
        hg_actual = f["home_goals"]
        ag_actual = f["away_goals"]
        outcome_actual = 1 if hg_actual > ag_actual else (2 if ag_actual > hg_actual else 0)
        ou25_actual = 1 if hg_actual + ag_actual > 2.5 else 0
        score_empirical[(hg_actual, ag_actual)] += 1

        # Model predictions
        # Note: In a real run, factors like fatigue would apply.
        # For calibration, we use the base ratings or the standard simulator flow.
        # We'll use simulator.get_xg which applies registered factors.
        # Since we want to calibrate the base model, we ensure factors that are
        # tournament-state dependent (like fatigue) are handled or ignored.

        counts = {1: 0, 0: 0, 2: 0}
        ou25_count = 0
        total_model_goals = 0.0

        # Pre-calculate xG
        # For historical calibration, we assume teams at their base rating.
        ctx = simulator._build_context(home_team, away_team, stage="group", neutral=True,
                                        home_lineup=None, away_lineup=None,
                                        home_extras=None, away_extras=None, meta=None)
        simulator.registry.apply(ctx)

        # Use the actual poisson.expected_goals
        h_xg = expected_goals(ctx.home.attack, ctx.away.defense)
        a_xg = expected_goals(ctx.away.attack, ctx.home.defense)

        for _ in range(n):
            h, a = sample_score(h_xg, a_xg, rng)
            res = 1 if h > a else (2 if a > h else 0)
            counts[res] += 1
            if h + a > 2.5:
                ou25_count += 1
            total_model_goals += (h + a)
            score_model_f[(h, a)] = score_model_f.get((h, a), 0.0) + (1.0 / n)

        p_home = counts[1] / n
        p_draw = counts[0] / n
        p_away = counts[2] / n
        p_ou25 = ou25_count / n

        # Brier 1X2
        o_h = 1 if outcome_actual == 1 else 0
        o_d = 1 if outcome_actual == 0 else 0
        o_a = 1 if outcome_actual == 2 else 0
        total_brier_1x2 += (p_home - o_h)**2 + (p_draw - o_d)**2 + (p_away - o_a)**2

        # Log-loss 1X2
        p_actual = p_home if outcome_actual == 1 else (p_draw if outcome_actual == 0 else p_away)
        total_log_loss_1x2 -= math.log(max(1e-15, p_actual))

        # Brier OU2.5
        total_brier_ou25 += (p_ou25 - ou25_actual)**2

        # RMSE goals
        total_sq_error_goals += ((total_model_goals / n) - (hg_actual + ag_actual))**2

    if valid_fixtures == 0:
        return GraderResult(0, 0, 0, 0, 0, 0)

    # Chi2
    chi2 = 0.0
    all_scores = set(score_empirical.keys()) | set(score_model_f.keys())
    for s in all_scores:
        obs = float(score_empirical[s])
        exp = score_model_f.get(s, 0.0)
        if exp > 0:
            chi2 += (obs - exp)**2 / exp

    return GraderResult(
        brier_1x2=total_brier_1x2 / valid_fixtures,
        log_loss_1x2=total_log_loss_1x2 / valid_fixtures,
        brier_ou25=total_brier_ou25 / valid_fixtures,
        rmse_goals=math.sqrt(total_sq_error_goals / valid_fixtures),
        chi2_scoreline=chi2,
        count=valid_fixtures
    )

def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    grade_parser = subparsers.add_parser("grade")
    grade_parser.add_argument("--n", type=int, default=5000)
    grade_parser.add_argument("--seed", type=int, default=42)
    grade_parser.add_argument("--historical", default="data/historical/curated_wc.json")
    grade_parser.add_argument("--current", default="data/results_2026.json")

    args = parser.parse_args()

    if args.command == "grade":
        with open(args.historical, "r", encoding='utf-8') as f:
            hist_fixtures = json.load(f)
        with open(args.current, "r", encoding='utf-8') as f:
            curr_data = json.load(f)
            curr_fixtures = curr_data["results"]

        fixtures = hist_fixtures + curr_fixtures

        # Load teams for ratings
        teams, _ = load_world_cup()

        res = calculate_metrics(fixtures, teams, args.n, args.seed)

        report = f"""# Calibration Report
- Matches: {res.count}
- N (Monte Carlo): {args.n}
- Seed: {args.seed}

| Metric | Score |
| :--- | :--- |
| Brier (1X2) | {res.brier_1x2:.5f} |
| Log-loss (1X2) | {res.log_loss_1x2:.5f} |
| Brier (O/U 2.5) | {res.brier_ou25:.5f} |
| Goal RMSE | {res.rmse_goals:.5f} |
| Scoreline χ² | {res.chi2_scoreline:.5f} |
"""
        print(report)

        os.makedirs("data/calibration", exist_ok=True)
        with open("data/calibration/latest.md", "w", encoding='utf-8') as f:
            f.write(report)

if __name__ == "__main__":
    main()
