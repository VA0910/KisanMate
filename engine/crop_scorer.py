"""Crop scorer (PROJECT_SPEC.md layer 1: the deterministic shell).

Rule-based, no AI/network calls. PROJECT_SPEC.md does not pin exact thresholds
for crop suitability (unlike the tomato prior table), so the rule table below
is an illustrative, easily-tunable default for smallholder crops grown around
Guntur -- adjust CROP_RULES if the real agronomic thresholds differ.
"""
from models import CropRecommendation

# Each crop scores 0-4 points: soil match, agro-zone match, rainfall >= its
# minimum, and groundwater depth <= its maximum (deeper water = costlier to
# irrigate, so shallower-tolerant crops need a shallower table to still score).
CROP_RULES: dict[str, dict] = {
    "rice": {
        "soils": {"alluvial", "black", "loamy"},
        "zones": {"delta", "coastal"},
        "min_rainfall_mm": 800,
        "max_groundwater_depth_m": 5.0,
        "reason": "high_rainfall_shallow_water_suits_paddy",
    },
    "tomato": {
        "soils": {"black", "red", "loamy", "alluvial"},
        "zones": {"coastal", "upland", "semi_arid"},
        "min_rainfall_mm": 400,
        "max_groundwater_depth_m": 15.0,
        "reason": "moderate_water_well_drained_soil_suits_tomato",
    },
    "chili": {
        "soils": {"black", "red"},
        "zones": {"upland", "semi_arid", "coastal"},
        "min_rainfall_mm": 350,
        "max_groundwater_depth_m": 15.0,
        "reason": "well_drained_soil_moderate_rainfall_suits_chili",
    },
    "cotton": {
        "soils": {"black"},
        "zones": {"semi_arid", "upland"},
        "min_rainfall_mm": 500,
        "max_groundwater_depth_m": 20.0,
        "reason": "black_soil_semi_arid_zone_suits_cotton",
    },
    "groundnut": {
        "soils": {"red", "sandy", "loamy"},
        "zones": {"semi_arid", "upland"},
        "min_rainfall_mm": 300,
        "max_groundwater_depth_m": 25.0,
        "reason": "low_water_need_suits_deep_groundwater_zones",
    },
    "maize": {
        "soils": {"red", "loamy", "alluvial", "black"},
        "zones": {"upland", "semi_arid", "coastal"},
        "min_rainfall_mm": 400,
        "max_groundwater_depth_m": 20.0,
        "reason": "adaptable_crop_moderate_rainfall_suits_maize",
    },
}


def score_crops(
    soil: str,
    groundwater_depth_m: float,
    agro_zone: str,
    seasonal_rainfall_mm: float,
) -> list[CropRecommendation]:
    """Rank candidate crops by how many of the 4 rule-of-thumb criteria they meet."""
    scored = []
    for crop, rule in CROP_RULES.items():
        score = 0
        if soil in rule["soils"]:
            score += 1
        if agro_zone in rule["zones"]:
            score += 1
        if seasonal_rainfall_mm >= rule["min_rainfall_mm"]:
            score += 1
        if groundwater_depth_m <= rule["max_groundwater_depth_m"]:
            score += 1
        scored.append(CropRecommendation(crop=crop, score=score, reason_code=rule["reason"]))

    scored.sort(key=lambda rec: rec.score, reverse=True)
    return scored[:3]
