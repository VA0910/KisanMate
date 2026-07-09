import base64
import json
import os
import random
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import assistant as assistant_mod
import demo
import firestore_client
import mandi
import services
import weather as weather_source
from engine.crop_recommender import current_season, rank_crops
from engine.fusion import fuse
from engine.growth import compute_stage
from engine.reminders import reminders_for_farmer
from engine.prior_table import TOMATO_CONDITIONS, is_contagious
from explain import (
    ExplainError,
    RecommendExplainError,
    combine_explanation,
    explain_fusion,
    explain_recommendations,
    fallback_recommendation,
    recommend_conversational,
    recommendation_note,
    template_explanation,
    template_message,
)
from models import (
    Case,
    ContextLocation,
    Farmer,
    FarmerLocation,
    FusionContext,
    Soil,
    Telemetry,
)
from vision import VisionError, diagnose_image

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="KisanMate API")


@app.middleware("http")
async def revalidate_frontend_assets(request, call_next):
    """Force browsers to revalidate the HTML/CSS/JS (no aggressive caching), so a
    new deploy is picked up immediately instead of a stale cached bundle leaving
    the UI on old behavior. Etags still make revalidation cheap (304s)."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static") or path in ("/", "/admin", "/rsk", "/log"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# The farmer-facing frontend: plain HTML/CSS/JS, no build step (PROJECT_SPEC.md).
# Mounted under /static so it never shadows the /api/* routes below; "/" itself
# serves static/index.html so the app still opens at the site root.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/rsk")
def rsk_dashboard():
    """Legacy alias for the officer dashboard; the current portal lives at /admin."""
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/admin")
def admin_portal():
    """The RSK officer portal -- a SEPARATE, denser desk app behind its own login
    (not the farmer's phone/OTP). The human-in-the-loop confirmation gate.
    """
    return FileResponse(STATIC_DIR / "admin.html")


# Demo officer credentials (PROJECT_SPEC.md): shown on the login page so a judge
# can sign in. Overridable via env; real authentication is the production step.
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "officer")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "rsk2024")


class AdminLoginRequest(BaseModel):
    username: str
    password: str


@app.get("/api/admin/demo-credentials")
def admin_demo_credentials():
    """The demo credentials to display on the login page (like the demo OTP)."""
    return {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}


@app.post("/api/admin/login")
def admin_login(request: AdminLoginRequest):
    """Validate the officer's demo credentials and return a session token.

    Prototype auth: a fixed demo credential compared server-side. The officer
    session is kept separate from the farmer session; real admin auth (SSO /
    hashed credentials / RBAC) is the named production step.
    """
    if request.username == ADMIN_USERNAME and request.password == ADMIN_PASSWORD:
        return {"ok": True, "token": "officer-demo-session"}
    raise HTTPException(status_code=401, detail="incorrect username or password")


class ConfirmRequest(BaseModel):
    case_id: str
    officer_verdict: str


class RecommendRequest(BaseModel):
    """Inputs for a crop recommendation.

    The ranking matches soil_type, season, and region against the crops
    collection. `season` defaults to the current cropping season when omitted.
    The `soil`/`agro_zone`/groundwater/rainfall fields are the legacy names the
    current frontend form still sends; they are accepted and mapped so nothing
    breaks while the profile-driven UI is built in a later step.
    """
    farmer_id: str
    soil_type: Optional[str] = None
    region: Optional[str] = None
    season: Optional[str] = None
    # legacy field names (still sent by the current recommend form)
    soil: Optional[str] = None
    agro_zone: Optional[str] = None
    groundwater_depth_m: Optional[float] = None
    seasonal_rainfall_mm: Optional[float] = None

    def resolved_soil(self) -> Optional[str]:
        return self.soil_type or self.soil

    def resolved_region(self) -> Optional[str]:
        return self.region or self.agro_zone


class DemoRequest(BaseModel):
    lang: str = "en"


class RequestOtpRequest(BaseModel):
    phone: str


class VerifyOtpRequest(BaseModel):
    phone: str
    code: str
    # Language chosen during onboarding, used to seed a brand-new farmer.
    lang: str = "en"


# In-memory pending OTPs, keyed by normalized phone. This is the MOCKED OTP store
# for the prototype (PROJECT_SPEC.md): request-otp returns the code so the UI can
# show it; verify-otp checks against it. The production step is SMS delivery with
# a shared/persistent store; in the single-process demo an in-memory dict suffices.
_PENDING_OTP: dict[str, str] = {}


class CurrentCropIn(BaseModel):
    crop_id: str
    planting_date: str


class LocationIn(BaseModel):
    lat: float
    lng: float
    mandal: str = ""


class UpdateFarmerRequest(BaseModel):
    """Partial profile update -- only the provided fields are written."""
    name: Optional[str] = None
    lang: Optional[str] = None
    soil_type: Optional[str] = None
    location: Optional[LocationIn] = None
    current_crops: Optional[list[CurrentCropIn]] = None


class TelemetryIn(BaseModel):
    event: str
    layer: str = "location"
    detail: Optional[dict] = None
    fallback_used: bool = False


def _primary_crop(farmer: Farmer) -> tuple[Optional[str], Optional[str]]:
    """The farmer's current crop and its planting date (crop_id, planting_date).

    Prefers the first entry in the profile's current_crops (which carries the
    planting date needed for the growth stage); falls back to the legacy single
    `crop` field for farmers who predate profile setup.
    """
    if farmer.current_crops:
        first = farmer.current_crops[0]
        return first.crop_id, first.planting_date
    return (farmer.crop or None), None


def build_context(farmer: Farmer, crop_doc=None) -> FusionContext:
    """Assemble the fusion `context` from the signed-in farmer's profile.

    Personalizes every AI call (PROJECT_SPEC.md "Context into Gemini"): crop and
    location come straight from the profile; soil carries the declared soil type;
    and the current growth stage is computed deterministically from the crop's
    planting_date and the crop DB's cycle_days/growth_stages (engine.growth).

    Degrades gracefully per source (spec layer 3): weather falls back to
    zone-normal when no live provider is wired, and a missing crop doc or planting
    date simply leaves growth_stage unset -- the deterministic fusion still runs.
    """
    crop_id, planting_date = _primary_crop(farmer)

    try:
        weather_reading = weather_source.get_weather(farmer.location.lat, farmer.location.lng)
    except Exception as exc:
        _log_event("weather_fallback", "context_data", str(exc), fallback_used=True)
        weather_reading = weather_source.zone_normal_weather()

    # Resolve the crop DB doc (for cycle_days/growth_stages) unless one was passed.
    if crop_doc is None and crop_id:
        try:
            crop_doc = firestore_client.get_crop(crop_id)
        except Exception as exc:
            _log_event("crop_lookup_fallback", "context_data", str(exc), fallback_used=True)
            crop_doc = None

    stage = None
    if planting_date and crop_doc is not None:
        stage = compute_stage(planting_date, crop_doc.cycle_days, crop_doc.growth_stages)

    return FusionContext(
        crop=crop_id or farmer.crop,
        location=ContextLocation(
            lat=farmer.location.lat, lng=farmer.location.lng, resolution="village"
        ),
        weather=weather_reading,
        # No live soil card is wired, so nitrogen stays unknown; the declared soil
        # type from the profile rides along for personalization.
        soil=Soil(nitrogen="unknown", source="unknown", type=farmer.soil_type),
        nearby_confirmed=[],
        growth_stage=stage["name"] if stage else None,
        crop_day=stage["day"] if stage else None,
        # Season derived from today's date; susceptible_diseases filled in by the
        # diagnose flow once the diagnosed crop is known.
        season=current_season(),
        susceptible_diseases=list(crop_doc.susceptible_diseases) if crop_doc is not None else [],
    )


def _log_event(event: str, layer: str, detail: str, fallback_used: bool = False) -> None:
    """Best-effort telemetry write; a logging failure must never surface to the user."""
    try:
        firestore_client.log_telemetry(
            Telemetry(event=event, layer=layer, detail={"error": detail}, fallback_used=fallback_used)
        )
    except Exception:
        pass


def _log_fallback(event: str, detail: str) -> None:
    _log_event(event, "ai_content", detail, fallback_used=True)


@app.post("/api/auth/request-otp")
def request_otp(request: RequestOtpRequest):
    """Generate a 4-digit OTP for a phone number and return it (demo mode).

    PROJECT_SPEC.md: the OTP is MOCKED for the prototype -- we return the code in
    the response so the UI can display "Demo OTP: XXXX" and an unattended judge can
    proceed without a real SMS. Sending it over SMS is the named production step.
    """
    phone = firestore_client.normalize_phone(request.phone)
    if len(phone) < 10:
        raise HTTPException(status_code=400, detail="please enter a valid 10-digit phone number")
    code = "%04d" % random.randint(0, 9999)
    _PENDING_OTP[phone] = code
    return {"phone": phone, "code": code, "demo": True}


@app.post("/api/auth/verify-otp")
def verify_otp(request: VerifyOtpRequest):
    """Verify the OTP and return { farmer, is_new }.

    A phone that matches no farmer creates a new, minimal farmer document (to be
    completed in profile setup) with is_new=true; a known phone returns the
    existing farmer with is_new=false. Existing document IDs are never changed --
    auth only resolves phone -> document (PROJECT_SPEC.md).
    """
    phone = firestore_client.normalize_phone(request.phone)
    expected = _PENDING_OTP.get(phone)
    if not expected or request.code.strip() != expected:
        raise HTTPException(status_code=401, detail="that code doesn't match; please try again")
    # One-time use: consume the code so it can't be replayed.
    _PENDING_OTP.pop(phone, None)

    try:
        farmer = firestore_client.get_farmer_by_phone(phone)
        if farmer is not None:
            return {"farmer": farmer.model_dump(), "is_new": False}

        lang = request.lang if request.lang in ("en", "hi", "te") else "en"
        new_farmer = Farmer(
            name="",
            phone=phone,
            lang=lang,
            location=FarmerLocation(lat=0.0, lng=0.0, mandal=""),
            crop="",
            land_size_acres=0.0,
            growth_stage="",
        )
        new_id = firestore_client.upsert_farmer(new_farmer)
        new_farmer.id = new_id
        return {"farmer": new_farmer.model_dump(), "is_new": True}
    except Exception as exc:
        # Firestore unreachable -- report honestly rather than fabricate a session.
        _log_event("auth_verify_error", "api", str(exc))
        raise HTTPException(status_code=503, detail="sign-in is temporarily unavailable; please retry")


@app.get("/api/farmers/{farmer_id}")
def read_farmer(farmer_id: str):
    """The signed-in farmer's profile (for the profile page to load current values)."""
    try:
        farmer = firestore_client.get_farmer(farmer_id)
    except Exception as exc:
        _log_event("farmer_get_error", "api", str(exc))
        raise HTTPException(status_code=503, detail="profile temporarily unavailable")
    if farmer is None:
        raise HTTPException(status_code=404, detail="farmer not found")
    return farmer.model_dump()


