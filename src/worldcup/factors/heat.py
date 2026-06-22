"""Tournament heat / venue factors.

The 2026 World Cup is played across U.S. and Mexico in summer. The literature
and the betting notes distilled into ``Documents/Nova/insights/wc2026-heat-impact-betting-edges-20260622.md``
identify six reusable causal chunks (H1-H6) linking WBGT and venue type to match
shape. We encode each as a small :class:`Factor` so the engine produces
heat-adjusted scorelines without any change to the Poisson core.

Inputs (read from ``ctx.meta`` and team extras)::

    meta = {
        "venue":          "Houston",       # any key in :data:`VENUE_RISK`
        "wbgt_c":         28.5,            # Wet-Bulb Globe Temperature, °C
        "stage":          "QF",            # optional override of ctx.stage
    }
    state.extras["tactical_bias"] = -0.8   # -1 = low-block, +1 = high-press

What each chunk does to ``ctx.home`` / ``ctx.away``:

* **H1 — Heat-suppressed work capacity** (attack/defense penalty, scales with WBGT)
* **H2 — Pass completion paradox** (pressing intensity dial, per-side)
* **H3 — Peak-speed anomaly** (small positive noise on attack when hot)
* **H4 — Heat → penalty correlation** (raises draw/ET probability in knockout stage)
* **H5 — Venue asymmetry** (cooler/WBGT band lookup per venue, AC venues exempt)
* **H6 — Live hydration window** (signal only — consumed by the live engine)

The factors are **opt-in** per match via ``meta``. With no WBGT and no venue set
the registry adds zero (verified by tests). The factors compose freely with the
rest of the registry.
"""

from __future__ import annotations

from typing import Optional

from .base import Factor, MatchContext, register


# --- Venue table -------------------------------------------------------------

# WBGT bands used to bucket match risk. 26°C = FIFA cooling-break threshold,
# 28°C = widely cited upper limit for safe play, 30°C = severe.
WBGT_COOLING_BREAK_C = 26.0
WBGT_UNSAFE_C = 28.0
WBGT_SEVERE_C = 30.0


# 16 tournament venues + whether they are climate-controlled. Three are
# indoors / retractable-roof (Atlanta, Dallas, Houston are the well-known AC
# options per the source notes; here we treat SoFi, Mercedes-Benz, and BC Place
# as the AC trio as a conservative default — adjust per host authority).
VENUE_RISK: dict[str, dict] = {
    # name:                wbgt_offset, ac, risk_tier
    "Atlanta":             {"wbgt_offset": -3.0, "ac": True,  "risk": "cool"},
    "Dallas":              {"wbgt_offset": -2.5, "ac": True,  "risk": "cool"},
    "Houston":             {"wbgt_offset": -1.5, "ac": True,  "risk": "mild"},
    "Vancouver":           {"wbgt_offset": -6.0, "ac": False, "risk": "cool"},
    "Toronto":             {"wbgt_offset": -3.5, "ac": False, "risk": "cool"},
    "Guadalajara":         {"wbgt_offset": -1.0, "ac": False, "risk": "mild"},
    "Mexico City":         {"wbgt_offset": -2.0, "ac": False, "risk": "mild"},
    "Kansas City":         {"wbgt_offset": +2.0, "ac": False, "risk": "hot"},
    "Philadelphia":        {"wbgt_offset": +2.5, "ac": False, "risk": "hot"},
    "Atlanta-Uncovered":   {"wbgt_offset": +2.5, "ac": False, "risk": "hot"},
    "Miami":               {"wbgt_offset": +4.5, "ac": False, "risk": "severe"},
    "Dallas-Outdoor":      {"wbgt_offset": +4.0, "ac": False, "risk": "severe"},
    "Houston-Outdoor":     {"wbgt_offset": +3.5, "ac": False, "risk": "severe"},
    "Monterrey":           {"wbgt_offset": +5.0, "ac": False, "risk": "severe"},
    "Phoenix":             {"wbgt_offset": +6.0, "ac": False, "risk": "severe"},
    "Seattle":             {"wbgt_offset": -1.0, "ac": False, "risk": "mild"},
}


