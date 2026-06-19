# World Cup Sim

A modular Monte Carlo simulator for the **2026 FIFA World Cup** (48 teams, 12
groups, Round of 32 → Final). It runs on the real, verified group draw and
squads, decides matches with a Poisson goals model, and — crucially — lets you
add new "variables" (home advantage, fatigue, form, weather, anything) as small
plug-in **factors** that integrate without touching the engine.

## Install

```bash
pip install -e .          # editable install; exposes the `worldcup` command
pip install -e ".[dev]"   # plus pytest for the test suite
```

No third-party runtime dependencies — pure standard library.

## CLI

```bash
worldcup teams                                   # the 48 teams by group
worldcup squad "Brazil"                          # squad + auto-picked best XI
worldcup match "Brazil" "Spain" --seed 1         # one match
worldcup match "Argentina" "France" --stage F    # a knockout game (forces a winner)
worldcup match "Brazil" "Spain" -n 5000          # Monte Carlo one fixture: W/D/L odds
worldcup tournament --seed 7                      # one full tournament, printed
worldcup odds -n 5000 --top 16                    # Monte Carlo title/stage odds
worldcup bets --event world-cup-winner -n 2000   # compare to Polymarket + simulate bets
```

Field a custom lineup for a single match:

```bash
worldcup match "Argentina" "Brazil" \
  --home-xi "Emiliano Martinez,Cristian Romero,Enzo Fernandez,Lionel Messi"
```

## Python API

```python
from worldcup import load_world_cup, TournamentSimulator, Lineup

teams, tournament = load_world_cup()

# Title odds
sim = TournamentSimulator(teams, tournament)
report = sim.monte_carlo(5000)
for name, p in report.title_odds()[:10]:
    print(f"{name:15s} {p:6.1%}")

# "What if Brazil rest Vinicius all tournament?" — lineups apply to every match.
brazil = teams["Brazil"]
rotated = Lineup("Brazil", [p for p in brazil.squad if p.name != "Vinicius Junior"][:11])
report2 = sim.monte_carlo(5000, lineups={"Brazil": rotated})
```

## How team strength works

- Each team has a **base rating** (0–100) derived from the June 2026 FIFA ranking.
- Each team has a **squad** of real players with a position and rating.
- A selected **lineup** shifts the base rating via the `lineup` factor: the best
  available XI leaves a team at its base rating; rotating in weaker players
  lowers attack/defense in proportion. You don't need full 26-man squads for
  this to work — the curated squads cover each nation's recognisable core.
- The engine converts the two sides' adjusted attack/defense into **expected
  goals** and draws a scoreline from independent Poisson distributions. Knockout
  ties go to extra time, then penalties.

## Football Manager attributes

Each player carries FM-style attributes on the **1-20 scale**, grouped the way you
size up a player in FM26 — and the 0-100 `rating` is *derived* from them
(`src/worldcup/fm_rating.py`), not entered by hand:

- **Mental** — Decisions, Anticipation, Positioning, Teamwork, Vision. Weighted
  heaviest for outfield players (in FM26 intelligence beats raw technique).
- **Physical** — Agility, Pace, Acceleration, Stamina.
- **Technical** — role-specific: Finishing for a forward, Passing for a
  midfielder, Tackling/Marking for a defender, shot-stopping for a keeper.

`rating = 5 × (w_mental·M + w_physical·P + w_technical·T)`, with per-position
weights in `POSITION_WEIGHTS`. The data loader recomputes a player's rating from
their `attributes` block automatically, so editing attributes — or importing a
real FM export — re-derives strength with no engine changes.

```bash
worldcup squad "Spain"      # now prints each player's Mental/Physical/Technical means
worldcup tui                # SQUADS -> pick a player for the full 1-20 attribute card
```

The bundled attributes were generated from the curated ratings
(`scripts/generate_fm_attributes.py`; the pre-attribute file is kept as
`data/teams_2026.baseline.json`). To plug in your own FM numbers, replace each
player's `attributes` object and the loader does the rest.

## Club chemistry

Every player carries a `club`, and the **chemistry** factor rewards club-mates who
line up together: each same-club *pair* in a side's XI adds to its attack and
defense (0.6 each, capped at +4.0). A three-player club core is three pairs; a
four-player core is six. This captures real understanding — e.g. Portugal's PSG
block (Nuno Mendes, Vitinha, João Neves, Gonçalo Ramos) earns the full bonus,
as do Germany's Bayern core and the all-domestic spines of Qatar and Saudi Arabia.

```bash
worldcup squad "Portugal"   # lists each player's club + the chemistry cores
worldcup tui                # SQUADS shows cores; a match panel shows each side's bonus
```

Clubs and the chemistry cores reflect 2025/26 squads (`scripts/add_clubs.py`).
Like any factor, chemistry can be tuned or switched off:
`registry.set_enabled("chemistry", False)`.

## Condition, coaching and intangibles

Three more factors model the things beyond raw rating (`scripts/add_intangibles.py`
populates their data — ages and managers as of 2025/26):

- **condition** — match-day physical state (injuries, illness, sharpness), distinct
  from `fatigue` (rest between games). Reads `extras['condition']` in `[0, 1]`,
  falling back to a baked `team.condition`; `1.0` is peak (no effect), lower scales
  the whole side down (−12 rating points at zero). `scripts/add_injuries.py` bakes a
  condition (and a `_injuries` note) for sides hit by **disclosed injuries** up to
  kickoff, weighted by how central the missing/doubtful players are — e.g. Germany
  0.89 (ter Stegen out, Musiala doubt), Argentina 0.86 (Romero & Molina out, Messi
  doubt), Brazil 0.94 (Rodrygo out). In a 600-run Monte Carlo this drops Germany's
  title odds ~3pp and lifts uninjured sides like Portugal. The numbers are
  time-sensitive — edit the script as the news changes.