@app.patch("/api/farmers/{farmer_id}")
def patch_farmer(farmer_id: str, request: UpdateFarmerRequest):
    """Persist profile edits (setup and the profile page both write here).

    Human-override layer (PROJECT_SPEC.md): the farmer's own values overwrite
    whatever was inferred/placeholder, and take effect immediately downstream.
    A planting date is required for every current crop -- it's what enables
    memory/reminders.
    """
    try:
        farmer = firestore_client.get_farmer(farmer_id)
    except Exception as exc:
        _log_event("farmer_patch_lookup_error", "api", str(exc))
        raise HTTPException(status_code=503, detail="profile store temporarily unavailable")
    if farmer is None:
        raise HTTPException(status_code=404, detail="farmer not found")

    updates: dict = {}
    if request.name is not None:
        updates["name"] = request.name
    if request.lang is not None:
        if request.lang not in ("en", "hi", "te"):
            raise HTTPException(status_code=400, detail="unsupported language")
        updates["lang"] = request.lang
    if request.soil_type is not None:
        updates["soil_type"] = request.soil_type
    if request.location is not None:
        updates["location"] = request.location.model_dump()
    if request.current_crops is not None:
        for crop in request.current_crops:
            if not crop.crop_id or not crop.planting_date:
                raise HTTPException(
                    status_code=400, detail="each crop needs a crop and a planting date"
                )
        updates["current_crops"] = [crop.model_dump() for crop in request.current_crops]

    if not updates:
        return farmer.model_dump()

    try:
        firestore_client.update_farmer(farmer_id, updates)
        updated = firestore_client.get_farmer(farmer_id)
    except Exception as exc:
        _log_event("farmer_patch_write_error", "api", str(exc))
        raise HTTPException(status_code=503, detail="could not save your profile; please retry")
    return updated.model_dump()


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def _geocode_places(query: str) -> list[dict]:
    """Look up places by name via OpenStreetMap Nominatim (India), returning
    [{name, lat, lng}]. Isolated so it can be exercised/mocked in tests."""
    params = urllib.parse.urlencode(
        {"q": query, "format": "jsonv2", "countrycodes": "in", "limit": 6, "addressdetails": 1}
    )
    req = urllib.request.Request(
        f"{NOMINATIM_URL}?{params}",
        headers={"User-Agent": "KisanMate/1.0 (agriculture assistant demo)"},
    )
    with urllib.request.urlopen(req, timeout=6) as resp:
        data = json.load(resp)

    places = []
    for item in data:
        try:
            lat, lng = float(item["lat"]), float(item["lon"])
        except (KeyError, ValueError, TypeError):
            continue
        addr = item.get("address", {})
        locality = (
            item.get("name")
            or addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("suburb")
            or addr.get("county")
            or ""
        )
        region = addr.get("state") or addr.get("state_district") or ""
        label = ", ".join(p for p in (locality, region) if p) or item.get("display_name", "")
        places.append({"name": label, "lat": lat, "lng": lng})
    return places


