# Changelog

All notable changes to the World Cup Sim model and engine.

## [2026-06-22] - Model Accuracy Workstream

### Added
- **Calibration Harness:** Reproducible grader in `worldcup.calibrate` that scores the model on historical international football (WC 2014, 2018, 2022).
- **Historical Corpus:** Curated 134+ historical matches in `data/historical/` normalized to the current team schema.
- **Dixon–Coles Correlation:** Implemented `dc_adjust` in the engine with ρ = -0.1 to model low-scoreline dependence (0-0, 1-0, 0-1, 1-1).
- **Schedule-aware Fatigue:** The `fatigue` factor now derives `rest_days` from the real 2026 fixture calendar in `data/fixtures_2026.json`.
- **Match Dates:** `MatchResult` and `PlayedResult` now carry a `date` field to propagate timing data.

### Changed
- **Model Tuning:** Optimized constants via historical grid search:
    - `RATING_COEFF`: 0.035 → 0.030
    - `BASE_GOALS`: 1.35 (verified)
    - Results: Brier (1X2) 0.602 → 0.566, Log-loss 1.012 → 0.962 on the historical set.
- **Sampling:** `sample_score` updated to use rejection sampling for the joint Dixon-Coles distribution while maintaining marginal Poisson expectations.

## [Prior Changes]

- **Heat Factors:** Added H1–H6 causal chunks for weather impact betting analysis.
- **FIFA 2026 Tiebreakers:** Implemented official head-to-head points/GD/GF rules for the expansion format.
- **Web UI:** Launched browser-based dashboard and results management.
- **Initial Release:** Poisson engine with squad-aware factor system.
