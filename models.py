"""Pydantic models for KisanMate: Gemini I/O contracts and Firestore document shapes.

Field names and nesting mirror PROJECT_SPEC.md exactly.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

Condition = Literal[
    "late_blight", "early_blight", "nitrogen_deficiency", "other", "healthy"
]
Language = Literal["en", "hi", "te"]


# --- shared geo shapes -----------------------------------------------------

class LatLng(BaseModel):
    lat: float
    lng: float


class FarmerLocation(LatLng):
    mandal: str


class ContextLocation(LatLng):
    resolution: Literal["plot", "village", "mandal", "zone"]


# --- vision output contract -------------------------------------------------

class VisionCandidate(BaseModel):
    # Free-text condition id (e.g. "late_blight", "rice_blast", "healthy"): the
    # candidate diseases come from the identified crop's DB entry, so this is no
    # longer limited to the tomato set (Condition is the calibrated-tomato subset).
    condition: str
    confidence: float
    visible_symptoms: list[str] = Field(default_factory=list)


class VisionOutput(BaseModel):
    image_quality: Literal["good", "poor"]
    crop_confirmed: str
    # The plant/crop the model identifies in the photo (identified FIRST), or
    # "unidentifiable" when it cannot tell. matches_profile is set authoritatively
    # by the backend against the farmer's current_crops (PROJECT_SPEC.md).
    identified_crop: str = ""
    matches_profile: bool = False
    candidates: list[VisionCandidate] = Field(default_factory=list)
    notes: str = ""


# --- fusion input contract --------------------------------------------------

class Weather(BaseModel):
    temp_c: float
    humidity_pct: float
    rain_48h_mm: float
    source: Literal["live", "cache", "zone_normal"]


class Forecast(BaseModel):
    """7-day rain outlook, used only for the irrigation dry-spell reminder
    (engine.reminders) -- separate from Weather above, which feeds the
    diagnosis prior and only looks at current/past conditions."""
    precip_next7_mm: float
    rain_prob_max_pct: float
    dry_spell: bool
    source: Literal["live"]


class Soil(BaseModel):
    nitrogen: Literal["low", "adequate", "unknown"]
    source: Literal["card", "cache", "unknown"]
    # The farmer's declared soil type (e.g. "black"); additive to the contract,
    # carried so the AI layer can personalize ("for your black soil...").
    type: Optional[str] = None


class NearbyConfirmed(BaseModel):
    condition: str
    distance_km: float
    age_days: int


class FusionContext(BaseModel):
    crop: str
    location: ContextLocation
    weather: Weather
    soil: Soil
    nearby_confirmed: list[NearbyConfirmed] = Field(default_factory=list)
    # Current growth stage computed deterministically from planting_date + the
    # crop DB (engine.growth). Additive to the contract; the deterministic fusion
    # ignores it, but it personalizes the AI layer ("...at the flowering stage").
    growth_stage: Optional[str] = None
    crop_day: Optional[int] = None
    # Cropping season derived from today's date (kharif|rabi|zaid) and the crop DB's
    # susceptible diseases for the diagnosed crop -- both additive, used only to
    # personalize/ground the AI explanation, never the deterministic fusion.
    season: Optional[str] = None
    susceptible_diseases: list[str] = Field(default_factory=list)


class FusionInput(BaseModel):
    vision: VisionOutput
    context: FusionContext


# --- fusion output contract -------------------------------------------------

class PosteriorEntry(BaseModel):
    condition: str
    score: float
    contagious: bool


class FusionEvidence(BaseModel):
    vision_top: str
    prior_top: str
    context_completeness: list[str] = Field(default_factory=list)


class FusionOutput(BaseModel):
    posterior: list[PosteriorEntry]
    top: str
    confidence: float
    margin: float
    conflict: bool
    decision: Literal["advise", "escalate_rsk"]
    alert_eligible: bool
    evidence: FusionEvidence


# --- firestore: crops/{crop_id} ---------------------------------------------

class CropNames(BaseModel):
    en: str
    hi: str
    te: str


class GrowthStage(BaseModel):
    name: str
    start_day: int
    care_note: str


class Crop(BaseModel):
    """A crop in the crops/{crop_id} collection (PROJECT_SPEC.md).

    This single collection is the data backbone: it feeds crop recommendations
    (soil/season/region match), the diagnosis prior and community-alert crop
    filter (susceptible_diseases), and reminders (cycle_days + growth_stages).
    """
    id: Optional[str] = None
    names: CropNames
    seasons: list[Literal["kharif", "rabi", "zaid"]] = Field(default_factory=list)
    soil_types: list[str] = Field(default_factory=list)
    water_need: Literal["low", "medium", "high"]
    regions: list[str] = Field(default_factory=list)
    cycle_days: int
    growth_stages: list[GrowthStage] = Field(default_factory=list)
    # Condition ids the crop is susceptible to, e.g. "late_blight".
    # nitrogen_deficiency is a condition but NOT a disease -- never listed here.
    susceptible_diseases: list[str] = Field(default_factory=list)


# --- crop recommendation (engine output) ------------------------------------

class CropRecommendation(BaseModel):
    crop: str
    score: int
    reason_code: str
    # Human-readable reason (Gemini-written; deterministic string as fallback).
    reason: Optional[str] = None
    # Which deterministic criteria matched: any of "soil", "season", "region".
    matched: list[str] = Field(default_factory=list)
    # Crop names carried through for the UI (from the crops collection).
    names: Optional[CropNames] = None


# --- firestore: farmers/{id} -------------------------------------------------

class CurrentCrop(BaseModel):
    """A crop the farmer is currently growing. planting_date (ISO YYYY-MM-DD) is
    required per crop -- it's what makes memory/reminders possible (PROJECT_SPEC.md)."""
    crop_id: str
    planting_date: str


class Farmer(BaseModel):
    id: Optional[str] = None
    name: str
    phone: str
    lang: Language
    location: FarmerLocation
    crop: str
    land_size_acres: float
    growth_stage: str
    # Profile additions (PROJECT_SPEC.md): feed recommendations, diagnosis
    # context, and reminders.
    soil_type: Optional[str] = None
    current_crops: list[CurrentCrop] = Field(default_factory=list)


# --- firestore: cases/{id} ---------------------------------------------------

class Case(BaseModel):
    id: Optional[str] = None
    farmer_id: str
    image_note: Optional[str] = None
    # Small (downscaled) photo as a data URL, so the officer portal can show it.
    image_data: Optional[str] = None
    vision: Optional[VisionOutput] = None
    context: Optional[FusionContext] = None
    fusion: Optional[FusionOutput] = None
    status: Literal["pending", "advised", "escalated", "disputed", "confirmed"] = "pending"
    officer_verdict: Optional[str] = None
    condition: Optional[str] = None
    contagious: Optional[bool] = None
    # Whether the farmer has already been shown the RSK officer's verdict popup.
    # Set False when a verdict is recorded (confirm), flipped True once the farmer
    # has seen it, so the one-time "your case was reviewed" popup fires exactly once.
    verdict_seen: bool = False
    created_at: Optional[datetime] = None


# --- firestore: alerts/{id} ---------------------------------------------------

class Alert(BaseModel):
    id: Optional[str] = None
    source_case_id: str
    condition: str
    tier: Literal["watch", "warning", "alert"]
    center: LatLng
    radius_km: float
    recipient_ids: list[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None


# --- firestore: telemetry/{id} -------------------------------------------------

class Telemetry(BaseModel):
    id: Optional[str] = None
    event: str
    layer: str
    detail: Optional[dict] = None
    fallback_used: bool = False
    created_at: Optional[datetime] = None