@app.get("/api/places")
def search_places(q: str = ""):
    """Live place search for the location picker: the farmer types a place name and
    picks a real match, whose coordinates become their location.

    Proxied through the backend so we can identify politely to the geocoder and
    keep the frontend simple. Degrades silently to an empty list (never an error)
    so the picker just shows "no matches" if the lookup is unavailable."""
    query = (q or "").strip()
    if len(query) < 3:
        return {"places": []}
    try:
        return {"places": _geocode_places(query)}
    except Exception as exc:
        _log_event("place_search_error", "location", str(exc))
        return {"places": []}


NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"


def _reverse_geocode_lookup(lat: float, lng: float) -> dict:
    """Raw Nominatim reverse-geocode response for a coordinate (shared by the
    place-name lookup below and the assistant's mandi-price state/district
    lookup)."""
    params = urllib.parse.urlencode(
        {"lat": lat, "lon": lng, "format": "jsonv2", "addressdetails": 1, "zoom": 12}
    )
    req = urllib.request.Request(
        f"{NOMINATIM_REVERSE_URL}?{params}",
        headers={"User-Agent": "KisanMate/1.0 (agriculture assistant demo)"},
    )
    with urllib.request.urlopen(req, timeout=6) as resp:
        return json.load(resp)


def _reverse_geocode(lat: float, lng: float) -> Optional[str]:
    """Look up a human-readable place name for a coordinate via Nominatim's
    reverse geocoder. Returns None if nothing usable comes back."""
    item = _reverse_geocode_lookup(lat, lng)
    addr = item.get("address", {})
    locality = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("suburb")
        or addr.get("county")
        or ""
    )
    region = addr.get("state") or addr.get("state_district") or ""
    label = ", ".join(p for p in (locality, region) if p) or item.get("display_name")
    return label or None


def _farmer_admin_area(lat: float, lng: float) -> tuple[Optional[str], Optional[str]]:
    """(state, district) for a coordinate, via the same Nominatim reverse lookup
    -- used to scope the assistant's mandi-price filters. Returns (None, None)
    on any failure; callers already treat that as "no location hint"."""
    try:
        addr = _reverse_geocode_lookup(lat, lng).get("address", {})
    except Exception:
        return None, None
    return addr.get("state"), (addr.get("state_district") or addr.get("county"))


@app.get("/api/places/reverse")
def reverse_place(lat: float, lng: float):
    """Reverse-geocode a device location so the picker's text field reflects
    where the farmer actually is, instead of staying on the prefilled default.
    Degrades silently to no name (never an error) so callers can just fall
    back to keeping whatever text was already shown."""
    try:
        return {"name": _reverse_geocode(lat, lng)}
    except Exception as exc:
        _log_event("place_reverse_error", "location", str(exc))
        return {"name": None}


@app.get("/api/reminders/{farmer_id}")
def get_reminders(farmer_id: str):
    """Cycle-based reminders for the farmer, computed deterministically from each
    current crop's planting_date + the crop DB (engine.reminders). The home screen
    calls this on load and shows them proactively -- no user action.

    PRODUCTION NOTE: the production push trigger is Cloud Scheduler calling an
    SMS-push endpoint over this same computation; the prototype computes on load.
    """
    try:
        farmer = firestore_client.get_farmer(farmer_id)
    except Exception as exc:
        _log_event("reminders_lookup_error", "api", str(exc))
        raise HTTPException(status_code=503, detail="reminders temporarily unavailable")
    if farmer is None:
        raise HTTPException(status_code=404, detail="farmer not found")

    # A live 7-day rain outlook upgrades the irrigation reminder to a dry-spell
    # alert; if the forecast is unavailable, dry_spell stays False and reminders
    # fall back to the existing cadence-only behavior -- silently, per layer 3.
    try:
        dry_spell = weather_source.get_forecast(farmer.location.lat, farmer.location.lng).dry_spell
        _log_event("reminders_weather", "weather", f"dry_spell={dry_spell}", fallback_used=False)
    except Exception as exc:
        _log_event("reminders_weather", "weather", str(exc), fallback_used=True)
        dry_spell = False

    # Cache crop lookups within this request (a farmer may grow the same crop
    # more than once, and get_crop is a Firestore read).
    crop_cache: dict = {}

    def crop_lookup(crop_id):
        if crop_id not in crop_cache:
            try:
                crop_cache[crop_id] = firestore_client.get_crop(crop_id)
            except Exception as exc:
                _log_event("reminders_crop_fallback", "context_data", str(exc), fallback_used=True)
                crop_cache[crop_id] = None
        return crop_cache[crop_id]

    reminders = reminders_for_farmer(farmer, crop_lookup, dry_spell=dry_spell)
    return {"farmer_id": farmer_id, "reminders": reminders}


@app.post("/api/telemetry")
def post_telemetry(entry: TelemetryIn):
    """Frontend-originated telemetry (e.g. the silent geolocation fallback).

    Best-effort: a logging failure must never surface to the farmer, matching the
    rest of the telemetry path (PROJECT_SPEC.md layer 3)."""
    try:
        firestore_client.log_telemetry(
            Telemetry(
                event=entry.event,
                layer=entry.layer,
                detail=entry.detail,
                fallback_used=entry.fallback_used,
            )
        )
    except Exception:
        pass
    return {"ok": True}


def _farmer_crop_planting_dates(farmer: Farmer) -> dict[str, Optional[str]]:
    """Lowercased crop_id -> planting_date for every crop the farmer currently
    grows (from current_crops; the legacy single `crop` field has no date)."""
    dates = {c.crop_id.strip().lower(): c.planting_date for c in (farmer.current_crops or []) if c.crop_id}
    if farmer.crop:
        dates.setdefault(farmer.crop.strip().lower(), None)
    return dates


