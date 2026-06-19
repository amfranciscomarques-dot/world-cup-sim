"""Bake a squad-wide ``condition`` (match-day fitness, 0-1) into each team in
data/teams_2026.json from the injuries disclosed up to the start of the 2026
World Cup, so default simulations already feel the absences.

``condition`` is read by the condition factor (a per-match ``extras['condition']``
still overrides it). The value reflects the injury burden weighted by how central
the missing/doubtful players are to *our* curated XI: a first-choice player ruled
OUT hurts more than a fringe one; a star racing for fitness (DOUBT) is a smaller,
partial hit. ``scale=12`` rating points at zero, so e.g. 0.90 ~= -1.2 points.

These are time-sensitive; edit as news changes. Sources tracked in NOTES below.
Re-running is safe and idempotent. Teams not listed stay at full fitness (1.0).

    python scripts/add_injuries.py
"""

from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "teams_2026.json"

# team -> (condition, reason). Only disclosed injuries to players in our squads.
INJURIES: dict[str, tuple[float, str]] = {
    # --- main title contenders ---------------------------------------------
    "Argentina": (0.86, "Romero (CB) & Molina (RB) OUT; Messi doubt (thigh)"),
    "Germany":   (0.89, "ter Stegen (GK) OUT; Musiala doubt (ankle)"),
    "Netherlands": (0.93, "Xavi Simons OUT (ACL)"),
    "Brazil":    (0.94, "Rodrygo OUT (ACL); squad has cover up front"),
    "Spain":     (0.97, "Yamal doubt (hamstring), expected fit for opener"),
    "France":    (0.98, "Saliba doubt (back), expected OK"),
    # England, Portugal: no disclosed injuries to our XI -> left at 1.0
    # --- secondary sides (disclosed, for realism) --------------------------
    "Japan":     (0.92, "Mitoma OUT (hamstring); Endo doubt (ankle)"),
    "Morocco":   (0.95, "Hakimi doubt (hamstring)"),
    "Turkiye":   (0.95, "Guler doubt (hamstring)"),
    "Ghana":     (0.95, "Kudus doubt (hamstring)"),
    "Scotland":  (0.95, "Gilmour OUT (knee)"),
    "Uruguay":   (0.96, "Jose Gimenez doubt (ankle)"),
    "Croatia":   (0.97, "Modric doubt (cheekbone fracture, likely masked)"),
}


def main() -> None:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    by_name = {t["name"]: t for t in data["teams"]}
    applied = 0
    for name, (cond, reason) in INJURIES.items():
        team = by_name.get(name)
        if team is None:
            print(f"  ! {name} not found, skipping")
            continue
        team["condition"] = cond
        team["_injuries"] = reason
        applied += 1
    # Make sure teams without injuries don't carry a stale value from a prior run.
    for team in data["teams"]:
        if team["name"] not in INJURIES:
            team.pop("condition", None)
            team.pop("_injuries", None)
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"applied condition to {applied} teams; the rest are at full fitness")


if __name__ == "__main__":
    main()
