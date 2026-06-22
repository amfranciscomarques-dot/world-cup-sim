"""Tests for the heat / venue factor module (H1-H6).

Each test pins down one chunk from the insight document
``Documents/Nova/insights/wc2026-heat-impact-betting-edges-20260622.md``:

    H1 — Heat-suppressed work capacity
    H2 — Pass completion paradox (press intensity dial)
    H3 — Peak-speed anomaly
    H4 — Heat → penalty correlation (knockout compression)
    H5 — Venue asymmetry (pure lookup, idempotent)
    H6 — Live hydration window (metadata only)

Plus integration tests covering the default registry and a smoke Monte Carlo
that verifies the *direction* of the heat effect on match output.
"""

import random

from worldcup.factors import (
    HeatStaminaFactor,
    HydrationWindowFactor,
    KnockoutHeatDrawFactor,
    PeakSpeedFactor,
    PressIntensityFactor,
    VenueAsymmetryFactor,
    VENUE_RISK,
    MatchContext,
    TeamMatchState,
    classify_wbgt,
    default_registry,
    enable_heat,
    venue_effective_wbgt,
)
from worldcup.models import Team


# -- fixtures ---------------------------------------------------------------

def _ctx(home_team, away_team, neutral=True, meta=None, home_extras=None, away_extras=None, stage="group"):
    return MatchContext(
        home=TeamMatchState.from_team(home_team, is_home=True, **(home_extras or {})),
        away=TeamMatchState.from_team(away_team, is_home=False, **(away_extras or {})),
        neutral=neutral,
        stage=stage,
        rng=random.Random(0),
        meta=dict(meta or {}),
    )


def _teams():
    return Team("A", 80), Team("B", 80)


# -- registry + venue data --------------------------------------------------

def test_heat_factors_in_default_registry():
    reg = default_registry()
    assert "heat_stamina" in reg.names()
    assert "press_intensity" in reg.names()
    assert "peak_speed" in reg.names()
    assert "knockout_heat_draw" in reg.names()
    assert "venue_asymmetry" in reg.names()
    assert "hydration_window" in reg.names()


def test_venue_table_covers_all_16_venues():
    assert len(VENUE_RISK) == 16, "exactly 16 tournament venues expected"
    ac_count = sum(1 for v in VENUE_RISK.values() if v.get("ac"))
    assert ac_count == 3, f"expected 3 AC venues, got {ac_count}"


def test_enable_heat_toggles_all_six():
    reg = default_registry()
    enable_heat(reg, False)
    for name in {"heat_stamina", "press_intensity", "peak_speed",
                 "knockout_heat_draw", "venue_asymmetry", "hydration_window"}:
        assert reg.get(name).enabled is False
    enable_heat(reg, True)
    for name in {"heat_stamina", "press_intensity", "peak_speed",
                 "knockout_heat_draw", "venue_asymmetry", "hydration_window"}:
        assert reg.get(name).enabled is True


# -- H5: venue asymmetry ----------------------------------------------------

def test_venue_asymmetry_noop_when_wbgt_already_set():
    a, b = _teams()
    ctx = _ctx(a, b, meta={"venue": "Monterrey", "wbgt_c": 22.0})
    VenueAsymmetryFactor().adjust(ctx)
    # Caller-provided value must not be overwritten.
    assert ctx.meta["wbgt_c"] == 22.0


def test_venue_asymmetry_fills_nominal_wbgt():
    a, b = _teams()
    # Vancouver has wbgt_offset=-6.0 in VENUE_RISK and a known nominal.
    ctx = _ctx(a, b, meta={"venue": "Vancouver"})
    VenueAsymmetryFactor().adjust(ctx)
    # Vancouver: nominal 18.0 + offset -6.0 = 12.0 (well below cooling tier).
    assert ctx.meta["wbgt_c"] == 12.0


def test_venue_asymmetry_clamps_ac_venues():
    a, b = _teams()
    ctx = _ctx(a, b, meta={"venue": "Atlanta"})
    VenueAsymmetryFactor().adjust(ctx)
    # Atlanta is AC → hard-clamped to <= 24°C.
    assert ctx.meta["wbgt_c"] <= 24.0


def test_venue_asymmetry_noop_unknown_venue():
    a, b = _teams()
    ctx = _ctx(a, b, meta={"venue": "Atlantis"})
    VenueAsymmetryFactor().adjust(ctx)
    assert "wbgt_c" not in ctx.meta


# -- H1: heat stamina -------------------------------------------------------