def _resolve_diagnosed_crop(vision: "VisionOutput", farmer: Farmer, context: FusionContext) -> None:
    """Identify the plant and point the diagnosis at the right crop profile.

    Sets `vision.matches_profile` authoritatively (never trusting the model to
    know the farmer's crops) and, whenever the photo isn't the crop `context`
    was originally built for, repoints `context.crop`/`susceptible_diseases`/
    growth stage at the identified plant's own profile from the crops DB (or
    leaves them general if it isn't in the DB). This also covers a multi-crop
    farmer photographing a DIFFERENT one of their own current crops than the
    one `context` defaulted to -- that still needs repointing, since "the
    farmer grows this somewhere" is not the same as "context is already built
    for it". Unidentifiable plants are left for the fusion decision to escalate.
    """
    identified = (vision.identified_crop or "").strip().lower()
    if not identified or identified == "unidentifiable":
        vision.matches_profile = False
        return

    farmer_crop_dates = _farmer_crop_planting_dates(farmer)
    vision.matches_profile = identified in farmer_crop_dates

    if identified == (context.crop or "").strip().lower():
        return  # context is already built for this exact crop

    # A different plant than context's default -- point at ITS OWN profile
    # from the crops DB (diseases + growth stage), whether or not it's also
    # one of the farmer's other current crops.
    context.crop = identified
    try:
        crop_doc = firestore_client.get_crop(identified)
    except Exception as exc:
        _log_event("diagnose_crop_lookup_fallback", "context_data", str(exc), fallback_used=True)
        crop_doc = None
    context.susceptible_diseases = list(crop_doc.susceptible_diseases) if crop_doc is not None else []

    planting_date = farmer_crop_dates.get(identified)
    stage = (
        compute_stage(planting_date, crop_doc.cycle_days, crop_doc.growth_stages)
        if planting_date and crop_doc is not None
        else None
    )
    context.growth_stage = stage["name"] if stage else None
    context.crop_day = stage["day"] if stage else None


# Cap on the stored photo so a case doc stays well under Firestore's 1MB limit.
# The frontend downscales before upload, so real photos land far below this.
_MAX_STORED_IMAGE_BYTES = 700_000


def _encode_image(image_bytes: bytes, content_type: Optional[str]) -> Optional[str]:
    """Encode the photo as a data URL for the officer portal, or None if it's too
    large to store safely (never fail the diagnosis over the photo)."""
    if not image_bytes or len(image_bytes) > _MAX_STORED_IMAGE_BYTES:
        return None
    mime = content_type or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"


def _diagnosis_candidates(crop_id: Optional[str], susceptible_diseases: list) -> list:
    """The conditions to diagnose for a crop (Option 2, crop-driven).

    Tomato keeps its calibrated prior set (late/early blight + nitrogen). Any other
    crop uses its own susceptible_diseases from the crops DB (plus "healthy"), so
    the diagnosis isn't limited to the hardcoded tomato list. Falls back to the
    tomato set when the crop has no disease data.
    """
    if crop_id == "tomato":
        return list(TOMATO_CONDITIONS)
    diseases = [d for d in (susceptible_diseases or []) if d]
    if not diseases:
        return list(TOMATO_CONDITIONS)
    return list(dict.fromkeys(diseases)) + ["healthy"]


def _top_visible_symptoms(vision: "Optional[VisionOutput]", condition: str) -> list:
    """Visible symptoms for the fused top condition (fall back to the most
    confident candidate), to ground the explanation's "why"."""
    if vision is None or not vision.candidates:
        return []
    match = next((c for c in vision.candidates if c.condition == condition), None)
    chosen = match or max(vision.candidates, key=lambda c: c.confidence)
    return list(chosen.visible_symptoms)


@app.post("/api/diagnose")
async def diagnose(
    farmer_id: str = Form(...),
    image: UploadFile = File(...),
    image_note: Optional[str] = Form(None),
):
    """Runs vision -> fusion -> explain on an uploaded leaf photo and stores the case.

    Vision and explain are pure AI content on top of the deterministic engine
    (engine.fusion.fuse): if either Gemini call fails, we log a telemetry entry
    and fall back to the deterministic path (vision=None -> prior-only fusion,
    which always escalates; a per-language template instead of an AI message)
    rather than surface any error to the farmer.
    """
    try:
        farmer = firestore_client.get_farmer(farmer_id)
    except Exception as exc:
        # Firestore itself is unreachable -- we don't even know the farmer's
        # language yet, so this is the one fallback message that defaults to English.
        _log_fallback("diagnose_unhandled_error", str(exc))
        return {"case_id": None, "fusion": None, "message": template_message("escalate_rsk", "en")}

    if farmer is None:
        raise HTTPException(status_code=404, detail="farmer not found")

    try:
        image_bytes = await image.read()
        image_data_url = _encode_image(image_bytes, image.content_type)
        context = build_context(farmer)

        # We don't yet know which plant the photo shows, so this first pass is
        # scoped to the farmer's DEFAULT crop's diseases (Option 2). If the photo
        # turns out to be a different plant, we re-score below against the RIGHT
        # crop's diseases -- otherwise the model can only ever pick from the wrong
        # disease list and misses the actual disease (e.g. wheat rust scored
        # against the tomato list).
        mime = image.content_type or "image/jpeg"
        candidates = _diagnosis_candidates(context.crop, context.susceptible_diseases)

        vision_result = None
        try:
            vision_result = diagnose_image(image_bytes, mime_type=mime, candidate_conditions=candidates)
        except VisionError as exc:
            _log_fallback("vision_fallback", str(exc))

        # Plant identification: figure out which plant the photo shows, set
        # matches_profile authoritatively, and point the diagnosis at that plant's
        # crop profile (PROJECT_SPEC.md "plant identification & multi-crop"). This
        # may repoint context.crop/susceptible_diseases.
        if vision_result is not None:
            _resolve_diagnosed_crop(vision_result, farmer, context)
            rescoped = _diagnosis_candidates(context.crop, context.susceptible_diseases)
            # The photo is a different plant than the first pass assumed: re-run
            # vision scoped to the identified crop's OWN diseases, so the model can
            # actually report them (the first pass was locked to the wrong list).
            if set(rescoped) != set(candidates):
                try:
                    rescored = diagnose_image(image_bytes, mime_type=mime, candidate_conditions=rescoped)
                    _resolve_diagnosed_crop(rescored, farmer, context)
                    vision_result = rescored
                except VisionError as exc:
                    # Keep the first-pass reading; fusion still runs against the
                    # repointed crop (it just can't confirm a disease it never saw).
                    _log_fallback("vision_rescope_fallback", str(exc))
            candidates = rescoped

        fusion_result = fuse(vision_result, context, candidates)

        visible_symptoms = _top_visible_symptoms(vision_result, fusion_result.top)
        try:
            explanation = explain_fusion(fusion_result, farmer.lang, context, visible_symptoms)
        except ExplainError as exc:
            _log_fallback("explain_fallback", str(exc))
            explanation = template_explanation(fusion_result.decision, farmer.lang)
        # Combined string kept for text-to-speech; `explanation` is the structured
        # what/why/what-to-do the UI renders in separate blocks.
        message = combine_explanation(explanation)

        case = Case(
            farmer_id=farmer_id,
            image_note=image_note,
            image_data=image_data_url,
            vision=vision_result,
            context=context,
            fusion=fusion_result,
            status="advised" if fusion_result.decision == "advise" else "escalated",
            condition=fusion_result.top,
            contagious=next(
                (p.contagious for p in fusion_result.posterior if p.condition == fusion_result.top),
                False,
            ),
        )
        case_id = firestore_client.create_case(case)

        return {
            "case_id": case_id,
            "fusion": fusion_result.model_dump(),
            "explanation": explanation,
            "message": message,
        }

    except HTTPException:
        raise
    except Exception as exc:
        # Last-resort safety net (spec layer 3): the farmer must never see a raw
        # error, even for failures outside the AI layer (e.g. Firestore itself).
        _log_fallback("diagnose_unhandled_error", str(exc))
        return {
            "case_id": None,
            "fusion": None,
            "message": template_message("escalate_rsk", farmer.lang),
        }


