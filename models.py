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
    condition: Condition
    confidence: float
    visible_symptoms: list[str] = Field(default_factory=list)


class VisionOutput(BaseModel):
    image_quality: Literal["good", "poor"]
    crop_confirmed: str
    candidates: list[VisionCandidate] = Field(default_factory=list)
    notes: str = ""


# --- fusion input contract --------------------------------------------------

class Weather(BaseModel):
    temp_c: float
    humidity_pct: float
    rain_48h_mm: float
    source: Literal["live", "cache", "zone_normal"]


class Soil(BaseModel):
    nitrogen: Literal["low", "adequate", "unknown"]
    source: Literal["card", "cache", "unknown"]


class NearbyConfirmed(BaseModel):
    condition: Condition
    distance_km: float
    age_days: int


class FusionContext(BaseModel):
    crop: str
    location: ContextLocation
    weather: Weather
    soil: Soil
    nearby_confirmed: list[NearbyConfirmed] = Field(default_factory=list)


class FusionInput(BaseModel):
    vision: VisionOutput
    context: FusionContext


# --- fusion output contract -------------------------------------------------

class PosteriorEntry(BaseModel):
    condition: Condition
    score: float
    contagious: bool


class FusionEvidence(BaseModel):
    vision_top: str
    prior_top: str
    context_completeness: list[str] = Field(default_factory=list)


class FusionOutput(BaseModel):
    posterior: list[PosteriorEntry]
    top: Condition
    confidence: float
    margin: float
    conflict: bool
    decision: Literal["advise", "escalate_rsk"]
    alert_eligible: bool
    evidence: FusionEvidence


# --- crop recommendation (engine/crop_scorer.py output) ---------------------

class CropRecommendation(BaseModel):
    crop: str
    score: int
    reason_code: str


# --- firestore: farmers/{id} -------------------------------------------------

class Farmer(BaseModel):
    id: Optional[str] = None
    name: str
    phone: str
    lang: Language
    location: FarmerLocation
    crop: str
    land_size_acres: float
    growth_stage: str


# --- firestore: cases/{id} ---------------------------------------------------

class Case(BaseModel):
    id: Optional[str] = None
    farmer_id: str
    image_note: Optional[str] = None
    vision: Optional[VisionOutput] = None
    context: Optional[FusionContext] = None
    fusion: Optional[FusionOutput] = None
    status: Literal["pending", "advised", "escalated", "confirmed"] = "pending"
    officer_verdict: Optional[str] = None
    condition: Optional[Condition] = None
    contagious: Optional[bool] = None
    created_at: Optional[datetime] = None


# --- firestore: alerts/{id} ---------------------------------------------------

class Alert(BaseModel):
    id: Optional[str] = None
    source_case_id: str
    condition: Condition
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
