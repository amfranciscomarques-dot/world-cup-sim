# World Cup Sim

A modular Monte Carlo simulator for the **2026 FIFA World Cup** (48 teams, 12 groups, Round of 32 → Final). Decides matches with a Dixon-Coles calibrated Poisson goals model and allows plug-in **factors** (home advantage, fatigue, form, heat) without touching the engine.

## Quick Start

```bash
pip install -e ".[dev]"        # editable install
pytest                         # run the suite
worldcup web                   # start the browser UI
```

## Features

- **Results-aware:** Loads played fixtures from Polymarket; simulates only the remaining games.
- **Dixon-Coles Model:** Low-score correlation (ρ = -0.1) for accurate draw and 1-0/0-0 frequencies.
- **Factor System:** Modular strength adjustments (Lineup, Fatigue, Chemistry, Condition, Coaching, Form, SofaScore, Heat).
- **Schedule-aware Fatigue:** Penalises short rest based on the real fixture calendar.
- **Polymarket Integration:** Compare model odds against live markets and simulate Kelly-sized value bets.
- **Interactive UIs:** Both Terminal (TUI) and Browser (Web) interfaces.

## Documentation

- [DOCUMENTATION.md](DOCUMENTATION.md): Deep technical reference (architecture, factors, engine).
- [PLAN.md](PLAN.md): Current development roadmap and principles.
- [CHANGELOG.md](CHANGELOG.md): History of improvements and model tuning.

## Commands

```bash
worldcup match "Brazil" "Spain" -n 5000          # Match odds
worldcup odds -n 5000                            # Tournament title/stage odds
worldcup bets --event world-cup-winner           # Value bet analysis
worldcup calibrate grade --n 5000                # Grade model against historical data
```

---
*No third-party runtime dependencies — pure standard library.*
