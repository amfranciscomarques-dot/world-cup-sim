"""Tests for the Football Manager attribute -> rating model."""

from __future__ import annotations

import pytest

from worldcup.data_loader import load_world_cup
from worldcup.fm_rating import (
    POSITION_WEIGHTS,
    attribute_names,
    generate_attributes,
    group_means,
    rating_from_attributes,
)


def test_position_weights_sum_to_one():
    for pos, (m, p, t) in POSITION_WEIGHTS.items():
        assert abs(m + p + t - 1.0) < 1e-9, pos


def test_all_max_attributes_give_100():
    for pos in POSITION_WEIGHTS:
        attrs = {name: 20 for name in attribute_names(pos)}
        assert rating_from_attributes(pos, attrs) == 100.0


def test_generation_is_deterministic():
    a = generate_attributes("Some Player", "MID", 85)
    b = generate_attributes("Some Player", "MID", 85)
    assert a == b


def test_generated_rating_stays_near_anchor():
    # Anchored generation should reproduce the target within a couple of points.
    for pos in POSITION_WEIGHTS:
        for target in (70, 80, 88):
            attrs = generate_attributes(f"P{pos}{target}", pos, target)
            derived = rating_from_attributes(pos, attrs)
            assert abs(derived - target) <= 2.5, (pos, target, derived)


def test_attributes_in_range():
    attrs = generate_attributes("Edge Case", "FWD", 92)
    assert all(1 <= v <= 20 for v in attrs.values())


def test_loader_derives_rating_from_attributes():
    teams, _ = load_world_cup()
    messi = teams["Argentina"].player("Lionel Messi")
    assert messi.attributes, "expected FM attributes on loaded players"
    # Loaded rating must match the model applied to the stored attributes.
    assert messi.rating == rating_from_attributes(messi.pos, messi.attributes)


def test_unknown_position_rejected():
    with pytest.raises(ValueError):
        rating_from_attributes("XX", {})