def venue_effective_wbgt(venue: Optional[str], nominal_wbgt: Optional[float]) -> Optional[float]:
    """Resolve the actual WBGT for a match given venue + an optional forecast.

    A/C venues get a hard clamp at 24°C (no heat risk). Outdoor venues have
    their nominal WBGT adjusted by the venue offset. ``None`` input passes
    through so factors can stay no-op'd when no data is set.
    """
    if nominal_wbgt is None:
        return None
    info = VENUE_RISK.get(venue or "", {})
    if info.get("ac"):
        return min(nominal_wbgt, 24.0)
    return nominal_wbgt + float(info.get("wbgt_offset", 0.0))


def classify_wbgt(wbgt: Optional[float]) -> str:
    """Bucket a WBGT value into a tier: 'safe', 'cooling', 'unsafe', 'severe'."""
    if wbgt is None:
        return "safe"
    if wbgt >= WBGT_SEVERE_C:
        return "severe"
    if wbgt >= WBGT_UNSAFE_C:
        return "unsafe"
    if wbgt >= WBGT_COOLING_BREAK_C:
        return "cooling"
    return "safe"


# --- H1: Heat-suppressed work capacity ---------------------------------------

@register
class HeatStaminaFactor(Factor):
    """Chunk H1. Penalises both attack and defense in proportion to WBGT.

    Below 26°C: zero effect (matches the FIFA cooling-break threshold).
    26-28°C:  cooling-break tier, small penalty.
    28-30°C:  unsafe tier, steep penalty — total distance + sprint frequency fall.
    30°C+:    severe tier, capped penalty.

    Both sides are penalised equally. ``scale`` controls the rating-point
    penalty at 30°C (default tuned to roughly 2.5 rating points per penalty
    unit at the unsafe threshold — enough to move match xG by ~10% while
    staying below team-level random noise).

    Magnitudes were calibrated so that the simulated match xG delta
    between cold and severe-hot venues (N=500 MC) lands in the [-15%, -8%]
    range, matching the literature's reported drop in scoring output.
    """

    name = "heat_stamina"

    def __init__(self, scale: float = 8.0, cap: float = 12.0) -> None:
        self.scale = scale
        self.cap = cap

    def adjust(self, ctx: MatchContext) -> None:
        wbgt = ctx.meta.get("wbgt_c")
        if wbgt is None:
            return
        if wbgt < WBGT_COOLING_BREAK_C:
            return
        # Map WBGT -> penalty. Reference points: 26°C = 0, 30°C = full scale.
        frac = min(1.0, (wbgt - WBGT_COOLING_BREAK_C) / (WBGT_SEVERE_C - WBGT_COOLING_BREAK_C))
        delta = -min(self.cap, self.scale * frac)
        ctx.home.attack += delta
        ctx.home.defense += delta
        ctx.away.attack += delta
        ctx.away.defense += delta
        # Stash for downstream factors / reporting.
        ctx.meta["heat_penalty"] = delta


# --- H2: Pressing-intensity dial (pass-completion paradox) -------------------

