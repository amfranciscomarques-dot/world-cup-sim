"""Football Manager-style attribute model for player ratings.

A player can be described by FM attributes on the **1-20 scale**, grouped the way
you evaluate a player in FM26 rather than as a flat sum:

* **Mental** (intelligence) — Decisions, Anticipation, Positioning, Teamwork,
  Vision. In FM26 these are frequently more decisive than pure technique, so they
  carry the heaviest weight for outfield players.
* **Physical** (athleticism) — Agility (most impactful for changing direction),
  Pace, Acceleration, Stamina (vital for pressing).
* **Technical** — role-specific: Finishing for a forward, Passing for a
  midfielder, Tackling/Marking for a defender, shot-stopping for a keeper.

A player's 0-100 ``rating`` is a *position-weighted* blend of the three group
means, rescaled from 1-20 onto 0-100. The data loader calls
:func:`rating_from_attributes` whenever a player carries an ``attributes`` block,
so editing attributes (or importing a real FM export) re-derives strength
automatically — no engine changes needed.
"""

from __future__ import annotations

import hashlib
import random
from typing import Dict, List

# Universal groups (same attributes for every position).
MENTAL: List[str] = ["Decisions", "Anticipation", "Positioning", "Teamwork", "Vision"]
PHYSICAL: List[str] = ["Agility", "Pace", "Acceleration", "Stamina"]

# Technical attributes that actually matter for each position's role.
TECHNICAL_BY_POS: Dict[str, List[str]] = {
    "GK": ["Reflexes", "Handling", "OneOnOnes", "Kicking"],
    "DEF": ["Tackling", "Marking", "Heading", "Passing"],
    "MID": ["Passing", "Technique", "FirstTouch", "Dribbling"],
    "FWD": ["Finishing", "Dribbling", "FirstTouch", "Composure"],
}

# Per-position weight of each group (mental, physical, technical). Each row sums
# to 1.0. Mental leads for outfield players (FM26 intelligence > raw technique);
# keepers lean on technical shot-stopping.
POSITION_WEIGHTS: Dict[str, tuple[float, float, float]] = {
    "GK": (0.30, 0.25, 0.45),
    "DEF": (0.40, 0.30, 0.30),
    "MID": (0.45, 0.25, 0.30),
    "FWD": (0.38, 0.30, 0.32),
}

# 1-20 attribute scale maps linearly onto 0-100 (20 -> 100, 18 -> 90).
SCALE = 5.0


def technical_attrs(pos: str) -> List[str]:
    return TECHNICAL_BY_POS[pos]


def attribute_names(pos: str) -> List[str]:
    """All attribute keys relevant to a position, mental -> physical -> technical."""
    return MENTAL + PHYSICAL + technical_attrs(pos)


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def group_means(pos: str, attrs: Dict[str, float]) -> Dict[str, float]:
    """Mean (1-20) of each group given an attribute dict."""
    return {
        "mental": _mean([attrs[a] for a in MENTAL if a in attrs]),
        "physical": _mean([attrs[a] for a in PHYSICAL if a in attrs]),
        "technical": _mean([attrs[a] for a in technical_attrs(pos) if a in attrs]),
    }


def rating_from_attributes(pos: str, attrs: Dict[str, float]) -> float:
    """Position-weighted 0-100 rating from a player's FM attributes."""
    if pos not in POSITION_WEIGHTS:
        raise ValueError(f"unknown position {pos!r}")
    wm, wp, wt = POSITION_WEIGHTS[pos]
    g = group_means(pos, attrs)
    weighted = wm * g["mental"] + wp * g["physical"] + wt * g["technical"]
    return round(weighted * SCALE, 1)


# --- Attribute generation --------------------------------------------------
# Used to seed every squad with plausible FM profiles anchored to a target
# rating. Real FM exports can replace these wholesale.

# Relative emphasis per position; redistributed so the position-weighted average
# is zero, which keeps a generated profile's rating anchored to its target.
_FLAVOR: Dict[str, Dict[str, float]] = {
    "GK": {"mental": 0.4, "physical": -0.7, "technical": 0.5},
    "DEF": {"mental": 0.5, "physical": 0.2, "technical": -0.3},
    "MID": {"mental": 0.7, "physical": -0.5, "technical": 0.2},
    "FWD": {"mental": -0.2, "physical": 0.5, "technical": 0.6},
}


def _seed(name: str) -> int:
    """Stable per-name seed so generation is deterministic across runs."""
    return int.from_bytes(hashlib.md5(name.encode("utf-8")).digest()[:4], "big")


def generate_attributes(name: str, pos: str, target_rating: float) -> Dict[str, int]:
    """Invent a believable 1-20 attribute profile whose weighted rating is ~=
    ``target_rating``. Deterministic for a given name."""
    rng = random.Random(_seed(name))
    base = target_rating / SCALE                       # target level on 1-20
    wm, wp, wt = POSITION_WEIGHTS[pos]
    flavor = _FLAVOR[pos]
    avg = wm * flavor["mental"] + wp * flavor["physical"] + wt * flavor["technical"]
    group_base = {g: base + flavor[g] - avg for g in ("mental", "physical", "technical")}

    attrs: Dict[str, int] = {}

    def fill(names: List[str], gb: float) -> None:
        # Zero-mean texture offsets so the group mean stays near ``gb`` (and the
        # final rating stays near the target).
        offs = [rng.uniform(-1.7, 1.7) for _ in names]
        mo = sum(offs) / len(offs)
        for n, o in zip(names, offs):
            attrs[n] = max(1, min(20, round(gb + o - mo)))

    fill(MENTAL, group_base["mental"])
    fill(PHYSICAL, group_base["physical"])
    fill(technical_attrs(pos), group_base["technical"])
    return attrs