- **coaching** — the manager. `team.coach = {"skill", "bias"}`: `skill` adds rating
  points to **both** attack and defense (organisation), while `bias` in `[-1, 1]`
  is a tactical *tilt* — `+1` all-out attack (attack up, defense down), `−1` parks
  the bus. A tilt changes the split, not the net strength, so a gung-ho side both
  scores and concedes more (e.g. Bielsa's Uruguay).
- **intangibles** — the "no one knows" factor. The hard case the user posed: a
  41-year-old Ronaldo's legs are a minus, but his experience and effect on morale
  could swing a match either way. So each influence is a **random variable**, not a
  single number: a `mean` (the part we believe — the legs) plus a `sigma` (the part
  nobody can call — the swing). Per match we draw one shock per side from the RNG,
  clamp it to ±6, and apply it to attack and defense. Over a Monte Carlo run the
  average tends to `mean`; individual tournaments get the heavy tails — the iconic
  night and the anonymous one.

The per-player `(mean, sigma)` comes from an explicit `player.xfactor =
{"mean", "sigma"}` or, by default, from an **age curve**: veterans (33+) get a
modest negative mean that grows with age and a `sigma` that grows with age **and**
stature (a talisman carries a *large spread*, not a large positive mean);
wonderkids (≤21) are raw on average but volatile; prime age is ≈ neutral. Means add
across the XI, variances add. Concretely, Ronaldo (41) resolves to `mean −1.53,
swing ±3.6`, Messi (39) to `≈ −1.2, ±3.7`, Yamal (18) to `≈ −0.8, ±2.7`.

```bash
worldcup squad "Portugal"   # shows ages, the coach, and the XI's intangible mean/swing
worldcup tui                # a match panel breaks down chemistry, coaching and intangibles
```

All three are ordinary factors — tune the constants in `factors/builtin.py`
(`ConditionFactor.scale`, `CoachFactor.tilt`, `IntangiblesFactor.cap`, the
`age_xfactor` curve) or switch any off with `registry.set_enabled(name, False)`.
Ages and coaches are curated estimates in the same spirit as the player ratings.

## Polymarket: compare odds and simulate value bets

The `bets` command pulls a live World Cup market from **Polymarket's public Gamma
API** (read-only, no key, stdlib `urllib` — see `src/worldcup/polymarket.py`),
converts each team's `Yes` price into an implied probability, and lines it up
against our Monte Carlo:

```bash
worldcup bets --event world-cup-winner -n 2000        # title market
worldcup bets --event world-cup-nation-to-reach-quarterfinals
worldcup bets --event world-cup-group-d-winner --bankroll 500 --kelly 0.5
worldcup bets --search "world cup"                    # discover event slugs
```

It prints, per team, the **market** vs **model** probability, the **edge**
(model − market) and the **EV per $1**. It then selects positive-edge teams,
sizes each with **fractional Kelly** (`--kelly`, default half), caps the slate at
your `--bankroll`, and replays the tournament to report the bet slate's P&L
distribution (mean, ROI, win-rate, p5/median/p95). Supported markets settle
straight from one tournament run: tournament winner, reach R16/QF, and group
winners. Team names are auto-mapped to our squads (accents and aliases handled);
unconfirmed playoff slots that have no squad are skipped and reported.

> The simulated P&L settles bets with the *same* model that priced them, so its
> ROI tracks the analytic edge by construction — read it as the **variance/
> drawdown of your edge if the model is right** (what Kelly sizing needs), not an
> independent backtest. The comparison table is the part that stands alone.

```python
from worldcup import load_world_cup, TournamentSimulator
from worldcup.polymarket import fetch_event
from worldcup.betting import run_betting

teams, tournament = load_world_cup()
event = fetch_event("world-cup-winner", teams)
sim = TournamentSimulator(teams, tournament)
report = run_betting(sim, event, 2000, bankroll=100.0, kelly_mult=0.5)
for c in report.comparisons[:5]:
    print(f"{c.team:12s} market {c.market_prob:5.1%}  model {c.model_prob:5.1%}  edge {c.edge:+5.1%}")
```

The same flow is available in the TUI under **"Polymarket: compare & bet"**.

## Adding a new variable (factor)

A factor is a small class that nudges the shared `MatchContext`. Register it and
it participates in every match automatically:

```python
from worldcup.factors.base import Factor, register

@register
class WeatherFactor(Factor):
    name = "weather"

    def adjust(self, ctx):
        if ctx.meta.get("rain"):
            ctx.home.attack -= 1.0      # sloppier in the wet
            ctx.away.attack -= 1.0
```

`@register` adds it to `default_registry()`. To use a bespoke set instead, build
a `FactorRegistry` by hand and pass it to `MatchSimulator` / `TournamentSimulator`.
Toggle any factor with `registry.set_enabled("weather", False)`.

The built-in factors are `lineup`, `home_advantage`, `fatigue`, and `form` — see
`src/worldcup/factors/builtin.py` for worked examples.

## Data

`data/teams_2026.json` and `data/tournament_2026.json` hold the squads, ratings,
and the verified group draw (final draw, 5 Dec 2025). Both are plain JSON —
edit ratings, swap in full squads, or change the draw and everything downstream
adapts. Player ratings are curated estimates; replace them with your own source
for sharper predictions.

## Tests

```bash
pytest
```
