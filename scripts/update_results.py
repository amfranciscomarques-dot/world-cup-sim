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

The work itself lives in `worldcup.data_loader.refresh_results` so the web UI's
"Update data" action shares the exact same code path.

    python scripts/update_results.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from worldcup import polymarket  # noqa: E402
from worldcup.data_loader import refresh_results  # noqa: E402


def main() -> None:
    try:
        summary = refresh_results(log=lambda m: print(m, file=sys.stderr))
    except polymarket.PolymarketError as exc:
        sys.exit(f"error: {exc}")

    print(f"wrote {summary['total']} played results "
          f"({summary['group']} group, {summary['knockout']} knockout) "
          f"to {Path(summary['path']).name}")
    skipped = summary["skipped"]
    if skipped:
        print(f"  skipped {len(skipped)} unmapped/unscored: {', '.join(skipped[:6])}"
              + ("..." if len(skipped) > 6 else ""))


if __name__ == "__main__":
    main()