def _case_row(case: Case, farmers_by_id: dict[str, Farmer]) -> dict:
    """Flatten a Case into the shape the RSK dashboard renders one card from."""
    farmer = farmers_by_id.get(case.farmer_id)

    top = case.fusion.top if case.fusion else None
    candidates: list[dict] = []
    visible_symptoms: list[str] = []
    if case.vision and case.vision.candidates:
        for c in case.vision.candidates:
            candidates.append(
                {"condition": c.condition, "confidence": c.confidence, "visible_symptoms": c.visible_symptoms}
            )
        # surface the symptoms for the AI's own top pick, falling back to the first candidate
        top_candidate = next((c for c in case.vision.candidates if c.condition == top), case.vision.candidates[0])
        visible_symptoms = top_candidate.visible_symptoms

    return {
        "case_id": case.id,
        "farmer_id": case.farmer_id,
        "farmer_name": farmer.name if farmer else case.farmer_id,
        "crop": case.context.crop if case.context else (farmer.crop if farmer else None),
        "mandal": farmer.location.mandal if farmer else None,
        "location": {"lat": farmer.location.lat, "lng": farmer.location.lng} if farmer else None,
        "image_note": case.image_note,
        "photo": case.image_data,
        "status": case.status,
        # Whether Gemini actually analysed the photo. When false, the fusion top is
        # a prior-only (environment) estimate, NOT a read of the image, so the
        # portal must not present it as a confident AI diagnosis.
        "photo_analyzed": bool(case.vision),
        "ai_top_condition": top,
        "ai_confidence": case.fusion.confidence if case.fusion else None,
        "ai_decision": case.fusion.decision if case.fusion else None,
        "image_quality": case.vision.image_quality if case.vision else None,
        "visible_symptoms": visible_symptoms,
        "candidates": candidates,
        "created_at": case.created_at,
    }


@app.get("/api/cases")
def list_cases():
    """The RSK officer's review queue: cases awaiting a human verdict.

    Two categories the portal separates on `status` (PROJECT_SPEC.md):
    "escalated" -> "AI needs review"; "disputed" -> "Farmer disputed".
    """
    try:
        cases = firestore_client.list_cases_by_status(["pending", "escalated", "disputed"])
        farmers_by_id = {f.id: f for f in firestore_client.list_farmers()}
    except Exception as exc:
        # Surface a real load failure instead of a false-empty queue: an officer
        # must not mistake "database down" for "no cases need review". The
        # dashboard renders 503 as a kind "couldn't load, retry" state.
        _log_event("cases_list_error", "api", str(exc))
        raise HTTPException(status_code=503, detail="cases temporarily unavailable")
    return {"cases": [_case_row(case, farmers_by_id) for case in cases]}


def _farmer_case_row(case: Case) -> dict:
    """Flatten one of the farmer's OWN cases for their "My reports" list.

    `condition` always reflects the current authoritative read: the RSK
    officer's verdict once the case is confirmed, else the AI's fused top guess
    (PROJECT_SPEC.md: "the [officer's] verdict is authoritative and flows back
    to the farmer").
    """
    return {
        "case_id": case.id,
        "created_at": case.created_at,
        "photo": case.image_data,
        "crop": case.context.crop if case.context else None,
        "status": case.status,
        "condition": case.officer_verdict or (case.fusion.top if case.fusion else None),
        "decision": case.fusion.decision if case.fusion else None,
        "contagious": case.contagious,
        "officer_reviewed": case.status == "confirmed" and case.officer_verdict is not None,
    }


@app.get("/api/farmers/{farmer_id}/cases")
def list_farmer_cases(farmer_id: str):
    """A farmer's own diagnosis history ("My reports"), newest first -- how an
    RSK officer's confirm/override verdict makes its way back to the farmer,
    since this is a poll-based prototype with no push/SMS channel."""
    try:
        cases = firestore_client.list_cases_by_farmer(farmer_id)
    except Exception as exc:
        _log_event("farmer_cases_error", "api", str(exc))
        raise HTTPException(status_code=503, detail="reports temporarily unavailable")
    return {"cases": [_farmer_case_row(case) for case in cases]}


@app.get("/api/farmers/{farmer_id}/notifications")
def list_farmer_notifications(farmer_id: str):
    """Officer verdicts the farmer hasn't seen yet -- drives the one-time
    "your case was reviewed" popup on the farmer's next login/reload after an
    RSK officer confirmed or overrode one of their cases. Newest first."""
    try:
        cases = firestore_client.list_cases_by_farmer(farmer_id)
    except Exception as exc:
        _log_event("farmer_notifications_error", "api", str(exc))
        # A notifications failure must never block the farmer's app -- degrade to
        # "nothing new" (the verdict is still visible under My reports).
        return {"notifications": []}
    unseen = [
        _farmer_case_row(c)
        for c in cases
        if c.status == "confirmed" and c.officer_verdict is not None and not c.verdict_seen
    ]
    return {"notifications": unseen}


@app.post("/api/cases/{case_id}/verdict-seen")
def mark_verdict_seen(case_id: str):
    """Acknowledge that the farmer has been shown a verdict popup, so it never
    fires again (best-effort: a write failure must not disrupt the farmer)."""
    try:
        firestore_client.update_case(case_id, {"verdict_seen": True})
    except Exception as exc:
        _log_event("verdict_seen_write_error", "api", str(exc))
    return {"case_id": case_id, "verdict_seen": True}


