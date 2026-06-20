"""Snapshot Polymarket odds into data/odds_2026.json (an offline cache).

Captures, in a single pass:
  * the tournament-winner market (per-team ``Yes`` price = implied probability),
  * the reach Round-of-16 / Quarterfinals futures,
  * every group-winner market, and
  * each fixture's three-way odds — live prices, plus, for played games, the
    pre-kickoff odds recovered from CLOB price history.

The HTML dashboard reads this file so it renders fast, offline and reproducibly
instead of making ~100 network calls every time (CLOB history is one call per
played-game side). Re-running overwrites it with whatever Polymarket currently
reports; a missing file just means "no market columns".

The work itself lives in `worldcup.odds_store.refresh_odds` so the web UI's
"Update data" action shares the exact same code path.

    python scripts/update_odds.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from worldcup import polymarket  # noqa: E402
from worldcup.odds_store import refresh_odds  # noqa: E402


def main() -> None:
    try:
        summary = refresh_odds(log=lambda m: print(m, file=sys.stderr))
    except polymarket.PolymarketError as exc:
        sys.exit(f"error: {exc}")

    print(f"wrote {Path(summary['path']).name}: {summary['winner']} winner lines, "
          f"{summary['games']} fixtures ({summary['pregame']} with pre-kickoff history)")


if __name__ == "__main__":
    main()
