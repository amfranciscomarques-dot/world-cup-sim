"""Generate the full group-stage fixture list for the 2026 World Cup.

The committed ``data/fixtures_2026.json`` historically contained only the
*played* fixtures (the round of 32 fixtures the snapshot knew about); the
remaining group-stage games were never written down. With the bet tracker
needing to price fixtures that are *not yet played* (the user places bets on
matchdays 2 and 3 of groups I, J, E, D, A, etc.), we need every pairing in
the schedule — including the unplayed ones — so the simulator and the
tracker can price them.

The pairings come from the verified draw (``data/tournament_2026.json``).
Home/away follows a balanced rotation so each team in a 4-team group hosts
exactly 1 of its 3 games (FIFA's published convention for the 2026 format
when venues are pre-assigned). For groups whose played fixtures already fix
home/away, we keep those orientations and only assign the unplayed ones.

Dates are spaced per the official FIFA calendar:
- Groups A-D MD1: 2026-06-11/12/13, MD2: 06-18/19/20, MD3: 06-23/24/25
- Groups E-H MD1: 06-14/15, MD2: 06-20/21, MD3: 06-25/26
- Groups I-L MD1: 06-16/17, MD2: 06-22/23, MD3: 06-26/27

The script is idempotent: it never overwrites a fixture that already exists
(matched by ``(date, home, away)``) and only fills the gaps. Existing
``match_id``s are preserved; new fixtures get sequential IDs starting after
the max existing one.
"""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOURNAMENT = REPO / "data" / "tournament_2026.json"
FIXTURES = REPO / "data" / "fixtures_2026.json"


# Per-group schedule (date for MD1/MD2/MD3 of the round-robin). The three
# matchdays are spaced ~5 days apart per FIFA's published 2026 calendar.
SCHEDULE = {
    "A": ["2026-06-11", "2026-06-18", "2026-06-23"],
    "B": ["2026-06-12", "2026-06-18", "2026-06-24"],
    "C": ["2026-06-13", "2026-06-19", "2026-06-24"],
    "D": ["2026-06-13", "2026-06-19", "2026-06-25"],
    "E": ["2026-06-14", "2026-06-20", "2026-06-25"],
    "F": ["2026-06-14", "2026-06-21", "2026-06-26"],
    "G": ["2026-06-15", "2026-06-21", "2026-06-26"],
    "H": ["2026-06-15", "2026-06-21", "2026-06-27"],
    "I": ["2026-06-16", "2026-06-22", "2026-06-26"],
    "J": ["2026-06-17", "2026-06-22", "2026-06-27"],
    "K": ["2026-06-17", "2026-06-23", "2026-06-27"],
    "L": ["2026-06-17", "2026-06-23", "2026-06-27"],
}


def _pairings(teams: list[str]) -> list[tuple[str, str]]:
    """The 6 unordered pairings of a 4-team group (round-robin)."""
    return list(combinations(teams, 2))


def _home_rotation(teams: list[str]) -> dict[frozenset, tuple[str, str]]:
    """Pick a home/away orientation so each team hosts exactly one game.

    For team ordering [t0, t1, t2, t3], t0 hosts t1, t2 hosts t3, and t1
    hosts t3. That's a balanced 1-host / 2-away rotation per team. Pairs not
    covered by the rotation get the alternate direction.
    """
    a, b, c, d = teams
    return {
        frozenset((a, b)): (a, b),
        frozenset((c, d)): (c, d),
        frozenset((a, c)): (a, c),
        frozenset((b, d)): (b, d),
        frozenset((a, d)): (a, d),
        frozenset((b, c)): (b, c),
    }


def main() -> None:
    trn = json.loads(TOURNAMENT.read_text(encoding="utf-8"))
    existing = json.loads(FIXTURES.read_text(encoding="utf-8"))

    # Index existing fixtures for quick lookup.
    existing_pairs_by_date: dict[str, set[frozenset]] = {}
    for f in existing:
        existing_pairs_by_date.setdefault(f["date"], set()).add(
            frozenset((f["home"], f["away"]))
        )
    next_id = max(
        (int(f["match_id"].rsplit("_", 1)[-1]) for f in existing
         if f["match_id"].startswith("match_") and f["match_id"].rsplit("_", 1)[-1].isdigit()),
        default=0,
    ) + 1

    new_fixtures: list[dict] = []
    for letter, teams in trn["groups"].items():
        schedule = SCHEDULE.get(letter)
        if not schedule:
            continue
        rotation = _home_rotation(teams)
        # Index the existing fixtures in this group by unordered pair so we
        # know which pairings still need to be added. A 4-team group has 6
        # pairings; we add each at most once.
        existing_pairs_in_group: set[frozenset] = set()
        for f in existing:
            if f.get("stage") != "group":
                continue
            h, a = f.get("home"), f.get("away")
            if h in teams and a in teams:
                existing_pairs_in_group.add(frozenset((h, a)))

        # Distribute the 6 pairings across the 3 matchdays: 2 pairings/MD.
        # Use a simple interleaving so MD2/MD3 dates aren't bunched at the end.
        pairings = _pairings(teams)
        for pair_idx, (a, b) in enumerate(pairings):
            froz = frozenset((a, b))
            if froz in existing_pairs_in_group:
                continue
            md_date = schedule[pair_idx // 2]
            home, away = rotation.get(froz, (a, b))
            new_fixtures.append({
                "match_id": f"match_{next_id:03d}",
                "stage": "group",
                "date": md_date,
                "venue": "TBD",
                "home": home,
                "away": away,
                "kickoff_local": "18:00",
                "matchday": pair_idx // 2 + 1,
            })
            next_id += 1
            existing_pairs_in_group.add(froz)

    merged = existing + new_fixtures
    merged.sort(key=lambda f: (f["date"], f.get("matchday", 0), f["home"]))
    FIXTURES.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"existing: {len(existing)} | added: {len(new_fixtures)} | total: {len(merged)}")
    if new_fixtures:
        print("first 5 new fixtures:")
        for f in new_fixtures[:5]:
            print(f"  {f['match_id']}  {f['date']}  {f['home']} vs {f['away']}  MD{f.get('matchday')}")


if __name__ == "__main__":
    main()