@app.post("/api/cases/{case_id}/dispute")
def dispute_case(case_id: str):
    """Farmer taps "This isn't right": mark the case DISPUTED so it lands in the
    officer portal's "Farmer disputed" category.

    This is NOT an officer verdict: it sets no `officer_verdict` and triggers NO
    community-alert propagation (PROJECT_SPEC.md). Only an officer Confirm/Override
    is authoritative.
    """
    try:
        case = firestore_client.get_case(case_id)
    except Exception as exc:
        _log_event("dispute_lookup_error", "api", str(exc))
        raise HTTPException(status_code=503, detail="case store temporarily unavailable")
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    try:
        firestore_client.update_case(case_id, {"status": "disputed"})
    except Exception as exc:
        _log_event("dispute_write_error", "api", str(exc))
        raise HTTPException(status_code=503, detail="could not record your feedback; please retry")

    _log_event("farmer_disputed", "human_override", f"case {case_id}")
    return {"case_id": case_id, "status": "disputed"}


@app.post("/api/confirm")
def confirm(request: ConfirmRequest):
    """Record the officer's authoritative verdict and, if the confirmed condition
    is contagious, fire the community alert engine for this case.

    The officer's verdict overrides whatever the AI inferred (PROJECT_SPEC.md
    layer 4): we set officer_verdict, force status to "confirmed", and adopt the
    verdict as the case's condition before propagation, so the alert reflects the
    human decision -- not the model's.
    """
    try:
        case = firestore_client.get_case(request.case_id)
    except Exception as exc:
        _log_event("confirm_lookup_error", "api", str(exc))
        raise HTTPException(status_code=503, detail="case store temporarily unavailable")

    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    verdict = request.officer_verdict
    contagious = is_contagious(verdict)

    try:
        # Only a contagious verdict can fan out an alert, so only then do we need
        # the farmer roster (keeps the non-contagious path to a single write).
        farmers = firestore_client.list_farmers() if contagious else []
        alert_summary = services.confirm_and_propagate(
            case,
            verdict,
            farmers,
            error_logger=lambda exc: _log_event("alert_propagation_error", "alert_engine", str(exc)),
        )
    except Exception as exc:
        # The case update itself failed (e.g. Firestore write) -- report honestly
        # rather than claim a confirmation that didn't persist.
        _log_event("confirm_write_error", "api", str(exc))
        raise HTTPException(status_code=503, detail="could not save the verdict; please retry")

    return {
        "case_id": request.case_id,
        "status": "confirmed",
        "officer_verdict": verdict,
        "condition": verdict,
        "contagious": contagious,
        "alert": alert_summary,
    }


@app.get("/api/alerts")
def list_all_alerts():
    """Every fired alert, enriched with recipient identities -- the officer view.

    Unlike the farmer-facing endpoint below, this is NOT anonymized: the officer
    needs to see who was notified (PROJECT_SPEC.md keeps anonymization to the
    farmer-facing side).
    """
    try:
        alerts = firestore_client.list_recent_alerts()
        name_by_id = {f.id: f.name for f in firestore_client.list_farmers()}
    except Exception as exc:
        _log_event("alerts_list_error", "api", str(exc))
        raise HTTPException(status_code=503, detail="alerts temporarily unavailable")

    rows = []
    for a in alerts:
        rows.append(
            {
                "alert_id": a.id,
                "condition": a.condition,
                "tier": a.tier,
                "recipient_count": len(a.recipient_ids),
                "recipients": [{"id": rid, "name": name_by_id.get(rid, rid)} for rid in a.recipient_ids],
                "radius_km": a.radius_km,
                "center": {"lat": a.center.lat, "lng": a.center.lng},
                "source_case_id": a.source_case_id,
                "created_at": a.created_at,
            }
        )
    return {"alerts": rows}


@app.get("/api/alerts/{farmer_id}")
def get_alerts(farmer_id: str):
    """Alerts received by one farmer, anonymized (no recipient identities exposed)."""
    try:
        alerts = firestore_client.list_alerts_for_farmer(farmer_id)
    except Exception as exc:
        # The farmer app renders this as a kind "couldn't load, check connection"
        # state (never a raw error), so an honest 503 beats a false "no alerts".
        _log_event("farmer_alerts_error", "api", str(exc))
        raise HTTPException(status_code=503, detail="alerts temporarily unavailable")

    alerts.sort(key=lambda a: (a.created_at is not None, a.created_at), reverse=True)
    payload = [
        {"condition": a.condition, "tier": a.tier, "radius_km": a.radius_km, "created_at": a.created_at}
        for a in alerts
    ]
    return {"farmer_id": farmer_id, "alerts": payload}


@app.get("/api/crops")
def list_crops():
    """Every crop in the crops collection -- the data backbone (PROJECT_SPEC.md)."""
    try:
        crops = firestore_client.list_crops()
    except Exception as exc:
        _log_event("crops_list_error", "api", str(exc))
        raise HTTPException(status_code=503, detail="crops temporarily unavailable")
    return {"crops": [crop.model_dump() for crop in crops]}


@app.get("/api/crops/{crop_id}")
def get_crop(crop_id: str):
    """A single crop's full detail (names, seasons, cycle, stages, diseases)."""
    try:
        crop = firestore_client.get_crop(crop_id)
    except Exception as exc:
        _log_event("crop_get_error", "api", str(exc))
        raise HTTPException(status_code=503, detail="crop temporarily unavailable")
    if crop is None:
        raise HTTPException(status_code=404, detail="crop not found")
    return crop.model_dump()


