# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -e ".[dev]"        # editable install + pytest/ruff/mypy; exposes the `worldcup` CLI
pytest                         # run the suite
pytest tests/test_engine.py::test_stronger_team_wins_more_often   # single test
ruff check src tests           # lint (config in pyproject; same gate as CI)
mypy -p worldcup               # type-check the typed core (presentation layer exempted)

# Run without installing (no pip needed): set PYTHONPATH to src/
PYTHONPATH=src python -m worldcup.cli odds -n 1000 --seed 42
```

CI (`.github/workflows/ci.yml`) runs ruff + mypy once and pytest across Python
3.10–3.13. `mypy` is **incrementally** adopted: the simulation core and
data/store layer are checked; the loose presentation/network modules are listed
under `[[tool.mypy.overrides]] ignore_errors` in `pyproject.toml` — remove one
from that list and fix what mypy reports to bring it under the gate.

CLI subcommands: `teams`, `squad <team>`, `match <home> <away>`, `tournament`,
`odds`, `bets`, `games`, `dashboard`, `html`, `tui`, `web`.
Pass `--seed` anywhere for reproducible runs.

```bash
PYTHONPATH=src python -m worldcup.cli web        # interactive browser UI; opens http://127.0.0.1:8000/
```

```bash
python scripts/update_results.py   # refresh data/results_2026.json (played scores)
python scripts/update_odds.py      # refresh data/odds_2026.json (market odds snapshot)
python scripts/update_sofascore.py # refresh data/sofascore_2026.json (player form ratings)
PYTHONPATH=src python -m worldcup.cli html -n 2000 --no-open   # build dashboard.html
```

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

A fifth, **presentation** layer sits on top of those for the dashboards:
`report.py` joins the three data sources — model (Monte Carlo), results so far,
and market (Polymarket) — into one plain serialisable dict (`build_report`);
`html.py` renders that dict to a single self-contained HTML page
(`render_dashboard`, the `html` command / TUI "Open HTML dashboard"). The market
side is read offline from an **odds snapshot** (`data/odds_2026.json` via
`odds_store.py`) so the page builds without network calls; `--live` refetches.
`report.build_report` takes `odds=None` gracefully (model-only: standings, title
odds and fixtures still render, market columns drop). `games.model_triple` is the
shared per-fixture Monte Carlo used by both `games`/`dashboard` and `report`.

There are **two interactive front ends over the same engine**, and they expose the
same feature set. `tui.py` is the terminal UI (`web`'s arrow-key sibling). `webapp.py`
is the browser UI (`web` command): a stdlib-only `http.server` whose JSON endpoints
each wrap the exact engine call a TUI screen makes (`/api/match`, `/api/match-odds`,
`/api/tournament`, `/api/title-odds`, `/api/squad` + `/api/lineups`, the live
`/api/games*`, `/api/dashboard-live`, `/api/bets`, the offline
`/api/report` + `/dashboard.html` which reuse `build_report`/`render_dashboard`,
and `/api/refresh` which re-snapshots the live data — see below).
The whole single-page app (HTML/CSS/JS) is one embedded string `_INDEX_HTML`; all
state lives server-side, the page just fetches. When you add a feature to one front
end, mirror it in the other.

The three offline snapshots can be refreshed from inside the running web UI (the
"Update data (live)" screen → `POST /api/refresh`), not just from the command line.
The fetch-and-write logic is **importable** — `data_loader.refresh_results()`,
`odds_store.refresh_odds()` and `sofascore_store.refresh_sofascore()` — so the
`scripts/update_*.py` CLIs, the web endpoint (and any future TUI screen) all share
one code path. Each takes the already-loaded `teams, tournament` and an optional
`log` callback for progress, and returns a summary dict. `/api/refresh` always
refreshes results (one fetch) and refreshes odds (`{"odds": true}`) / SofaScore
ratings (`{"sofa": true}`) only when asked — both slower, and the SofaScore fetch
is best-effort (a failure is reported, never fatal, and never wipes a good
snapshot). It then busts `_GAMES_CACHE` and echoes fresh `api_meta()` so the page
re-renders the new counts.

### The factor system is the core abstraction

"New variables" = new **factors**. A `Factor` (`factors/base.py`) is a small object
whose `adjust(ctx)` mutates the shared `MatchContext` — specifically each side's
`attack`/`defense` floats, which start at the team's base rating. The engine reads
only the final numbers and never knows which factors exist, so factors compose
freely. Add one by subclassing `Factor`, giving it a unique `name`, and decorating
with `@register` (puts it in `default_registry()`); or assemble a `FactorRegistry`
by hand and pass it to the simulators. Built-ins live in `factors/builtin.py`:
`lineup`, `home_advantage`, `fatigue`, `chemistry`, `condition`, `coaching`,
`intangibles`, `form`, `sofascore`.

`sofascore` is the **results-aware form factor**: it reads each side's average
SofaScore player rating across the games it has *actually played so far*
(`extras['sofa_rating']`, ~6.0–7.6) and shifts attack/defense by the gap from a
~6.7 baseline. The per-team ratings come from the offline SofaScore snapshot
(see below) via `sofascore_store.form_extras`, which is handed to the simulators
as `extras` so it applies to a team's *future* fixtures only — it is never
back-applied to the played-game grids that grade model vs market (no look-ahead).
Like `fatigue`/`form` it is opt-in: a team with no rating yet is untouched.

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
  (regenerated from the live Polymarket feed by `scripts/update_results.py`, or the web
  UI's "Update data" screen — both call `data_loader.refresh_results()`) lists
  already-played fixtures. `load_results()` reads it; `TournamentSimulator(results=...)`
  splits them by stage into `known_group` / `known_knockout` (keyed by team pair).
  `play_group` records group scores verbatim instead of simulating, and
  `play_knockout(..., known=...)` does the same for the bracket, so standings *and* the
  knockout run are real and only the remaining games are drawn. `worldcup
  odds`/`tournament` (and the TUI) use this by default — pass `--fresh` to ignore it and
  simulate the whole event from scratch. A missing file just means "simulate everything".
- **A played knockout tie needs a decidable winner.** The Polymarket score is just the
  90'+ET scoreline, so a tie that went to penalties looks level. `PlayedResult` carries
  optional `home_pens`/`away_pens` and a `winner` property (score, then penalties);
  `play_knockout` replays a known tie only when the winner is determinable (decisive
  score, or a recorded shootout) and otherwise *simulates* that tie — it never advances a
  side silently from a level score. `refresh_results` writes the score; add the
  `home_pens`/`away_pens` by hand (or via a future feed) to pin a shootout result.
- **The market side is also snapshotted offline.** `scripts/update_odds.py` (or the web
  UI's "Update data" screen — both call `odds_store.refresh_odds()`) writes
  `data/odds_2026.json` — the tournament-winner / reach / group futures plus every
  fixture's three-way market (live prices, and the pre-kickoff odds recovered from CLOB
  history for played games). `odds_store.load_odds()` reads it. This is what lets `html`
  build the model-vs-market dashboard with no network round-trips (CLOB history is one
  request per played-game side, so doing it live is slow). Regenerate it whenever you
  regenerate results.
- **In-tournament player form is snapshotted offline too.** `scripts/update_sofascore.py`
  (or the web UI's "Update data" screen — both call `sofascore_store.refresh_sofascore()`)
  writes `data/sofascore_2026.json`: each played fixture's average SofaScore player
  rating per side (with the per-player ratings kept for a future per-player granularity).
  `sofascore_store.load_sofascore()` reads it and `form_extras()` collapses it into the
  per-team `extras` the `sofascore` factor consumes. The live fetch (`worldcup.sofascore`,
  stdlib `urllib`) targets SofaScore's public JSON API and is **best-effort** — its
  unique-tournament/season IDs are discovered at runtime and a failed fetch leaves the
  snapshot untouched — so the committed snapshot is the source of truth the simulation
  reads. Regenerate it whenever you regenerate results, so the form reflects the new games.
- `current_standings` is computed by `tournament.standings_from_results` — real group
  tables from played results only (teams may have `played < 3`), distinct from
  `play_group` which simulates. The dashboard's group cards use it.