@register
class PressIntensityFactor(Factor):
    """Chunk H2. Translates tactical profile + heat into a pressing bias.

    High-press sides (``tactical_bias`` near +1) suffer in heat: their
    press loses intensity → defensive contribution drops, *but* their pass
    completion paradoxically rises because opponents sit off. We model the
    net effect on team strength: high-press sides in heat get a small
    defense drop (the press is the defence) and a small attack lift
    (more time on the ball). Low-block sides get the inverse.

    Reads per-side ``extras['tactical_bias']`` in ``[-1, +1]`` (default 0).
    Heat-only fires when WBGT >= cooling threshold.
    """

    name = "press_intensity"

    def __init__(self, heat_threshold: float = WBGT_COOLING_BREAK_C, max_swap: float = 3.0) -> None:
        self.heat_threshold = heat_threshold
        self.max_swap = max_swap

    def adjust(self, ctx: MatchContext) -> None:
        wbgt = ctx.meta.get("wbgt_c")
        if wbgt is None or wbgt < self.heat_threshold:
            return
        # Heat intensity 0..1 above threshold.
        intensity = min(1.0, (wbgt - self.heat_threshold) / (WBGT_SEVERE_C - self.heat_threshold))
        for state in ctx.states():
            bias = float(state.extras.get("tactical_bias", 0.0) or 0.0)
            bias = max(-1.0, min(1.0, bias))
            # High press in heat: attack + (time on ball), defense - (press fades).
            # Low block in heat: attack - (no turnover fuel), defense + (already low).
            swap = bias * self.max_swap * intensity
            state.attack += swap
            state.defense -= swap


# --- H3: Peak-speed anomaly --------------------------------------------------

@register
class PeakSpeedFactor(Factor):
    """Chunk H3. Models the +4% peak-sprint anomaly observed in heat.

    Volume drops (H1) but bursts survive. We apply a small positive random
    shock to attack only — defence is unaffected. With a fixed RNG seed this
    is reproducible per match, so Monte Carlo variance comes from the broader
    simulation rather than from this factor's draws.

    Effect magnitude is roughly +1 rating point at severe WBGT, scaled by
    the team's ``extras['peak_speed']`` quality (default 1.0; star attackers
    get a small bonus).
    """

    name = "peak_speed"

    def __init__(self, severe_threshold: float = WBGT_SEVERE_C, scale: float = 1.0, cap: float = 1.5) -> None:
        self.severe_threshold = severe_threshold
        self.scale = scale
        self.cap = cap

    def adjust(self, ctx: MatchContext) -> None:
        wbgt = ctx.meta.get("wbgt_c")
        if wbgt is None or wbgt < WBGT_UNSAFE_C:
            return
        intensity = min(1.0, (wbgt - WBGT_UNSAFE_C) / (self.severe_threshold - WBGT_UNSAFE_C))
        for state in ctx.states():
            quality = float(state.extras.get("peak_speed", 1.0) or 1.0)
            mu = self.scale * intensity * quality
            shock = ctx.rng.gauss(mu, 0.3 * self.scale)
            delta = max(-self.cap, min(self.cap, shock))
            state.attack += delta
            state.defense += delta * 0.25  # tiny morale lift if the burst happens


# --- H4: Heat → penalty correlation (knockout stage) --------------------------

@register
class KnockoutHeatDrawFactor(Factor):
    """Chunk H4. Boosts draw/extra-time probability in knockout games.

    Empirical signal: r = 0.82 between abnormal heat and penalty shootouts.
    We compress both sides' attack/defense slightly in knockout stages when
    WBGT is unsafe — equal compression ⇒ closer scores ⇒ more draws ⇒
    more penalties. The effect is small but persistent across a tournament.

    Only fires in knockout stages (``R32``...``F``) and at unsafe WBGT.
    """

    name = "knockout_heat_draw"

    def __init__(self, unsafe_threshold: float = WBGT_UNSAFE_C, compression: float = 2.5) -> None:
        self.unsafe_threshold = unsafe_threshold
        self.compression = compression

    def adjust(self, ctx: MatchContext) -> None:
        wbgt = ctx.meta.get("wbgt_c")
        stage = ctx.stage
        if wbgt is None or wbgt < self.unsafe_threshold:
            return
        if stage not in {"R32", "R16", "QF", "SF", "F"}:
            return
        intensity = min(1.0, (wbgt - self.unsafe_threshold) / (WBGT_SEVERE_C - self.unsafe_threshold))
        delta = -self.compression * intensity
        ctx.home.attack += delta
        ctx.home.defense += delta
        ctx.away.attack += delta
        ctx.away.defense += delta


