"""Snapshot the World Cup's played results into data/results_2026.json.

Pulls every *closed* (played) fixture from Polymarket's per-game markets — the
same feed `worldcup games`/`dashboard` already use — and writes a compact,
offline result file. The tournament simulator reads it so that `worldcup odds`
and `worldcup tournament` reflect games that have actually happened: real group
standings, with only the *remaining* fixtures simulated.

Stage is inferred from the draw: a fixture between two teams drawn into the same
group is a group game; anything else is a knockout game.

Re-running is safe and idempotent — it overwrites the file with whatever
Polymarket currently reports. Network is required (read-only, no API key).

    python scripts/update_results.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from worldcup.data_loader import load_world_cup  # noqa: E402
from worldcup import polymarket  # noqa: E402

OUT = ROOT / "data" / "results_2026.json"


def main() -> None:
    teams, tournament = load_world_cup()
    group_of = {name: letter for letter, names in tournament.groups.items() for name in names}

    print("fetching played World Cup fixtures from Polymarket...", file=sys.stderr)
    try:
        games = polymarket.fetch_games(teams, closed=True)
    except polymarket.PolymarketError as exc:
        sys.exit(f"error: {exc}")

    rows: list[dict] = []
    skipped: list[str] = []
    for g in games:
        # Need both sides mapped to our squads and a parsable score.
        if not g.mapped or g.result is None:
            skipped.append(g.title or g.slug)
            continue
        h, a = int(g.score.split("-")[0]), int(g.score.split("-")[1])
        same_group = group_of.get(g.home) is not None and group_of.get(g.home) == group_of.get(g.away)
        rows.append({
            "date": g.date,
            "stage": "group" if same_group else "knockout",
            "home": g.home,
            "away": g.away,
            "home_goals": h,
            "away_goals": a,
        })

    rows.sort(key=lambda r: (r["date"], r["home"]))
    payload = {
        "_about": (
            "Played 2026 World Cup results, fetched from Polymarket per-game markets. "
            "Read by the tournament simulator to seed real group standings and only "
            "simulate the remaining fixtures. Regenerate with scripts/update_results.py."
        ),
        "_fetched": date.today().isoformat(),
        "results": rows,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    groups = sum(1 for r in rows if r["stage"] == "group")
    print(f"wrote {len(rows)} played results ({groups} group, {len(rows) - groups} knockout) to {OUT.name}")
    if skipped:
        print(f"  skipped {len(skipped)} unmapped/unscored: {', '.join(skipped[:6])}"
              + ("..." if len(skipped) > 6 else ""))


if __name__ == "__main__":
    main()
