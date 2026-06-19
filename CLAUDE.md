# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -e ".[dev]"        # editable install + pytest; exposes the `worldcup` CLI
pytest                         # run the suite
pytest tests/test_engine.py::test_stronger_team_wins_more_often   # single test

# Run without installing (no pip needed): set PYTHONPATH to src/
PYTHONPATH=src python -m worldcup.cli odds -n 1000 --seed 42
```

CLI subcommands: `teams`, `squad <team>`, `match <home> <away>`, `tournament`, `odds`.
Pass `--seed` anywhere for reproducible runs.

## Architecture

A simulation flows through four layers, each in its own subpackage under `src/worldcup/`:

1. **Data** (`data_loader.py`, `data/*.json`) → plain dataclasses in `models.py`
   (`Player`, `Team`, `Lineup`, `MatchResult`). `load_world_cup()` loads both JSON
   files and **cross-links** them: it sets each `Team.group` and raises if the draw
   references a team with no squad entry. Adding a team to the draw therefore
   requires a matching squad entry, and vice-versa.
2. **Factors** (`factors/`) → the extension point (see below).
3. **Engine** (`engine/`) → `MatchSimulator.simulate()` builds a `MatchContext`,
   runs the factor registry over it, then `poisson.py` turns the adjusted
   attack/defense into expected goals and draws a scoreline. Knockout ties resolve
   via extra time then a penalty shootout.
4. **Tournament** (`tournament/`) → `group_stage.py` (round-robin + FIFA tiebreakers),
   `knockout.py` (seeded single-elim bracket), and `simulator.py`
   (`TournamentSimulator` = one full run + `monte_carlo()` aggregation).

### The factor system is the core abstraction

"New variables" = new **factors**. A `Factor` (`factors/base.py`) is a small object
whose `adjust(ctx)` mutates the shared `MatchContext` — specifically each side's
`attack`/`defense` floats, which start at the team's base rating. The engine reads
only the final numbers and never knows which factors exist, so factors compose
freely. Add one by subclassing `Factor`, giving it a unique `name`, and decorating
with `@register` (puts it in `default_registry()`); or assemble a `FactorRegistry`
by hand and pass it to the simulators. Built-ins live in `factors/builtin.py`:
`lineup`, `home_advantage`, `fatigue`, `form`.

Note that **lineup selection is itself a factor**, not special-cased in the engine.
A `Team`'s base `rating` represents its strongest XI; the `lineup` factor shifts
attack/defense by the quality delta between the chosen lineup and `team.best_lineup()`.
The engine never requires a fixed squad size, so a squad can be any subset; in
practice every team now carries its full 26-man call-up (see below), which is what
gives the live engine's substitutions a real bench to draw from.

`lineups` / `extras` dicts passed to `TournamentSimulator` apply to **every** match
a team plays — that's how you model "team X rests its stars all tournament". `extras`
keys (e.g. `rest_days`, `form`) land in `TeamMatchState.extras` for factors to read.

## Things worth knowing before changing them

- **The knockout bracket follows the official FIFA 2026 format.** The Round of 32
  pairings are fixed by group-finishing position (`R32_MATCHES` in `knockout.py`),
  and the 8 third-placed qualifiers are slotted via FIFA's published allocation
  table (`data/third_place_allocation_2026.json`, Annex C — all 495 combinations).
  `build_official_bracket_2026()` arranges the 32 teams into leaf order, and
  `simulator.run_once` feeds them to `play_knockout(..., prearranged=True)`.
  `R32_LEAF_ORDER` is what makes neighbour-pairing + winner-collapse reproduce the
  official R16/QF/SF/Final match-number tree, so don't reorder it casually.
- `play_knockout` still supports the old balanced *performance seed* bracket when
  called with `prearranged=False` (the default) — used by unit tests on arbitrary
  fields. It requires a **power-of-two** field (32 for the World Cup) and raises otherwise.
- The Poisson model constants (`BASE_GOALS`, `RATING_COEFF`, xG clamps) live in
  `engine/poisson.py`. `RATING_COEFF` controls how much a rating-point edge shifts xG;
  retune it there, not in the match logic.
- Player/team ratings in `data/teams_2026.json` are **curated estimates**. Every team
  carries a full **26-man** call-up: a recognisable starting core with full FM
  attributes, plus depth players as lighter entries (name/pos/rating/club/age, no
  attributes — `fm_rating` falls back to `rating`). `scripts/add_full_squads.py`
  regenerates the depth additions and asserts each team is a clean 26. The draw is the
  verified final draw of 5 Dec 2025.
- `data/` is resolved relative to the repo root (`parents[2]` from `data_loader.py`); the
  layout matters if files move.
- **Simulations are results-aware as the tournament unfolds.** `data/results_2026.json`
  (regenerated from the live Polymarket feed by `scripts/update_results.py`) lists
  already-played fixtures. `load_results()` reads it; `TournamentSimulator(results=...)`
  keys them by team pair and `play_group` records those scores verbatim instead of
  simulating, so standings are real and only the remaining games are drawn. `worldcup
  odds`/`tournament` (and the TUI) use this by default — pass `--fresh` to ignore it and
  simulate the whole event from scratch. A missing file just means "simulate everything".
  Knockout results aren't injected yet (no knockout games played at time of writing); the
  loader already tags stage so that's the natural extension point.