@app.post("/api/recommend")
def recommend(request: RecommendRequest):
    """Rank candidate crops FROM the crops collection for this farmer's field.

    The ranking is deterministic (engine.crop_recommender.rank_crops: match
    soil_type + current season + region). Gemini only rewrites the reason text on
    top; if it fails we log a fallback and keep the deterministic reasons, so the
    farmer always gets a useful, grounded answer (PROJECT_SPEC.md layers 1-3).

    Personalized from the profile: soil/region default to the farmer's own when
    the request omits them, and a `context_note` (plus the Gemini reasons) names
    the farmer's soil and current growth stage.
    """
    season = request.season or current_season()

    # Load the farmer first so the recommendation can personalize from the profile.
    try:
        farmer = firestore_client.get_farmer(request.farmer_id)
    except Exception:
        farmer = None
    lang = farmer.lang if farmer else "en"

    soil_type = request.resolved_soil() or (farmer.soil_type if farmer else None)
    region = request.resolved_region()

    try:
        crops = firestore_client.list_crops()
        recommendations = rank_crops(crops, soil_type=soil_type, season=season, region=region)
    except Exception as exc:
        _log_event("recommend_error", "deterministic", str(exc))
        raise HTTPException(status_code=503, detail="recommendation temporarily unavailable")

    # Build the farmer's field context (soil + current crop growth stage) to
    # personalize both the note and the Gemini reasons.
    context = None
    if farmer is not None:
        try:
            context = build_context(farmer)
        except Exception as exc:
            _log_event("recommend_context_fallback", "context_data", str(exc), fallback_used=True)

    # AI content layer: warm reason text on top of the deterministic reasons.
    try:
        reasons = explain_recommendations(recommendations, lang, context)
        for rec in recommendations:
            if rec.crop in reasons and reasons[rec.crop].strip():
                rec.reason = reasons[rec.crop].strip()
    except RecommendExplainError as exc:
        _log_fallback("recommend_explain_fallback", str(exc))

    return {
        "farmer_id": request.farmer_id,
        "season": season,
        "context_note": recommendation_note(context, lang),
        "recommendations": [rec.model_dump() for rec in recommendations],
    }


def _crop_recommendation_answer(farmer: Optional[Farmer], question: str, lang: str) -> dict:
    """Grounded, conversational crop recommendation -- shared by /api/recommend/ask
    and the assistant's crop_recommendation intent (main.py, don't rebuild this).

    Takes the farmer's free-form question plus their full profile context (soil,
    location, season/weather from today's date, current & recent crops) and a
    GROUNDED shortlist from the crops DB, and asks Gemini for one or two specific
    crops with a structured "why" (soil, season/weather, rotation). If Gemini
    fails, falls back to the deterministic top crop(s) with a template reason --
    same {recommendations, spoken} shape either way.

    Raises on a hard data failure (crops DB unreachable) -- callers decide how
    to degrade (an HTTP 503 for the dedicated endpoint; a graceful spoken
    message for the assistant).
    """
    season = current_season()
    crops = firestore_client.list_crops()  # may raise -- caller's problem to handle

    soil_type = farmer.soil_type if farmer else None
    docs_by_id = {c.id: c for c in crops}

    # Grounding shortlist: the crops that actually fit this field, so Gemini
    # recommends realistic options rather than hallucinating.
    ranked = rank_crops(crops, soil_type=soil_type, season=season, region=None, limit=8)
    grounding = []
    for rec in ranked:
        doc = docs_by_id.get(rec.crop)
        if doc is None:
            continue
        grounding.append({
            "crop_id": doc.id,
            "name": doc.names.en,
            "seasons": doc.seasons,
            "soil_types": doc.soil_types,
            "water_need": doc.water_need,
            "susceptible_diseases": doc.susceptible_diseases,
        })

    # Current/recent crops drive the rotation reasoning ("after tomatoes...").
    current_ids = [c.crop_id for c in (farmer.current_crops if farmer else [])]
    prev_crop_name = None
    if current_ids and docs_by_id.get(current_ids[0]) is not None:
        pnames = docs_by_id[current_ids[0]].names.model_dump()
        prev_crop_name = pnames.get(lang) or pnames.get("en") or current_ids[0]

    context = None
    if farmer is not None:
        try:
            context = build_context(farmer)
        except Exception as exc:
            _log_event("recommend_ask_context_fallback", "context_data", str(exc), fallback_used=True)

    weather = None
    if context is not None:
        weather = {
            "temp_c": context.weather.temp_c,
            "humidity_pct": context.weather.humidity_pct,
            "rain_48h_mm": context.weather.rain_48h_mm,
            "source": context.weather.source,
        }
    profile = {
        "soil_type": soil_type,
        "location": farmer.location.mandal if farmer else None,
        "season": season,
        "weather": weather,
        "current_crops": current_ids,
    }

    try:
        return recommend_conversational(question, profile, grounding, lang)
    except RecommendExplainError as exc:
        _log_fallback("recommend_ask_fallback", str(exc))
        candidates = [
            {"crop_id": g["crop_id"], "names": docs_by_id[g["crop_id"]].names.model_dump()}
            for g in grounding
        ]
        return fallback_recommendation(candidates, soil_type, season, prev_crop_name, lang)


class RecommendAskRequest(BaseModel):
    farmer_id: str
    question: str = ""


@app.post("/api/recommend/ask")
def recommend_ask(request: RecommendAskRequest):
    """Conversational, voice-first crop recommendation (PROJECT_SPEC.md)."""
    question = (request.question or "").strip()

    try:
        farmer = firestore_client.get_farmer(request.farmer_id)
    except Exception:
        farmer = None
    lang = farmer.lang if farmer else "en"

    try:
        result = _crop_recommendation_answer(farmer, question, lang)
    except Exception as exc:
        _log_event("recommend_ask_error", "deterministic", str(exc))
        raise HTTPException(status_code=503, detail="recommendation temporarily unavailable")

    return {
        "farmer_id": request.farmer_id,
        "question": question,
        "recommendations": result["recommendations"],
        "spoken": result["spoken"],
    }


# --- farm assistant: one voice/text entry -> intent classifier -> handlers -----
# (PROJECT_SPEC.md voice upgrade). See assistant.py for the classifier + the
# Gemini-guardrailed handlers (fertilizer_advice, general_farming_qa); the
# other handlers below just narrate data this app already fetches elsewhere.

def _resolved_lang(request_lang: Optional[str], farmer: Optional[Farmer]) -> str:
    if request_lang in ("en", "hi", "te"):
        return request_lang
    if farmer is not None and farmer.lang in ("en", "hi", "te"):
        return farmer.lang
    return "en"


def _handle_crop_recommendation(farmer, text, intent, lang) -> dict:
    try:
        result = _crop_recommendation_answer(farmer, text, lang)
        return {"answer_text": result["spoken"], "data": {"recommendations": result["recommendations"]}}
    except Exception as exc:
        _log_event("assistant_recommend_fallback", "deterministic", str(exc), fallback_used=True)
        return {"answer_text": assistant_mod.farming_qa_unavailable_text(lang), "data": {}}


def _handle_weather_advice(farmer, text, intent, lang) -> dict:
    if farmer is None:
        return {"answer_text": assistant_mod.weather_advice_unavailable_text(lang), "data": {}}
    try:
        forecast = weather_source.get_forecast(farmer.location.lat, farmer.location.lng)
    except Exception as exc:
        _log_event("assistant_weather_fallback", "weather", str(exc), fallback_used=True)
        return {"answer_text": assistant_mod.weather_advice_unavailable_text(lang), "data": {}}
    _log_event("assistant_weather_used", "weather", f"dry_spell={forecast.dry_spell}", fallback_used=False)
    return {"answer_text": assistant_mod.weather_advice_text(forecast, lang), "data": forecast.model_dump()}


