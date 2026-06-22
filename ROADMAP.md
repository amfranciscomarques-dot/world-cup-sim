# World Cup Sim — Roadmap

Where the project is and where it could go next. This is a living document —
priorities shift as the 2026 tournament approaches and as the model is graded
against real results.

For how things work today, see [`DOCUMENTATION.md`](DOCUMENTATION.md).

---

## Status at a glance

| Area | State |
|------|:-----:|
| 48-team data on the verified final draw (5 Dec 2025) | ✅ Done |
| Full **26-man** squads for every team (1248 players) | ✅ Done |
| FM-attribute → rating derivation | ✅ Done |
| Poisson match engine + knockout (ET, penalties) | ✅ Done |
| Factor system + 8 built-in factors | ✅ Done |
| Live (time-segmented) engine: fatigue, subs, hydration | ✅ Done |
| Tournament Monte Carlo (stage/title odds) | ✅ Done |
| Polymarket odds comparison + Kelly betting + dashboard | ✅ Done |
| CLI + interactive TUI | ✅ Done |
| Test suite (65 tests) | ✅ Done |

The simulator is feature-complete and validated. What follows is **depth and
accuracy**, not missing pillars.

---

## Guiding principles

1. **Accuracy is data, not code.** The engine is sound; most prediction error
   lives in curated ratings. Prefer improvements that sharpen data over new
   mechanics.
2. **New variables are factors.** Anything new about *match strength* should be a
   `Factor`, not a special case in the engine.
3. **Everything stays reproducible and dependency-free.** Seeded runs, pure
   stdlib, plain-JSON data.
4. **Grade against reality.** As real World Cup games are played, the dashboard's
   Brier/log-loss is the scoreboard that decides what to tune.

---

## Near-term (next)

High-value, low-risk, mostly self-contained.

- [ ] **Backfill FM attributes for depth players.** The ~880 depth additions ride
      the `rating` fallback (no attribute card). Generating profiles via
      `generate_fm_attributes.py` would make every player inspectable in the TUI
      and let the live engine read real `Stamina` for bench legs.
- [ ] **Verify deep-bench accuracy for smaller federations.** Starting cores are
      solid; the 20th–26th slots for minor nations are plausible estimates. A pass
      against published call-ups would tighten them.
- [ ] **Rebalance position-heavy squads.** A few teams sit at the FWD-heavy end of
      the allowed band; nudge toward realistic 3/8/8/7-ish splits.
- [ ] **Per-player `Stamina` in the live engine end-to-end.** Once depth players
      have attributes, confirm bench `Stamina` actually feeds substitution timing.
- [ ] **Rating provenance note.** Document the source/vintage of each team's base
      rating so updates are auditable.

## Mid-term

Meaningful modelling upgrades; each is a contained change.

- [ ] **Official FIFA third-place slot-allocation table.** Replace performance
      seeding in `knockout.py` with the real fixed-slot table (gated behind a
      flag so both remain available). See the module docstring.
- [ ] **Correlated goals / Dixon–Coles adjustment.** The current model draws home
      and away goals independently; a low-score correlation term would sharpen
      draw and 1-0/0-0 frequencies.
- [ ] **Schedule-aware fatigue.** Drive the `fatigue` factor's `rest_days` from the
      actual fixture calendar (and travel) inside `TournamentSimulator`, rather
      than a static `extras` value.
- [ ] **Live, time-decaying form.** Update `extras['form']` from recent results
      during a tournament run instead of a fixed pre-set value.
- [x] **Weather / venue factors.** Altitude (Mexico City), humidity, and a real
      `temperature` per fixture feeding both the heat-break logic and a wet-pitch
      factor. → *Shipped `factors/heat.py` (H1–H6 + heat-pace multiplier in
      `engine/match.py`). Six reusable causal chunks from the WC heat-betting
      insight, plus a 16-venue table with 3 AC venues. Tests in
      `tests/test_heat.py` (28 cases). Opt-in per match via `meta={'venue',
      'wbgt_c'}`.*
- [ ] **More Polymarket market types.** Extend `betting.py` settlement beyond
      winner / reach-R16-QF / group-winner (e.g. top scorer, stage exits,
      head-to-heads) as markets appear.
- [x] **Calibration harness against real matches.** Track Brier score and
      log-loss against historical tournaments to justify constant changes.

## Longer-term / exploratory

Bigger bets — valuable but larger in scope.

- [x] Dixon–Coles shipped (ρ = -0.1).
- [ ] **Parameter fitting.** Fit `RATING_COEFF`, `BASE_GOALS`, and factor scales to
      historical match data instead of hand-tuning.
- [ ] **Player-level match events.** Goalscorers, cards, and injuries *during* a
      match (the live engine already has the time axis to host them).
- [ ] **In-tournament momentum / morale carryover** between a team's matches.
- [ ] **Persisted simulation runs** (cache Monte Carlo outputs; diff odds across
      data revisions).
- [ ] **Web/visual front-end** over the existing Python API (bracket view, odds
      charts) — the CLI/TUI stay the reference.
- [ ] **Parallelised Monte Carlo.** Independent iterations are embarrassingly
      parallel; a multiprocessing path would speed up large `-n` runs. (Note: the
      single shared `rng` would need per-worker seeding to stay reproducible.)

---

## Explicit non-goals

- **Live betting execution.** The Polymarket integration is read-only analysis;
  there is no intent to place real trades.
- **A heavyweight dependency stack.** Stdlib-only is a feature. New deps need a
  strong, isolated justification.
- **Bit-for-bit FIFA officialdom by default.** Performance seeding is a deliberate
  design choice; the official table is offered as an option, not the default.

---

## How to contribute a change

1. **A new strength variable?** Write a `Factor` (see
   [DOCUMENTATION → factor system](DOCUMENTATION.md#the-factor-system)). Don't
   touch the engine.
2. **A data fix?** Edit the JSON or the relevant `scripts/` generator, re-run it
   (they validate and are idempotent), and confirm `load_world_cup()` still loads
   cleanly.
3. **Always** keep `pytest` green and add a test for new behaviour.
4. **Tuning a constant?** Change it where it lives (see the
   [tuning reference](DOCUMENTATION.md#tuning-reference)) and, ideally, back it
   with a calibration number rather than taste.
