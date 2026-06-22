# Changelog

All notable changes to the World Cup Sim model and engine. See
[`README.md`](README.md) for the full reference and [`PLAN.md`](PLAN.md) for what's
next.

## [Unreleased]

### Added
- **Bet Tracker (`worldcup tracker`):** Record real-money bets in
  `data/user_bets_2026.json`, price every selection against the engine, and write a
  standalone `tracker.html` projecting each slip's ROI / P&L distribution via Monte
  Carlo. Selection types: `match_result`, `over25`, `under25`, `btts`, `player_goal`,
  `outright`; a slip settles only when every leg hits. Mirrored into the web UI.
- **Value-bet scanner (`scripts/value_bets_today.py`):** Scans today's fixtures and
  derives hundreds of derivative markets (1X2, multiple O/U lines, BTTS, exact score,
  Asian Handicap, Double Chance, Draw No Bet) from the model's joint scoreline
  distribution, ranked by edge with half-Kelly staking.

## [2026-06-22] — Model Accuracy Workstream

### Added
- **Calibration harness (`worldcup.calibrate`):** Reproducible grader that scores the
  model on historical international football (WC 2014/2018/2022) plus played 2026
  fixtures; writes `data/calibration/latest.md`.
- **Historical corpus:** 134 curated historical matches in `data/historical/`,
  normalised to the current team schema.
- **Dixon–Coles correlation:** `dc_adjust` in the engine with ρ = −0.1 to model
  low-scoreline dependence (0-0, 1-0, 0-1, 1-1) via rejection sampling that preserves
  the marginal Poisson expectations.
- **Schedule-aware fatigue:** The `fatigue` factor now derives `rest_days` from the
  real 2026 fixture calendar in `data/fixtures_2026.json`.
- **Match dates:** `MatchResult` and `PlayedResult` now carry a `date` field.

### Changed
- **Model tuning:** Optimised constants via historical grid search
  (`scripts/tune_model.py`):
  - `RATING_COEFF`: 0.035 → 0.030
  - `BASE_GOALS`: 1.35 (verified)
  - Effect (N=1000 historical set): Brier (1X2) 0.603 → 0.566, log-loss 1.012 → 0.963.
  - Latest full-corpus report (134 matches, N=5000): Brier 0.593, log-loss 0.996 —
    see `data/calibration/latest.md`.
- **Sampling:** `sample_score` uses rejection sampling for the joint Dixon–Coles
  distribution while keeping marginal Poisson expectations.

### Notes
- Results-aware update: Group G standings reflect the played fixtures (Belgium 1-1
  Egypt, Iran 2-2 New Zealand, Belgium 0-0 Iran). Group G is Belgium / Iran / Egypt /
  New Zealand — Brazil is in Group C and Germany in Group E in the official 2026 draw.

## [Earlier]

- **SofaScore form factor:** Results-aware in-tournament form — shifts a team's
  attack/defense by its average SofaScore player rating over games actually played
  (offline snapshot, future-fixtures-only, no look-ahead).
- **Official FIFA 2026 knockout bracket:** Fixed R32 pairings by group position plus
  the published third-place allocation table (Annex C, all 495 combinations).
- **Heat factors (H1–H6):** Per-venue WBGT model and a global heat goal brake for
  weather-impact betting analysis.
- **FIFA 2026 tiebreakers:** Official head-to-head points/GD/GF rules for the
  48-team expansion format.
- **Offline market & form snapshots:** `odds_2026.json` and `sofascore_2026.json`,
  refreshable from the CLI or the web UI's "Update data" screen.
- **Web UI:** Browser-based dashboard and live results management (sibling of the TUI).
- **Live (time-segmented) engine:** Progressive fatigue, auto-coach substitutions,
  and hydration/cooling breaks.
- **Initial release:** Poisson engine with the squad-aware pluggable factor system.