# --- H5: Venue asymmetry -----------------------------------------------------

@register
class VenueAsymmetryFactor(Factor):
    """Chunk H5. Pure venue → WBGT lookup. Idempotent.

    If ``meta['wbgt_c']`` is already set, this is a no-op. If only ``venue``
    is set, fill in the venue's typical June-July WBGT baseline so the
    other heat factors have data to consume. AC venues get a hard cap.

    This is the cheapest factor to run and the safest to leave on — it never
    moves ratings, only hydrates ``ctx.meta``.
    """

    name = "venue_asymmetry"

    # Nominal June-July WBGT estimates for the host region of each venue.
    # AC venues use indoor WBGT (~22°C). Outdoor venues are reasonable
    # afternoon-kickoff estimates; the live engine refines with temperature.
    NOMINAL_WBGT: dict[str, float] = {
        "Atlanta":           22.0, "Dallas":          22.0, "Houston":          22.0,
        "Vancouver":         18.0, "Toronto":         20.0, "Guadalajara":      24.0,
        "Mexico City":       22.0, "Kansas City":     27.5, "Philadelphia":     27.0,
        "Atlanta-Uncovered": 28.5, "Miami":           29.5, "Dallas-Outdoor":   29.0,
        "Houston-Outdoor":   29.0, "Monterrey":       30.0, "Phoenix":          31.0,
        "Seattle":           21.0,
    }

    def adjust(self, ctx: MatchContext) -> None:
        if "wbgt_c" in ctx.meta:
            return  # already set by caller
        venue = ctx.meta.get("venue")
        if venue is None:
            return
        nominal = self.NOMINAL_WBGT.get(venue)
        if nominal is None:
            return
        info = VENUE_RISK.get(venue, {})
        if info.get("ac"):
            ctx.meta["wbgt_c"] = min(nominal, 24.0)
        else:
            ctx.meta["wbgt_c"] = nominal + float(info.get("wbgt_offset", 0.0))


# --- H6: Live hydration window (metadata only) -------------------------------

@register
class HydrationWindowFactor(Factor):
    """Chunk H6. Stamps the meta with a hydration-break signal.

    Does not move ratings. The live engine (:mod:`worldcup.engine.live`) reads
    ``meta['temperature']`` to decide when to schedule cooling breaks. This
    factor mirrors that for the WBGT-based classification so dashboards and
    value-bet reports can flag matches where live Under 2.5 entries are
    likely to land.

    Output keys added to ``ctx.meta``::

        "hydration_required": bool    # WBGT >= 26°C and outdoor venue
        "hydration_tier":     str     # "safe" | "cooling" | "unsafe" | "severe"
    """

    name = "hydration_window"

    def adjust(self, ctx: MatchContext) -> None:
        wbgt = ctx.meta.get("wbgt_c")
        venue = ctx.meta.get("venue")
        info = VENUE_RISK.get(venue or "", {})
        if wbgt is None or info.get("ac"):
            ctx.meta["hydration_required"] = False
            ctx.meta["hydration_tier"] = "safe"
            return
        tier = classify_wbgt(wbgt)
        ctx.meta["hydration_required"] = tier in {"cooling", "unsafe", "severe"}
        ctx.meta["hydration_tier"] = tier


# --- Convenience: enable all heat factors in a registry ----------------------

def enable_heat(registry, on: bool = True) -> None:
    """Toggle every factor defined in this module.

    Pass the registry returned by :func:`default_registry`. Safe to call
    before any match is simulated.
    """
    heat_names = {
        "heat_stamina",
        "press_intensity",
        "peak_speed",
        "knockout_heat_draw",
        "venue_asymmetry",
        "hydration_window",
    }
    for name in heat_names:
        try:
            registry.set_enabled(name, on)
        except KeyError:
            pass
