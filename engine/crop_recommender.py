"""Collection-driven crop recommender (PROJECT_SPEC.md layer 1: deterministic shell).

Ranks the crops in the crops/{crop_id} collection for a farmer by matching three
simple, deterministic criteria against each crop's own fields:
  - soil_type -> crop.soil_types
  - current season (kharif/rabi/zaid) -> crop.seasons
  - region (agro-climatic zone) -> crop.regions

The ranking is pure Python with no AI/network calls, so it always produces an
answer. Gemini only rewrites the reason strings into warm language on top of the
deterministic reason built here (see explain.explain_recommendations); if Gemini
is unavailable, these deterministic reasons stand on their own.
"""
from datetime import date
from typing import Optional

from models import Crop, CropRecommendation

# How many ranked crops the recommender returns.
TOP_N = 4

# The frontend soil chips use "loamy"; the crops schema uses the spec token "loam".
_SOIL_ALIASES = {"loamy": "loam"}

_SEASON_LABEL = {"kharif": "kharif (monsoon)", "rabi": "rabi (winter)", "zaid": "zaid (summer)"}


def current_season(today: Optional[date] = None) -> str:
    """Indian cropping season for a date: kharif (Jun-Oct), rabi (Nov-Mar), zaid (Apr-May)."""
    month = (today or date.today()).month
    if 6 <= month <= 10:
        return "kharif"
    if month == 11 or month <= 3:
        return "rabi"
    return "zaid"


def _normalize_soil(soil: Optional[str]) -> Optional[str]:
    if not soil:
        return None
    soil = soil.strip().lower()
    return _SOIL_ALIASES.get(soil, soil)


def _deterministic_reason(
    matched: list[str],
    soil_type: Optional[str],
    season: Optional[str],
    region: Optional[str],
) -> str:
    """A plain-English reason built from the criteria that matched -- the fallback
    used verbatim whenever the Gemini rewrite is unavailable."""
    parts = []
    if "soil" in matched and soil_type:
        parts.append(f"your {soil_type} soil")
    if "season" in matched and season:
        parts.append(f"the current {_SEASON_LABEL.get(season, season)} season")
    if "region" in matched and region:
        parts.append(f"your {region} area")
    if not parts:
        return "A generally hardy option, though it is not a strong match for your soil, season, or area."
    if len(parts) == 1:
        return f"Well suited to {parts[0]}."
    return "Well suited to " + ", ".join(parts[:-1]) + f" and {parts[-1]}."


def _reason_code(matched: list[str]) -> str:
    return "match_" + "_".join(matched) if matched else "general_suitability"


def rank_crops(
    crops: list[Crop],
    soil_type: Optional[str] = None,
    season: Optional[str] = None,
    region: Optional[str] = None,
    limit: int = TOP_N,
) -> list[CropRecommendation]:
    """Rank crops from the collection by how many of soil/season/region they match.

    Deterministic: score is the count of matched criteria (0-3); ties break by
    crop id so the ordering is stable and reproducible for the demo. `limit`
    controls how many are returned (default TOP_N; larger for a grounding shortlist).
    """
    soil_type = _normalize_soil(soil_type)

    scored: list[tuple[int, str, CropRecommendation]] = []
    for crop in crops:
        matched: list[str] = []
        if soil_type and soil_type in {s.strip().lower() for s in crop.soil_types}:
            matched.append("soil")
        if season and season in crop.seasons:
            matched.append("season")
        if region and region.strip().lower() in {r.strip().lower() for r in crop.regions}:
            matched.append("region")

        rec = CropRecommendation(
            crop=crop.id or crop.names.en.lower(),
            score=len(matched),
            reason_code=_reason_code(matched),
            reason=_deterministic_reason(matched, soil_type, season, region),
            matched=matched,
            names=crop.names,
        )
        scored.append((len(matched), rec.crop, rec))

    scored.sort(key=lambda t: (-t[0], t[1]))
    return [rec for _, _, rec in scored[:limit]]
