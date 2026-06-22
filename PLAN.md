# PLAN

Current development roadmap and guiding principles.

## Guiding Principles

1. **Accuracy is data, not code:** The engine is sound; most prediction error lives in curated ratings.
2. **New variables are factors:** Anything affecting match strength should be a `Factor`, not an engine special case.
3. **Zero Dependencies:** Pure standard library at runtime; dependencies for dev/calibration only.
4. **Grade against reality:** Every constant change must be justified by the [Calibration Harness](src/worldcup/calibrate.py).

## Near-term (Accuracy)

- [ ] **Automated Rotation:** Model lineup fatigue/rotation for teams already qualified after Matchday 2.
- [ ] **Confederation Bias:** Apply a scaling factor to ratings based on inter-confederation win rates in the historical set.
- [ ] **Hyperparameter Tuning:** Use the harness to fit factor scales (form, chemistry, coaching) using a cross-validation split.
- [ ] **Backfill FM Attributes:** Complete the 1-20 attribute profiles for all 1248 players to sharpen bench impact modeling.

## Mid-term (Features)

- [ ] **Official FIFA third-place slot-allocation table:** Complete the migration to the real fixed-slot table in `knockout.py`.
- [ ] **Live, time-decaying form:** Update team form dynamically based on results *during* a tournament simulation run.
- [ ] **Odds parity alerts:** Check Polymarket vs model for R16+ fixtures and flag significant deviations.

## Longer-term (Exploratory)

- [ ] **Player-level match events:** Track goalscorers, cards, and injuries during segments in the `live` engine.
- [ ] **In-tournament momentum:** Carry over morale shocks between a team's matches in a single Monte Carlo run.
- [ ] **Parallelised Monte Carlo:** Multiprocessing path for faster large-N simulations (with reproducible per-worker seeding).

---
*For technical details, see [DOCUMENTATION.md](DOCUMENTATION.md).*
