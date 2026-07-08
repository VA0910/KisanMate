"""Tomato prior table (PROJECT_SPEC.md "Prior table (tomato) -- deterministic").

prior_score = base + sum(weight * factor_active), normalized across the tracked
conditions. No AI, no I/O -- a pure function of the fusion context.

"healthy" is tracked alongside the three diseases (flat base, no weather/soil
bonuses -- those factors only encode disease risk) so that a confident "healthy"
vision reading can actually win the posterior instead of being silently dropped
from consideration and forcing a disease label even when the photo shows none
of the tracked diseases.
"""
from models import Condition, FusionContext

TOMATO_CONDITIONS: tuple[Condition, ...] = (
    "late_blight",
    "early_blight",
    "nitrogen_deficiency",
    "healthy",
)

# Whether each tomato condition can spread farmer-to-farmer; drives both the
# nearby-confirmed prior bonus below and the alert engine's contagion checks.
CONTAGIOUS: dict[Condition, bool] = {
    "late_blight": True,
    "early_blight": True,
    "nitrogen_deficiency": False,
}

# Conditions that are never contagious regardless of crop.
NON_CONTAGIOUS_CONDITIONS = {"healthy", "other", "nitrogen_deficiency"}


def is_contagious(condition: str) -> bool:
    """Whether a (possibly non-tomato) condition can spread.

    Uses the calibrated tomato map where available; otherwise a named disease
    from the crops DB (e.g. "rice_blast", "bacterial_leaf_blight") is treated as
    contagious, while healthy/other/nutrient conditions are not -- so the
    community-alert gate works for any crop's diseases, not just tomato's.
    """
    if condition in CONTAGIOUS:
        return CONTAGIOUS[condition]
    if not condition or condition in NON_CONTAGIOUS_CONDITIONS:
        return False
    return True


def compute_prior(context: FusionContext) -> dict[Condition, float]:
    """Return normalized prior scores for late_blight / early_blight / nitrogen_deficiency / healthy."""
    scores: dict[Condition, float] = {c: 1.0 for c in TOMATO_CONDITIONS}  # base

    temp = context.weather.temp_c
    if 10 <= temp <= 20:
        scores["late_blight"] += 2.0
    if 27 <= temp <= 35:
        scores["late_blight"] -= 1.5
        scores["early_blight"] += 1.5

    if context.weather.humidity_pct > 85:
        scores["late_blight"] += 2.0
        scores["early_blight"] += 1.5

    if context.weather.rain_48h_mm > 0:  # rain / leaf-wetness in the last 48h
        scores["late_blight"] += 1.5
        scores["early_blight"] += 0.5

    if context.soil.nitrogen == "low":
        scores["nitrogen_deficiency"] += 3.0

    for nearby in context.nearby_confirmed:
        if CONTAGIOUS.get(nearby.condition, False):
            scores[nearby.condition] = scores.get(nearby.condition, 0.0) + 3.0

    scores = {c: max(score, 0.0) for c, score in scores.items()}
    total = sum(scores.values()) or 1.0
    return {c: score / total for c, score in scores.items()}
