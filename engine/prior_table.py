"""Tomato prior table (PROJECT_SPEC.md "Prior table (tomato) -- deterministic").

prior_score = base + sum(weight * factor_active), normalized across the three
tracked conditions. No AI, no I/O -- a pure function of the fusion context.
"""
from models import Condition, FusionContext

TOMATO_CONDITIONS: tuple[Condition, ...] = (
    "late_blight",
    "early_blight",
    "nitrogen_deficiency",
)

# Whether each condition can spread farmer-to-farmer; drives both the nearby-
# confirmed prior bonus below and the alert engine's contagion checks.
CONTAGIOUS: dict[Condition, bool] = {
    "late_blight": True,
    "early_blight": True,
    "nitrogen_deficiency": False,
}


def compute_prior(context: FusionContext) -> dict[Condition, float]:
    """Return normalized prior scores for late_blight / early_blight / nitrogen_deficiency."""
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