def test_h1_noop_below_cooling_threshold():
    a, b = _teams()
    ctx = _ctx(a, b, meta={"wbgt_c": 25.0})
    HeatStaminaFactor().adjust(ctx)
    assert ctx.home.attack == 80
    assert ctx.meta.get("heat_penalty") is None


def test_h1_penalty_scales_with_wbgt():
    a, b = _teams()
    cool = _ctx(a, b, meta={"wbgt_c": 26.5})
    HeatStaminaFactor().adjust(cool)
    severe = _ctx(a, b, meta={"wbgt_c": 30.5})
    HeatStaminaFactor().adjust(severe)
    # Severe tier must produce a deeper penalty than cooling tier.
    assert cool.meta["heat_penalty"] > severe.meta["heat_penalty"]
    assert severe.home.attack < cool.home.attack


def test_h1_penalty_capped_at_scale():
    a, b = _teams()
    ctx = _ctx(a, b, meta={"wbgt_c": 45.0})  # absurdly hot
    HeatStaminaFactor(scale=8.0, cap=12.0).adjust(ctx)
    # Penalty magnitude must not exceed cap.
    assert ctx.meta["heat_penalty"] >= -12.0


# -- H2: press intensity dial ----------------------------------------------

def test_h2_noop_below_threshold():
    a, b = _teams()
    ctx = _ctx(a, b, meta={"wbgt_c": 22.0},
               home_extras={"tactical_bias": 0.9},
               away_extras={"tactical_bias": -0.9})
    PressIntensityFactor().adjust(ctx)
    assert ctx.home.attack == 80
    assert ctx.away.attack == 80


def test_h2_high_press_in_heat_swaps_attack_for_defense():
    a, b = _teams()
    ctx = _ctx(a, b, meta={"wbgt_c": 30.0},
               home_extras={"tactical_bias": 0.9},
               away_extras={"tactical_bias": -0.9})
    PressIntensityFactor().adjust(ctx)
    # High-press home: attack should rise, defense should fall.
    assert ctx.home.attack > 80
    assert ctx.home.defense < 80
    # Low-block away: opposite.
    assert ctx.away.attack < 80
    assert ctx.away.defense > 80


def test_h2_zero_bias_is_neutral():
    a, b = _teams()
    ctx = _ctx(a, b, meta={"wbgt_c": 30.0})
    PressIntensityFactor().adjust(ctx)
    assert ctx.home.attack == 80


# -- H3: peak-speed anomaly -------------------------------------------------

def test_h3_noop_below_unsafe_threshold():
    a, b = _teams()
    ctx = _ctx(a, b, meta={"wbgt_c": 27.0})
    PeakSpeedFactor().adjust(ctx)
    assert ctx.home.attack == 80


def test_h3_fires_at_unsafe_wbgt():
    a, b = _teams()
    # Run many trials with the same seed pattern to get a stable average.
    samples = []
    for seed in range(50):
        ctx = MatchContext(
            home=TeamMatchState.from_team(a, is_home=True),
            away=TeamMatchState.from_team(b, is_home=False),
            neutral=True, rng=random.Random(seed),
            meta={"wbgt_c": 30.5},
        )
        PeakSpeedFactor().adjust(ctx)
        samples.append(ctx.home.attack - 80)
    mean_shock = sum(samples) / len(samples)
    # Mean should be positive (the anomaly is upward on attack).
    assert mean_shock > 0


# -- H4: knockout heat draw -------------------------------------------------

def test_h4_noop_in_group_stage():
    a, b = _teams()
    ctx = _ctx(a, b, meta={"wbgt_c": 29.0}, stage="group")
    KnockoutHeatDrawFactor().adjust(ctx)
    assert ctx.home.attack == 80


def test_h4_compresses_both_sides_in_knockout():
    a, b = _teams()
    ctx = _ctx(a, b, meta={"wbgt_c": 29.0}, stage="QF")
    KnockoutHeatDrawFactor().adjust(ctx)
    # Both sides should compress (negative delta on attack and defense).
    assert ctx.home.attack < 80
    assert ctx.home.defense < 80
    assert ctx.away.attack < 80
    assert ctx.away.defense < 80


def test_h4_compression_scales_with_wbgt():
    a, b = _teams()
    cooling = _ctx(a, b, meta={"wbgt_c": 26.5}, stage="QF")
    KnockoutHeatDrawFactor().adjust(cooling)
    severe = _ctx(a, b, meta={"wbgt_c": 30.5}, stage="QF")
    KnockoutHeatDrawFactor().adjust(severe)
    assert severe.home.attack < cooling.home.attack


# -- H6: hydration window metadata ------------------------------------------

