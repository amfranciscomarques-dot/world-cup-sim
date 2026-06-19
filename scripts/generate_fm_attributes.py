"""Populate data/teams_2026.json with Football Manager attribute profiles.

For every player, generate a believable 1-20 attribute block (Mental / Physical /
Technical) anchored to their existing curated ``rating`` using
``worldcup.fm_rating.generate_attributes``. The original file is backed up to
``teams_2026.baseline.json`` first. Re-running is deterministic (seeded by name).

    python scripts/generate_fm_attributes.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from worldcup.fm_rating import generate_attributes, rating_from_attributes  # noqa: E402

DATA = ROOT / "data" / "teams_2026.json"
BACKUP = ROOT / "data" / "teams_2026.baseline.json"


def main() -> None:
    raw = json.loads(DATA.read_text(encoding="utf-8"))
    if not BACKUP.exists():
        shutil.copyfile(DATA, BACKUP)
        print(f"backed up original -> {BACKUP.name}")

    raw["_about"] = (
        "Base team ratings derive from the June 2026 FIFA Men's World Ranking "
        "(0-100). Each player carries Football Manager attributes (1-20) grouped "
        "into Mental/Physical/Technical; worldcup.fm_rating computes the player's "
        "0-100 rating from them with position-specific weights. 'rating' is kept "
        "as a fallback/anchor. Edit attributes or import a real FM export to "
        "re-derive strengths automatically."
    )

    total = 0
    filled = 0
    max_drift = 0.0
    for entry in raw["teams"]:
        for p in entry.get("players", []):
            total += 1
            # Preserve hand-curated attribute blocks; only generate for depth
            # players that have none (otherwise their FM stats render as 0.0).
            if p.get("attributes"):
                continue
            anchor = float(p["rating"])
            attrs = generate_attributes(p["name"], p["pos"], anchor)
            p["attributes"] = attrs
            derived = rating_from_attributes(p["pos"], attrs)
            max_drift = max(max_drift, abs(derived - anchor))
            filled += 1

    DATA.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"filled attributes for {filled} of {total} players across {len(raw['teams'])} teams")
    print(f"({total - filled} players kept their curated attributes)")
    print(f"max rating drift from anchor: {max_drift:.1f}")


if __name__ == "__main__":
    main()
