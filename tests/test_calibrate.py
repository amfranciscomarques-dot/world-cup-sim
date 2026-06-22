import json
from worldcup.calibrate import calculate_metrics
from worldcup.data_loader import load_world_cup

def test_calibration_baseline():
    # Load 2026 teams
    teams, _ = load_world_cup()

    # Use a small subset for pinning or the full curated set
    with open("data/historical/curated_wc.json", "r", encoding='utf-8') as f:
        fixtures = json.load(f)

    # Current model baseline (N=1000 for speed in tests, seed=42)
    res = calculate_metrics(fixtures, teams, n=1000, seed=42)

    # Baseline as of 2026-06-22 (approximate)
    # If the model changes significantly, this should fail.
    assert res.brier_1x2 < 0.65
    assert res.log_loss_1x2 < 1.10
    assert res.count > 90