def test_h6_marks_hydration_required_at_cooling_tier():
    a, b = _teams()
    ctx = _ctx(a, b, meta={"venue": "Miami", "wbgt_c": 27.0})
    HydrationWindowFactor().adjust(ctx)
    assert ctx.meta["hydration_required"] is True
    assert ctx.meta["hydration_tier"] == "cooling"


def test_h6_skips_ac_venues():
    a, b = _teams()
    ctx = _ctx(a, b, meta={"venue": "Atlanta", "wbgt_c": 30.0})
    HydrationWindowFactor().adjust(ctx)
    assert ctx.meta["hydration_required"] is False
    assert ctx.meta["hydration_tier"] == "safe"


def test_h6_severe_tier_at_extreme_wbgt():
    a, b = _teams()
    ctx = _ctx(a, b, meta={"venue": "Phoenix", "wbgt_c": 31.0})
    HydrationWindowFactor().adjust(ctx)
    assert ctx.meta["hydration_tier"] == "severe"


# -- helpers ----------------------------------------------------------------

def test_classify_wbgt_bands():
    assert classify_wbgt(None) == "safe"
    assert classify_wbgt(20.0) == "safe"
    assert classify_wbgt(26.0) == "cooling"
    assert classify_wbgt(28.0) == "unsafe"
    assert classify_wbgt(30.0) == "severe"


def test_venue_effective_wbgt_clamps_ac():
    # AC venues clamp to 24°C regardless of nominal.
    assert venue_effective_wbgt("Atlanta", 30.0) == 24.0
    assert venue_effective_wbgt("Dallas", 28.0) == 24.0


def test_venue_effective_wbgt_applies_outdoor_offset():
    # Monterrey has wbgt_offset=+5.0.
    assert venue_effective_wbgt("Monterrey", 25.0) == 30.0


def test_venue_effective_wbgt_none_passes_through():
    assert venue_effective_wbgt("Monterrey", None) is None
    assert venue_effective_wbgt(None, 28.0) == 28.0


# -- integration: full registry with heat ----------------------------------

def test_full_registry_no_op_without_meta():
    reg = default_registry()
    a, b = _teams()
    ctx = MatchContext(
        home=TeamMatchState.from_team(a, is_home=True),
        away=TeamMatchState.from_team(b, is_home=False),
        neutral=True, rng=random.Random(0),
    )
    reg.apply(ctx)
    assert ctx.home.attack == 80
    assert ctx.away.attack == 80


def test_full_registry_at_monterrey_severe_wbgt():
    reg = default_registry()
    a, b = _teams()
    ctx = MatchContext(
        home=TeamMatchState.from_team(a, is_home=True, tactical_bias=0.6),
        away=TeamMatchState.from_team(b, is_home=False, tactical_bias=-0.6),
        neutral=True, stage="group", rng=random.Random(0),
        meta={"venue": "Monterrey", "wbgt_c": 30.0},
    )
    reg.apply(ctx)
    # Heat stamina + peak speed + press dial should all move strengths.
    assert ctx.home.attack != 80 or ctx.home.defense != 80
    # Hydration metadata should be set.
    assert ctx.meta["hydration_required"] is True
    assert ctx.meta["hydration_tier"] == "severe"


# -- smoke: monte carlo direction-of-effect ---------------------------------

def test_monte_carlo_heat_reduces_total_goals():
    """Smoke test: severe heat should reduce total match goals (H1 + pace mult)."""
    from worldcup.data_loader import load_world_cup
    from worldcup.engine import MatchSimulator

    teams, _ = load_world_cup()
    # Pick two reasonable teams that exist in the data.
    home = next(t for t in teams.values() if t.name in {"USA", "Mexico", "Brazil", "Germany"})
    away = next(t for t in teams.values() if t.name in {"Switzerland", "Canada", "Morocco", "Scotland"} and t.name != home.name)

    reg = default_registry()
    sim = MatchSimulator(registry=reg, rng=random.Random(42))

    cold = sim.monte_carlo(home, away, 500, stage="group", neutral=True)
    hot = sim.monte_carlo(home, away, 500, stage="group", neutral=True,
                          meta={"venue": "Monterrey", "wbgt_c": 30.0})

    cold_goals = cold.avg_home_goals + cold.avg_away_goals
    hot_goals = hot.avg_home_goals + hot.avg_away_goals
    # Direction must hold: heat reduces total goals.
    assert hot_goals < cold_goals, (
        f"expected heat to reduce goals, got cold={cold_goals:.3f} hot={hot_goals:.3f}"
    )
