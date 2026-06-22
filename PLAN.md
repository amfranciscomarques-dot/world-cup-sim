# PLAN

Development roadmap and guiding principles. For shipped work see
[`CHANGELOG.md`](CHANGELOG.md); for the full reference see [`README.md`](README.md).

## Guiding Principles

1. **Accuracy is data, not code:** The engine is sound; most prediction error lives
   in curated ratings.
2. **New variables are factors:** Anything affecting match strength should be a
   `Factor`, not an engine special case.
3. **Zero dependencies:** Pure standard library at runtime; dependencies for
   dev/calibration only.
4. **Grade against reality:** Every constant change must be justified by the
   calibration harness (`python -m worldcup.calibrate grade`), which writes
   `data/calibration/latest.md`.

## Near-term (Accuracy)

- [ ] **Automated rotation:** Model lineup fatigue/rotation for teams already
  qualified after Matchday 2.
- [ ] **Confederation bias:** Apply a scaling factor to ratings based on
  inter-confederation win rates in the historical set.
- [ ] **Hyperparameter tuning:** Use the harness to fit factor scales (form,
  chemistry, coaching) on a cross-validation split.
- [ ] **Backfill FM attributes:** Complete the 1–20 attribute profiles for all 1248
  players to sharpen bench-impact modelling.

## Mid-term (Features)

- [ ] **Live, time-decaying form:** Update team form dynamically based on results
  *during* a tournament simulation run (the offline SofaScore factor already feeds
  future fixtures; this would make it intra-run).
- [ ] **Odds parity alerts:** Flag significant model-vs-Polymarket deviations for
  R16+ fixtures.
- [ ] **TUI "Update data" screen:** Mirror the web UI's `POST /api/refresh` in the
  terminal front end.

## Longer-term (Exploratory)

- [ ] **Player-level match events:** Track goalscorers, cards, and injuries during
  segments in the `live` engine.
- [ ] **In-tournament momentum:** Carry over morale shocks between a team's matches
  within a single Monte Carlo run.
- [ ] **Parallelised Monte Carlo:** Multiprocessing path for faster large-N runs
  (with reproducible per-worker seeding).

## Recently shipped

Moved to [`CHANGELOG.md`](CHANGELOG.md): the calibration harness + Dixon–Coles
correlation, schedule-aware fatigue, model retuning (`RATING_COEFF` 0.035 → 0.030),
the official FIFA third-place slot-allocation table, the SofaScore form factor, and
the bet tracker.
