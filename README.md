# World Cup Sim

A modular Monte Carlo simulator for the **2026 FIFA World Cup** (48 teams, 12
groups, Round of 32 → Final). It runs on the verified final draw (5 Dec 2025)
and curated 26-man squads, resolves matches with a Dixon–Coles-calibrated
Poisson goals model, and lets you add new "variables" — home advantage, fatigue,
weather, anything — as small plug-in **factors** that compose without touching
the engine. As the tournament unfolds it is **results-aware**: it reads the games
already played and simulates only what's left.

No third-party runtime dependencies — pure standard library. Requires Python 3.10+.

> **Companion docs:** [`CHANGELOG.md`](CHANGELOG.md) for model/engine history,
> [`PLAN.md`](PLAN.md) for the roadmap, [`CLAUDE.md`](CLAUDE.md) for contributor
> conventions and gotchas. This README is the complete reference.

---

## Contents

1. [Quick start](#quick-start)
2. [Architecture overview](#architecture-overview)
3. [The data layer](#the-data-layer)
4. [Domain models](#domain-models)
5. [Player ratings (FM attributes)](#player-ratings-fm-attributes)
6. [The factor system](#the-factor-system)
7. [Built-in factors](#built-in-factors)
8. [The match engine](#the-match-engine)
9. [The live (time-segmented) engine](#the-live-time-segmented-engine)
10. [The tournament layer](#the-tournament-layer)
11. [Results awareness](#results-awareness)
12. [Polymarket: odds, betting & the bet tracker](#polymarket-odds-betting--the-bet-tracker)
13. [Calibration harness](#calibration-harness)
14. [The CLI](#the-cli)
15. [Interactive front ends (TUI & web)](#interactive-front-ends-tui--web)
16. [Python API cookbook](#python-api-cookbook)
17. [Testing](#testing)
18. [Tuning reference](#tuning-reference)

---

## Quick start

```bash
pip install -e ".[dev]"          # editable install + pytest/ruff/mypy; exposes the `worldcup` CLI
pytest                           # run the suite
worldcup web                     # interactive browser UI at http://127.0.0.1:8000/

# Run without installing — just put src/ on the path:
PYTHONPATH=src python -m worldcup.cli odds -n 1000 --seed 42
```

Pass `--seed` anywhere for reproducible runs.

On Windows there are double-click launchers at the repo root: **`Launch TUI.bat`**,
**`Open Web UI.bat`**, **`Quick Odds.bat`**, **`Simulate Tournament.bat`**,
**`Open Dashboard.bat`**, **`Open Tracker.bat`**, **`Refresh Data.bat`**.

---

## Architecture overview

A simulation flows through four layers, each in its own subpackage under
`src/worldcup/`:

```
  data/*.json ─► data_loader ─► models ─► factors ─► engine ─► tournament
   (squads,      (load +       (Player,   (adjust   (Poisson   (groups +
    draw)         cross-link)   Team…)     attack/    xG →       knockout +
                                           defense)   goals)     Monte Carlo)
```

1. **Data** (`data_loader.py`, `data/*.json`) → plain dataclasses in `models.py`.
   `load_world_cup()` loads both JSON files and **cross-links** them: it sets each
   `Team.group` and raises if the draw references a team with no squad entry.
2. **Factors** (`factors/`) → the extension point. Each factor mutates a shared
   `MatchContext`, nudging each side's effective `attack`/`defense`.
3. **Engine** (`engine/`) → `MatchSimulator.simulate()` builds a context, runs the
   factor registry over it, then `poisson.py` turns the adjusted attack/defense
   into expected goals and draws a scoreline. Knockout ties resolve via extra
   time then a penalty shootout. `engine/live.py` adds an optional time axis.
4. **Tournament** (`tournament/`) → `group_stage.py` (round-robin + FIFA
   tiebreakers), `knockout.py` (official FIFA 2026 bracket), and `simulator.py`
   (`TournamentSimulator` = one full run + `monte_carlo()` aggregation).

A fifth **presentation** layer sits on top for the dashboards: `report.py` joins
the three data sources (model Monte Carlo, results so far, market odds) into one
serialisable dict; `html.py` renders it to a single self-contained HTML page.

The key design property: **the engine reads only the final attack/defense numbers
and never knows which factors exist**, so factors compose freely.

---

## The data layer

JSON files under `data/` drive everything:

| File | What it holds |
|------|---------------|
| `teams_2026.json` | The 48 teams: base `rating`, full **26-man** squads, per-player attributes/club/age, optional `coach`, `condition`, `_injuries`. |
| `tournament_2026.json` | The format rules and the verified final group draw (5 Dec 2025), plus host team names. |
| `teams_2026.baseline.json` | The pre-attribute snapshot of the squads (kept for reference / regeneration). |
| `fixtures_2026.json` | The real match calendar (dates per fixture) that drives schedule-aware fatigue. |
| `third_place_allocation_2026.json` | FIFA Annex C — all 495 third-place-qualifier slot combinations. |
| `results_2026.json` | Played fixtures and scores (offline snapshot from Polymarket). |
| `odds_2026.json` | Market odds snapshot (winner/reach/group futures + per-fixture three-way). |
| `sofascore_2026.json` | Per-fixture average SofaScore player ratings per side. |
| `user_bets_2026.json` | Your recorded real-money bets (the bet tracker). |
| `historical/` | Curated historical corpus (WC 2014/2018/2022) for the calibration harness. |
| `calibration/latest.md` | The most recent calibration report — **auto-generated** by the harness. |

`data/` is resolved relative to the repo root (`parents[2]` from
`data_loader.py`), so the layout matters if files move.

**Loading and cross-linking** (`load_world_cup`):

```python
from worldcup.data_loader import load_world_cup
teams, tournament = load_world_cup()   # ({name: Team}, TournamentDef)
```

- Each `Team.group` is filled in from the draw.
- If the draw names a team with no squad entry, loading **raises** — adding a team
  to the draw therefore requires a matching squad entry, and vice-versa.
- When a player carries an `attributes` block, their `rating` is **recomputed**
  from it (see [Player ratings](#player-ratings-fm-attributes)); otherwise the
  curated `rating` field is used verbatim.

Current data: **48 teams, 12 groups, 1248 players** (every team a clean 26).

### Squad composition

Every nation carries a full **26-man** call-up:

- A recognisable **starting core** with full FM attributes (Mental/Physical/
  Technical groups on the 1–20 scale).
- **Depth players** as lighter entries — name/pos/rating/club/age, no attributes.
  `fm_rating` falls back to `rating` for these, so they work everywhere.

`scripts/add_full_squads.py` regenerates the depth additions and **asserts each
team is a clean 26** before writing. Position balance is guaranteed sane (3 GK
plus a real bench at every outfield position), which is what gives the live
engine's substitutions a real bench to draw from for every team.

> Player and team ratings are **curated estimates**. The starting cores and major
> nations are accurate; the deepest bench slots for some smaller federations are
> plausible estimates. Replace any of it with your own source for sharper
> predictions — everything downstream adapts automatically.

### Data-generation & refresh scripts

All under `scripts/`, each idempotent and re-runnable:

| Script | Purpose |
|--------|---------|
| `add_full_squads.py` | Expand every team to a clean 26-man call-up; validates before writing. |
| `generate_fm_attributes.py` | Seed FM attribute profiles from curated ratings. |
| `add_clubs.py` | Attach each player's 2025/26 club (drives chemistry). |
| `add_intangibles.py` | Populate ages and manager (`coach`) profiles. |
| `add_injuries.py` | Bake a sub-1.0 `condition` and `_injuries` note for sides with disclosed injuries. |
| `build_fixtures.py` / `regenerate_fixtures_2026.py` | Build / refresh the `fixtures_2026.json` calendar. |
| `update_results.py` | Refresh `results_2026.json` (played scores) from the live Polymarket feed. |
| `update_odds.py` | Refresh `odds_2026.json` (market odds snapshot). |
| `update_sofascore.py` | Refresh `sofascore_2026.json` (player form ratings). |
| `curate_historical.py` | Normalise the historical corpus for the calibration harness. |
| `tune_model.py` | Grid-search the Poisson constants against the historical set. |
| `value_bets_today.py` | Scan today's fixtures for positive-edge bets vs Polymarket. |

The three offline snapshots (`results`, `odds`, `sofascore`) can also be refreshed
from inside the running web UI ("Update data" screen). The fetch-and-write logic is
importable — `data_loader.refresh_results()`, `odds_store.refresh_odds()`,
`sofascore_store.refresh_sofascore()` — so the scripts, the web endpoint, and any
future TUI screen share one code path.

---

## Domain models

`models.py` holds deliberately plain dataclasses (no simulation logic), so the
data layer, factors, and engine share one vocabulary without circular imports.

### `Player` (frozen)

| Field | Notes |
|-------|-------|
| `name`, `pos`, `rating` | `pos` ∈ `{GK, DEF, MID, FWD}`; `rating` is 0–100. |
| `attributes` | Optional FM attributes (1–20). When present, the loader derives `rating` from them. Excluded from equality/hash. |
| `club` | Players sharing a club in the same XI generate chemistry. |
| `age` | Drives the intangibles age curve when no explicit `xfactor` is set. |
| `xfactor` | Optional `{"mean", "sigma"}` intangible override. Excluded from equality/hash. |

### `Team`

`rating` is the headline strength used when no lineup is selected. `squad` is the
list of `Player`s. `coach = {"skill", "bias"}`, `condition` ∈ [0,1], and
`injuries` (a note) feed the coaching/condition factors. `best_lineup(size=11)`
picks the highest-rated XI, guaranteeing a goalkeeper.

### `Lineup`

A selected set of players for one team in one match. `by_position`, `mean_rating`.

### `MatchResult`

Outcome of a match: goals, `stage`, a `date`, `extra_time`/`penalties` flags,
`home_xg`/`away_xg`, and — for live matches — `home_subs`/`away_subs`
(`(minute, off, on)` tuples) and `cooling_breaks`. `winner`/`loser` properties
resolve by score then penalties; `score_str()` formats it (`(a.e.t.)`, `(pens
x-y)`).

---

## Player ratings (FM attributes)

`fm_rating.py` derives a player's 0–100 `rating` from Football Manager-style
attributes on the **1–20 scale**, grouped the way you size up a player in FM26:

- **Mental** — Decisions, Anticipation, Positioning, Teamwork, Vision. Weighted
  heaviest for outfield players (intelligence beats raw technique).
- **Physical** — Agility, Pace, Acceleration, Stamina.
- **Technical** — role-specific: Finishing (FWD), Passing (MID), Tackling/Marking
  (DEF), shot-stopping (GK).

```
rating = 5 × (w_mental·M + w_physical·P + w_technical·T)
```

with per-position weights in `POSITION_WEIGHTS`:

| Pos | Mental | Physical | Technical |
|-----|:------:|:--------:|:---------:|
| GK  | 0.30 | 0.25 | 0.45 |
| DEF | 0.40 | 0.30 | 0.30 |
| MID | 0.45 | 0.25 | 0.30 |
| FWD | 0.38 | 0.30 | 0.32 |

The loader calls `rating_from_attributes` whenever a player has an `attributes`
block, so editing attributes — or importing a real FM export — re-derives strength
with **no engine changes**. `generate_attributes()` can invent a believable,
deterministic profile anchored to a target rating (used to seed the bundled data).

---

## The factor system

**"New variables" = new factors.** This is the core abstraction (`factors/base.py`).

A `Factor` is a small object with a unique `name` and an `adjust(ctx)` method that
mutates the shared `MatchContext` — specifically each side's `attack`/`defense`
floats, which start at the team's base rating. The engine reads only the final
numbers, so factors compose freely and (for the independent built-ins)
order-independently.

### Key objects

- **`TeamMatchState`** — mutable per-team strength: `attack`, `defense` (both start
  at `team.rating`), the optional `lineup`, and a free-form `extras` dict factors
  read from (`rest_days`, `form`, `condition`, `coach`, `intangibles`,
  `sofa_rating`, …).
- **`MatchContext`** — `home`/`away` states, `stage`, `neutral` flag, the shared
  `rng`, and a `meta` dict (e.g. `temperature`, `venue`, `wbgt_c`).
- **`FactorRegistry`** — an ordered, mutable collection. `apply(ctx)` runs every
  enabled factor in sequence. `set_enabled(name, bool)`, `add`, `remove`, `get`,
  `names`, `copy`.

### Adding a factor

Three lines. Subclass `Factor`, give it a unique `name`, decorate with `@register`
to put it in `default_registry()`:

```python
from worldcup.factors.base import Factor, MatchContext, register

@register
class AltitudeFactor(Factor):
    name = "altitude"
    def adjust(self, ctx: MatchContext) -> None:
        if ctx.meta.get("venue") == "Mexico City":
            ctx.home.attack += 1.5      # locals used to the thin air
```

Or assemble a `FactorRegistry` by hand and pass it to the simulators for a bespoke
set. Toggle any factor with `registry.set_enabled("altitude", False)`.

---

## Built-in factors

Live in `factors/builtin.py`. The default registry applies them in this order:
`lineup`, `home_advantage`, `fatigue`, `chemistry`, `condition`, `coaching`,
`intangibles`, `form`, `sofascore`.

| Factor | Reads | Effect |
|--------|-------|--------|
| **lineup** | the selected XI vs `best_lineup()` | Shifts attack/defense by the quality delta (× `sensitivity` 0.6). Best XI = base rating; rotation lowers it. No lineup = untouched. |
| **home_advantage** | `ctx.neutral` | +3 attack / +2 defense to the home side; no-op at neutral venues (all non-host group games). |
| **fatigue** | `extras['rest_days']` | Penalises short rest vs a 4-day reference (0.6/day, ±4 cap). `rest_days` is derived from the real `fixtures_2026.json` calendar. |
| **chemistry** | club-mates in the XI | +0.6 attack & defense per same-club **pair**, capped +4.0. A 4-player club core = 6 pairs. |
| **condition** | `extras['condition']` or baked `team.condition` | Match-day fitness in [0,1]; scales the side down (−12 rating at zero). Models injuries/sharpness. |
| **coaching** | `team.coach = {skill, bias}` | `skill` lifts both ends; `bias` ∈ [−1,1] tilts attack vs defense by ±2.5 without changing net strength. |
| **intangibles** | per-player `xfactor` / age curve | Draws one Gaussian shock per side per match: `mean` (knowable drift) + `sigma` (genuine swing), clamped ±6. See below. |
| **form** | `extras['form']` ∈ [−1,1] | ±3 rating at the extremes. Opt-in; 0/unset = neutral. |
| **sofascore** | `extras['sofa_rating']` (~6.0–7.6) | Results-aware in-tournament form: shifts attack/defense by the gap from a ~6.7 baseline, using each side's average SofaScore rating over the games it has *actually played*. Opt-in; no rating yet = untouched. |

### The SofaScore form factor (no look-ahead)

`sofascore` reads each side's average SofaScore player rating across the games it
has played so far and shifts attack/defense by the gap from a ~6.7 baseline. The
per-team ratings come from the offline snapshot (`data/sofascore_2026.json`) via
`sofascore_store.form_extras`, handed to the simulators as `extras` so it applies
to a team's *future* fixtures only — it is **never** back-applied to the played-game
grids that grade model vs market (no look-ahead). The live fetch
(`worldcup.sofascore`) is best-effort; the committed snapshot is the source of truth.

### Heat / venue factors (`factors/heat.py`)

The 2026 World Cup is played across the U.S. and Mexico in June–July. Six reusable
causal chunks (H1–H6) from the tournament-heat betting analysis are encoded as
factors. They compose freely with everything above and are no-ops when no `venue` /
`wbgt_c` is set in `meta`.

| Factor | Reads | Effect |
|--------|-------|--------|
| **venue_asymmetry** | `meta['venue']` (no WBGT set) | Pure lookup: fills `meta['wbgt_c']` from the venue's nominal June–July WBGT, plus venue offset; AC venues hard-clamped to ≤ 24°C. Idempotent. |
| **heat_stamina** | `meta['wbgt_c']` | H1. Symmetric penalty to attack and defense, scaled from 26°C (FIFA cooling-break threshold) up to 30°C; cap at the constructor's `scale` (default 8 points). |
| **press_intensity** | `meta['wbgt_c']`, per-side `extras['tactical_bias']` ∈ [−1,+1] | H2. The pass-completion paradox. High-press sides in heat see attack + / defense − (the press fades); low-block sides see the inverse. Max swap 3 points at severe WBGT. |
| **peak_speed** | `meta['wbgt_c']`, per-side `extras['peak_speed']` | H3. +1 attack shock at severe WBGT (the +4% isolated peak-speed anomaly). Random per match via the context RNG. |
| **knockout_heat_draw** | `meta['wbgt_c']`, `ctx.stage` ∈ {R32…F} | H4. Compresses both sides' attack and defense in knockout games at unsafe WBGT — scorelines flatten → more draws → more penalties (the r=0.82 signal). |
| **hydration_window** | `meta['venue']`, `meta['wbgt_c']` | H6. Stamps `meta['hydration_required']` and `meta['hydration_tier']` (`safe`/`cooling`/`unsafe`/`severe`). The live engine reads `meta['temperature']` for the actual break schedule. |

The venue table (`VENUE_RISK`) covers all 16 tournament venues with `wbgt_offset`,
`ac` flag and `risk` tier; exactly 3 venues are climate-controlled.

Because per-side symmetric penalties preserve total xG in the Poisson model
(`expected_goals(80,80) == expected_goals(76,76)`), the engine also applies a
**heat-pace multiplier** to total match xG after the factors run:

| WBGT | Group | Knockout |
|------|-------|----------|
| < 26°C | 1.00 | 1.00 |
| 26–28°C | 0.95 | 0.90 |
| 28–30°C | 0.88 | 0.78 |
| ≥ 30°C | 0.82 | 0.70 |

This global brake delivers the empirical "fewer goals in heat" signal: in a
500-iteration Monte Carlo at Monterrey (30°C WBGT) vs. a cool baseline, total match
goals drop by ~13–15% and knockout penalty-shootout probability rises by ~50%
relative. Opt-in per match: pass `meta={'venue': 'Monterrey', 'wbgt_c': 30.0}` to
`simulate(...)` / `monte_carlo(...)`. Use `enable_heat(registry, on=True/False)` to
toggle the six heat factors at runtime.

### The intangibles model (the "no one knows" factor)

The motivating case: a 41-year-old talisman's legs are a minus, but his experience
and effect on morale could swing a match either way. So each influence is a
**random variable**, not a single number:

- `mean` — the directional expectation (what we think we know — the legs).
- `sigma` — the genuine uncertainty (what no one can call — the swing).

Per match the factor draws one shock per side from the context RNG, clamps to ±6,
and applies it to attack and defense. Over a Monte Carlo run the average tends to
`mean`; individual tournaments get the heavy tails (the iconic night, the anonymous
one). Per-player `(mean, sigma)` comes from an explicit `player.xfactor` or, by
default, from an **age curve** (`age_xfactor`): veterans (33+) get a modest negative
mean that grows with age and a `sigma` that grows with age **and** stature; wonderkids
(≤21) are raw on average but volatile; prime age ≈ neutral. Means add across the XI;
variances add (`sigma_team = sqrt(Σ sigma_i²)`). Concretely: Ronaldo (41) → `mean
−1.53, swing ±3.6`; Messi (39) → `≈ −1.2, ±3.7`; Yamal (18) → `≈ −0.8, ±2.7`.

---

## The match engine

`engine/match.py` — `MatchSimulator` is the glue between factors and the Poisson
model. Construct once (optionally with a custom registry and an explicit `rng` for
reproducibility) and reuse.

### `simulate(...) → MatchResult`

1. Build a `MatchContext` (states start at base rating; `extras` from kwargs).
2. `registry.apply(ctx)` — every factor adjusts attack/defense.
3. `poisson.expected_goals` converts the adjusted strengths to xG; draw a scoreline
   from the (Dixon–Coles-correlated) Poisson distribution.
4. Knockout ties (`stage` ∈ `{R32, R16, QF, SF, F}`) resolve via extra time (one
   third of regulation xG) then a penalty shootout.

World Cup games default to `neutral=True`; the home-advantage factor only fires for
host games (`neutral=False`).

### The Poisson model (`engine/poisson.py`)

```
xG = BASE_GOALS · exp(RATING_COEFF · (attack − opponent_defense))   clamped to [MIN_XG, MAX_XG]
```

| Constant | Value | Meaning |
|----------|------:|---------|
| `BASE_GOALS` | 1.35 | League-average goals per team when sides are equal. |
| `RATING_COEFF` | 0.030 | How much a one-point rating edge shifts xG. A ~24-pt gap → ~3.1 vs ~0.6 xG. (Tuned 2026-06-22 from 0.035.) |
| `RHO` | −0.1 | **Dixon–Coles** low-score correlation — boosts 0-0/1-1 and de-couples 1-0/0-1 to match real draw frequencies. |
| `MIN_XG` / `MAX_XG` | 0.05 / 6.0 | Keeps extreme ratings from producing absurd blowouts. |

Goals are drawn as independent Poisson counts (Knuth's algorithm), then the
**Dixon–Coles** weight `dc_adjust` is applied to the four low scorelines via
rejection sampling — preserving the marginal Poisson expectations while correcting
the joint low-score distribution. The penalty-shootout conversion edge scales gently
with attack rating (clamped 0.60–0.88).

### `monte_carlo(home, away, n) → MatchOdds`

Replays one fixture `n` times: win/draw/loss probabilities (knockout folds in ET +
pens), average goals & xG, and the most common scorelines.

---

## The live (time-segmented) engine

`engine/live.py`, reached via `MatchSimulator.simulate_live(...)`. The aggregate
model resolves a match as a single xG draw per side — fast, perfect for big Monte
Carlo runs, but with **no notion of time**. The live engine adds that time axis: the
match is split into **five-minute segments** and three things happen the aggregate
model cannot express.

- **Progressive fatigue** — every on-pitch player tires from kickoff to the final
  whistle, *faster* late on (a mildly convex curve, `FATIGUE_EXPONENT` 1.3).
  Lower-`Stamina` players fade quicker, dragging the side's attack/defense down.
- **Substitutions (auto-coach)** — at realistic windows (60', 75', 90', 105') the
  coach replaces the weakest/most-tired outfielder with the best *fresh* bench
  player of the same position, up to **five** subs, only if the bench player beats
  the tired one by `SUB_IMPROVEMENT` (1.5). A deep bench (the full 26-man squad)
  matters; keepers are excluded from outfield subs.
- **Hydration / cooling breaks** — when `meta['temperature'] ≥ 30 °C`, FIFA-style
  breaks around 30'/75'/105' refund some fatigue.

Everything *pre-match* (home advantage, coaching, chemistry, intangibles, form,
between-games fatigue) is still computed once by the normal factor registry; the live
engine only layers in-match dynamics on top of that baseline. With fatigue off and no
useful bench it **reduces exactly to the aggregate model** (a tested invariant). It
also returns an event log (subs, cooling breaks) on the `MatchResult`.

```bash
worldcup match "Haiti" "Curacao" --temp 33 --seed 7   # subs at 60'/75' + cooling breaks
worldcup match "Brazil" "Spain" --no-live             # fast aggregate model instead
```

---

## The tournament layer

### Group stage (`tournament/group_stage.py`)

Round-robin within each group, ranked by the FIFA 2026 tiebreakers in strict order:
points, then — since the 48-team expansion — the **head-to-head** results among the
tied teams (head-to-head points, GD, then goals scored, re-applied to any smaller
subset that stays level) *before* the group-wide GD and goals scored, then fair play
(not modelled — no cards) and the FIFA ranking (proxied by team rating) ahead of a
drawing of lots (`rank_standings`). Host nations get home advantage in their own
games. `rank_third_placed` selects the best third-placed teams to fill the 32-team
knockout field, using the simplified order points → overall GD → goals scored →
FIFA-ranking proxy (no head-to-head, since those teams come from different groups).

### Knockout (`tournament/knockout.py`)

The bracket follows the **official FIFA 2026 format**. Round-of-32 pairings are fixed
by group-finishing position (`R32_MATCHES`), and the 8 third-placed qualifiers are
slotted via FIFA's published allocation table (`data/third_place_allocation_2026.json`,
Annex C — all 495 combinations). `build_official_bracket_2026()` arranges the 32 teams
in leaf order and `play_knockout(..., prearranged=True)` plays them down the official
R16/QF/SF/Final match tree. `R32_LEAF_ORDER` is what reproduces the official
match-number tree — don't reorder it casually. `play_knockout` requires a
**power-of-two** field (32).

> `play_knockout` also keeps a generic balanced *performance-seed* mode
> (`prearranged=False`, the default) for unit tests on arbitrary fields, where it
> seeds via `bracket_seed_order` to keep top seeds apart.

### Orchestration (`tournament/simulator.py`)

- `TournamentSimulator.run_once()` → one full tournament (`TournamentOutcome`: groups,
  the 32 qualifiers in bracket order, the knockout result, the champion).
- `monte_carlo(n)` → runs the whole thing `n` times and tallies how often each team
  reaches each stage (`R32 → R16 → QF → SF → Final → Champion`), returning a
  `MonteCarloReport` with `title_odds()` and per-team `reach` probabilities.

**`lineups` / `extras` apply to every match a team plays** — that's how you model
"team X rests its stars all tournament" or "team Y is in red-hot form throughout".

---

## Results awareness

**Simulations are results-aware as the tournament unfolds.** `data/results_2026.json`
(regenerated from the live Polymarket feed by `scripts/update_results.py`, or the web
UI's "Update data" screen — both call `data_loader.refresh_results()`) lists
already-played fixtures. `load_results()` reads it; `TournamentSimulator(results=...)`
splits them by stage into `known_group` / `known_knockout`. `play_group` records group
scores verbatim instead of simulating, and `play_knockout(..., known=...)` does the
same for the bracket, so standings *and* the knockout run are real and only the
remaining games are drawn. `worldcup odds`/`tournament` (and the front ends) use this
by default — pass `--fresh` to ignore it and simulate the whole event from scratch. A
missing file just means "simulate everything".

- **A played knockout tie needs a decidable winner.** The Polymarket score is just the
  90'+ET scoreline, so a tie that went to penalties looks level. `PlayedResult` carries
  optional `home_pens`/`away_pens` and a `winner` property (score, then penalties);
  `play_knockout` replays a known tie only when the winner is determinable and otherwise
  *simulates* that tie — it never advances a side silently from a level score. Add the
  `home_pens`/`away_pens` by hand to pin a shootout result.
- `current_standings` is computed by `tournament.standings_from_results` — real group
  tables from played results only (teams may have `played < 3`), distinct from
  `play_group` which simulates. The dashboard's group cards use it.

---

## Polymarket: odds, betting & the bet tracker

`polymarket.py` pulls live markets from **Polymarket's public Gamma API**
(read-only, no key, stdlib `urllib`). `betting.py`, `games.py` and `tracker.py` line
our Monte Carlo up against the market. The market side is also **snapshotted offline**
(`data/odds_2026.json` via `odds_store.py`) so the dashboards build with no network
round-trips; `--live` refetches.

- **`worldcup bets`** — for a market (title, reach R16/QF, group winners), prints
  per-team market vs model probability, edge (model − market) and EV/$1; then selects
  positive-edge teams, sizes each with **fractional Kelly**, caps at the bankroll, and
  replays the tournament to report the slate's P&L distribution.
- **`worldcup games`** — compares our per-match odds to Polymarket's per-game markets
  (upcoming vs live odds; played vs pre-kickoff odds + actual result), with a
  Brier-score accuracy summary.
- **`worldcup dashboard`** — a model-vs-market scoreboard over all finished games
  (favourite-correct rate, Brier, log-loss, head-to-head on disagreements).
- **`worldcup tracker`** — your **personal bet tracker**: records real-money bets
  (`data/user_bets_2026.json`), prices every selection against the engine, and writes
  a standalone `tracker.html` projecting each slip's ROI / P&L distribution via Monte
  Carlo. Known selection types: `match_result`, `over25`, `under25`, `btts`,
  `player_goal`, `outright`. A slip settles only when *every* leg hits.
- **`scripts/value_bets_today.py`** — scans today's fixtures, derives hundreds of
  derivative markets (1X2, multiple O/U lines, BTTS, exact score, Asian Handicap,
  Double Chance, Draw No Bet) from the model's joint scoreline distribution, and
  surfaces positive-edge bets ranked by edge with half-Kelly staking. Polymarket only
  lists the three-way 1X2 line, so only those are priced vs. a real exchange; the rest
  you price against your own book.

> The simulated P&L (both `bets` and `tracker`) settles bets with the **same model
> that priced them**, so its ROI tracks the analytic edge by construction. Read it as
> the variance/drawdown of your edge *if the model is right* (what Kelly sizing needs),
> **not** an independent backtest. The model-vs-market comparison table is the part
> that stands on its own.

---

## Calibration harness

`worldcup.calibrate` is a reproducible grader that scores the model against real
international football — the curated historical corpus (`data/historical/`, WC
2014/2018/2022) plus the played 2026 fixtures.

```bash
PYTHONPATH=src python -m worldcup.calibrate grade --n 5000 --seed 42
```

It Monte-Carlos each historical fixture with the current engine and reports Brier
(1X2), log-loss (1X2), Brier (O/U 2.5), goal RMSE and a scoreline χ², writing the
report to **`data/calibration/latest.md`**. This is the gate for any constant change:
`scripts/tune_model.py` grid-searches the Poisson constants against the same corpus,
and `RATING_COEFF` was retuned 0.035 → 0.030 on this basis (see
[`CHANGELOG.md`](CHANGELOG.md)).

---

## The CLI

`worldcup <subcommand>` (or `PYTHONPATH=src python -m worldcup.cli <subcommand>`).
Pass `--seed` anywhere for reproducible runs.

| Command | What it does |
|---------|--------------|
| `teams` | List the 48 teams by group with ratings. |
| `squad <team>` | Squad by position (FM means, club, age), best XI, chemistry cores, intangible mean/swing, coach, condition. |
| `match <home> <away>` | Simulate one match. Flags below. |
| `match <home> <away> -n N` | Monte Carlo a fixture: W/D/L odds, avg goals/xG, common scorelines. |
| `tournament` | Simulate one full tournament, printed group-by-group then the bracket. |
| `odds -n N` | Monte Carlo title/stage probabilities (`--top` for how many teams). |
| `bets` | Compare to a Polymarket event + simulate value bets (`--event`, `--search`, `--bankroll`, `--kelly`, `--min-edge`). |
| `games` | Compare per-match odds to Polymarket per-game markets (`--game`, `--played`, `--upcoming`). |
| `dashboard` | Model-vs-market scoreboard over finished games. |
| `html` | Write a self-contained `dashboard.html` (model vs market). `--live` to refetch odds. |
| `tracker` | Write a self-contained `tracker.html` (your bets vs the model). |
| `tui` | Interactive, arrow-key terminal UI. |
| `web` | Interactive browser UI (same feature set as the TUI). |

Useful `match` flags: `--stage group/R32/R16/QF/SF/F` (knockout forces a winner),
`--home-advantage`, `--home-xi`/`--away-xi` (comma-separated names), `--temp C` (≥30
triggers cooling breaks), `--no-live` (fast aggregate model), `--no-sofa` (ignore
in-tournament form). `odds`/`tournament` take `--fresh` to ignore played results.

```bash
worldcup match "Argentina" "Brazil" \
  --home-xi "Emiliano Martinez,Cristian Romero,Enzo Fernandez,Lionel Messi"
```

---

## Interactive front ends (TUI & web)

There are **two interactive front ends over the same engine**, exposing the same
feature set — mirror any new feature in both.

- **`worldcup tui`** — the terminal UI (arrow-key sibling of the web app).
- **`worldcup web`** — the browser UI: a stdlib-only `http.server` whose JSON
  endpoints each wrap the exact engine call a TUI screen makes (`/api/match`,
  `/api/match-odds`, `/api/tournament`, `/api/title-odds`, `/api/squad`,
  `/api/lineups`, the live `/api/games*`, `/api/dashboard-live`, `/api/bets`, the
  offline `/api/report` + `/dashboard.html`, and `/api/refresh` which re-snapshots
  the live data). The whole single-page app is one embedded HTML/CSS/JS string; all
  state lives server-side, the page just fetches.

The "Update data (live)" screen → `POST /api/refresh` always refreshes results and
refreshes odds / SofaScore ratings only when asked. The SofaScore fetch is
best-effort (a failure is reported, never fatal, and never wipes a good snapshot),
then `_GAMES_CACHE` is busted and fresh counts echoed so the page re-renders.

---

## Python API cookbook

```python
from worldcup import load_world_cup, TournamentSimulator, Lineup
from worldcup.engine import MatchSimulator
import random

teams, tournament = load_world_cup()

# Title odds
report = TournamentSimulator(teams, tournament).monte_carlo(5000)
for name, p in report.title_odds()[:10]:
    print(f"{name:15s} {p:6.1%}")

# One reproducible match
sim = MatchSimulator(rng=random.Random(1))
print(sim.simulate(teams["Brazil"], teams["Spain"]).score_str())

# A live match in the heat
res = sim.simulate_live(teams["Haiti"], teams["Curacao"], meta={"temperature": 33})
print(res.score_str(), res.home_subs, res.cooling_breaks)

# "What if Brazil rest Vinicius all tournament?" — lineups apply to every match.
brazil = teams["Brazil"]
rotated = Lineup("Brazil", [p for p in brazil.squad if p.name != "Vinicius Junior"][:11])
report2 = TournamentSimulator(teams, tournament).monte_carlo(5000, lineups={"Brazil": rotated})

# Turn a factor off
sim.registry.set_enabled("chemistry", False)
```

---

## Testing

```bash
pytest                                                            # whole suite (174 tests)
pytest tests/test_engine.py::test_stronger_team_wins_more_often   # one test
ruff check src tests                                              # lint (same gate as CI)
mypy -p worldcup                                                  # type-check the typed core
```

CI (`.github/workflows/ci.yml`) runs ruff + mypy once and pytest across Python
3.10–3.13. `mypy` is **incrementally** adopted: the simulation core and data/store
layer are checked; the loose presentation/network modules are exempted under
`[[tool.mypy.overrides]] ignore_errors` in `pyproject.toml`.

Test modules cover the engine, factors, chemistry, intangibles, fatigue, heat,
Dixon–Coles adjustment, the live engine, the tournament, betting, the bet tracker
(logic/pricing/report), SofaScore form, FM ratings, the calibration harness, the
dashboard and the web app. Notable invariants under test: stronger teams win more
often; the live engine collapses to the aggregate model with fatigue off;
substitutions respect the 5-sub budget and the hour-mark window; cooling breaks fire
only when hot; knockout always produces a winner; factors are wired into the default
registry.

---

## Tuning reference

Everything tunable lives next to what it controls — no magic numbers in the match
logic.

| Knob | Location | Controls |
|------|----------|----------|
| `BASE_GOALS`, `RATING_COEFF`, `RHO`, `MIN_XG`, `MAX_XG` | `engine/poisson.py` | Goal-scoring level, rating sensitivity, Dixon–Coles correlation. |
| `LineupFactor.sensitivity` | `factors/builtin.py` | How hard rotation hurts. |
| `HomeAdvantageFactor` bonuses | `factors/builtin.py` | Host-nation boost. |
| `FatigueFactor` reference/slope | `factors/builtin.py` | Short-rest penalty depth. |
| `ChemistryFactor.per_pair` / `cap` | `factors/builtin.py` | Club-chemistry reward. |
| `ConditionFactor.scale` | `factors/builtin.py` | Injury/sharpness penalty depth. |
| `CoachFactor.tilt` | `factors/builtin.py` | Attacking/defensive bias strength. |
| `IntangiblesFactor.cap`, `age_xfactor` curve | `factors/builtin.py` | Swing magnitude and the age model. |
| `SofaScoreFactor` baseline/scale | `factors/builtin.py` | In-tournament form sensitivity. |
| `VENUE_RISK`, heat-pace bands | `factors/heat.py`, `engine` | Per-venue WBGT and the heat goal brake. |
| `POSITION_WEIGHTS`, `SCALE` | `fm_rating.py` | How attributes map to a 0–100 rating. |
| `FATIGUE_*`, `SUB_*`, `HYDRATION_*`, `SEGMENT_MIN` | `engine/live.py` | All in-match dynamics. |

To disable any factor at runtime: `registry.set_enabled("<name>", False)`.

---

*No third-party runtime dependencies — pure standard library.*