def _handle_mandi_price(farmer, text, intent, lang) -> dict:
    commodity = (intent.commodity or intent.crop or "").strip()
    if not commodity:
        return {"answer_text": assistant_mod.mandi_need_commodity_text(lang), "data": {}}

    state = district = None
    if farmer is not None:
        state, district = _farmer_admin_area(farmer.location.lat, farmer.location.lng)
    district = (intent.location or "").strip() or district or (farmer.location.mandal if farmer else None)

    try:
        price = mandi.get_price(commodity, state=state, district=district)
    except Exception as exc:
        _log_event("assistant_mandi_fallback", "mandi", str(exc), fallback_used=True)
        price = None

    if price is None:
        _log_event("assistant_mandi_no_rate", "mandi", f"commodity={commodity}", fallback_used=True)
        return {"answer_text": assistant_mod.mandi_no_rate_text(commodity, lang), "data": {}}

    _log_event("assistant_mandi_used", "mandi", f"commodity={commodity}", fallback_used=False)
    return {"answer_text": assistant_mod.mandi_price_text(price, lang), "data": price}


def _handle_fertilizer_advice(farmer, text, intent, lang) -> dict:
    crop = intent.crop or (farmer.crop if farmer else None)
    context = None
    if farmer is not None:
        try:
            context = build_context(farmer)
        except Exception:
            context = None
    profile = {
        "crop": crop,
        "soil_type": farmer.soil_type if farmer else None,
        "growth_stage": context.growth_stage if context else None,
    }
    try:
        result = assistant_mod.fertilizer_advice(text, profile, lang)
        return {"answer_text": result["answer_text"], "data": {"crop": crop}}
    except assistant_mod.FertilizerAdviceError as exc:
        _log_fallback("assistant_fertilizer_fallback", str(exc))
        fallback = assistant_mod.fallback_fertilizer_advice(crop, lang)
        return {"answer_text": fallback["answer_text"], "data": {"crop": crop}}


def _handle_general_farming_qa(farmer, text, intent, lang) -> dict:
    try:
        result = assistant_mod.general_farming_qa(text, lang)
        return {"answer_text": result["answer_text"], "data": {}, "citations": result["citations"]}
    except assistant_mod.FarmingQAError as exc:
        _log_fallback("assistant_general_qa_fallback", str(exc))
        return {"answer_text": assistant_mod.farming_qa_unavailable_text(lang), "data": {}, "citations": []}


_ASSISTANT_HANDLERS = {
    "crop_recommendation": _handle_crop_recommendation,
    "weather_advice": _handle_weather_advice,
    "mandi_price": _handle_mandi_price,
    "fertilizer_advice": _handle_fertilizer_advice,
    "general_farming_qa": _handle_general_farming_qa,
}


class AssistantRequest(BaseModel):
    text: str
    lang: str = "en"
    farmer_id: str


@app.post("/assistant")
def assistant(request: AssistantRequest):
    """One voice/text entry point for the farm assistant: classifies the
    farmer's free-form question, then routes to a small, fixed set of
    handlers -- an INTENT CLASSIFIER -> HANDLERS architecture, not a general
    chatbot. Every path degrades to a farmer-facing message (never a raw
    error) and logs telemetry at each fallback point (PROJECT_SPEC.md layer 3).
    """
    text = (request.text or "").strip()
    try:
        farmer = firestore_client.get_farmer(request.farmer_id)
    except Exception:
        farmer = None
    lang = _resolved_lang(request.lang, farmer)

    if not text:
        return {"intent": "off_topic", "answer_text": assistant_mod.off_topic_refusal_text(lang),
                "data": {}, "citations": [], "lang": lang}

    try:
        intent = assistant_mod.classify_intent(text, lang)
    except assistant_mod.IntentClassifyError as exc:
        _log_event("assistant_classify_fallback", "ai_content", str(exc), fallback_used=True)
        intent = assistant_mod.keyword_intent(text, lang)

    # The language Gemini detected in the farmer's own words wins over the
    # app's current UI language, when it's one we support.
    if intent.lang in ("en", "hi", "te"):
        lang = intent.lang

    if not intent.on_topic or intent.intent == "off_topic":
        _log_event("assistant_off_topic", "assistant", text[:120])
        return {"intent": "off_topic", "answer_text": assistant_mod.off_topic_refusal_text(lang),
                "data": {}, "citations": [], "lang": lang}

    handler = _ASSISTANT_HANDLERS.get(intent.intent, _handle_general_farming_qa)
    result = handler(farmer, text, intent, lang)
    return {
        "intent": intent.intent,
        "answer_text": result["answer_text"],
        "data": result.get("data") or {},
        "citations": result.get("citations") or [],
        "lang": lang,
    }


@app.post("/api/demo/run")
def demo_run(request: DemoRequest):
    """Run the deterministic hero scenario end to end on the seeded data.

    One call seeds the demo farmers, produces a recommendation, diagnoses +
    escalates a late-blight case, confirms it as an RSK officer, and fires the
    community alert -- returning the per-step data the farmer app replays with
    narration. Deterministic and Gemini-free, so an unattended judge gets the
    same story every time.
    """
    try:
        return demo.run_demo_scenario(lang=request.lang)
    except Exception as exc:
        _log_event("demo_error", "demo", str(exc))
        raise HTTPException(status_code=503, detail="demo could not run right now")


@app.post("/api/demo/reset")
def demo_reset():
    """Wipe demo-generated cases/alerts/telemetry and re-seed the demo farmers,
    so an unattended judge can always return to a clean, known starting state."""
    try:
        return demo.reset_demo()
    except Exception as exc:
        _log_event("demo_reset_error", "demo", str(exc))
        raise HTTPException(status_code=503, detail="reset could not run right now")


@app.get("/log")
def log_view():
    """The telemetry log view -- "the system explains itself" (a judge sees
    fallbacks fire invisibly and gracefully)."""
    return FileResponse(STATIC_DIR / "log.html")


@app.get("/api/telemetry")
def get_telemetry(limit: int = 50):
    """Recent telemetry entries, newest first, for the /log table."""
    try:
        entries = firestore_client.list_recent_telemetry(limit)
    except Exception as exc:
        _log_event("telemetry_list_error", "api", str(exc))
        raise HTTPException(status_code=503, detail="telemetry temporarily unavailable")
    return {
        "entries": [
            {
                "event": e.event,
                "layer": e.layer,
                "fallback_used": e.fallback_used,
                "detail": e.detail,
                "created_at": e.created_at,
            }
            for e in entries
        ]
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}
