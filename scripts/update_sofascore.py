"""Snapshot SofaScore match ratings into data/sofascore_2026.json (offline cache).

For every finished World Cup fixture, captures each side's average SofaScore
player rating (and the per-player ratings behind it). The simulation reads this
file as an in-tournament *form* signal — a squad consistently rated above the
~6.7 baseline is overperforming and gets a lift in its future games
(see worldcup.factors.builtin.SofaScoreFactor). Refresh it whenever you refresh
the played results, so the form reflects the games just added.

The work itself lives in `worldcup.sofascore_store.refresh_sofascore` so the web
UI's "Update data" action shares the exact same code path. The public SofaScore
API is bot-protected and fragile: a failed fetch leaves any existing snapshot
untouched rather than overwriting it with nothing.

    python scripts/update_sofascore.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from worldcup import sofascore  # noqa: E402
from worldcup.sofascore_store import refresh_sofascore  # noqa: E402


def main() -> None:
    try:
        summary = refresh_sofascore(log=lambda m: print(m, file=sys.stderr))
    except sofascore.SofaScoreError as exc:
        sys.exit(f"error: {exc}")

    print(f"wrote {Path(summary['path']).name}: {summary['games']} games rated, "
          f"{summary['teams_rated']} teams with a form signal")
    skipped = summary["skipped"]
    if skipped:
        print(f"  skipped {len(skipped)} unmapped/unrated: {', '.join(skipped[:6])}"
              + ("..." if len(skipped) > 6 else ""))


if __name__ == "__main__":
    main()
