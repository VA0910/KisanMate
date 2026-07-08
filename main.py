import random
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import demo
import firestore_client
import services
import weather as weather_source
from engine.crop_recommender import current_season, rank_crops
from engine.fusion import fuse
from engine.prior_table import CONTAGIOUS
from explain import (
    ExplainError,
    RecommendExplainError,
    explain_fusion,
    explain_recommendations,
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

# The farmer-facing frontend: plain HTML/CSS/JS, no build step (PROJECT_SPEC.md).
# Mounted under /static so it never shadows the /api/* routes below; "/" itself
# serves static/index.html so the app still opens at the site root.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/rsk")
def rsk_dashboard():
    """The RSK officer dashboard -- the human-in-the-loop confirmation gate.

    A separate, denser static page from the farmer app (officers are literate
    desk users), served from the same FastAPI app.
    """
    return FileResponse(STATIC_DIR / "rsk.html")


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


def _build_context(farmer: Farmer) -> FusionContext:
    """Assemble the fusion context for a farmer, degrading gracefully per source.

    Weather is sourced through weather.get_weather(); if it's unavailable (no live
    provider, or a failed/timed-out fetch) we log a telemetry fallback and drop to
    zone-normal defaults, so the prior table still runs on honest degraded data
    instead of surfacing an error. Soil/nearby are still marked degraded pending
    their own live sourcing.
    """
    try:
        weather_reading = weather_source.get_weather(farmer.location.lat, farmer.location.lng)
    except Exception as exc:
        _log_event("weather_fallback", "context_data", str(exc), fallback_used=True)
        weather_reading = weather_source.zone_normal_weather()

    return FusionContext(
        crop=farmer.crop,
        location=ContextLocation(lat=farmer.location.lat, lng=farmer.location.lng, resolution="village"),
        weather=weather_reading,
        soil=Soil(nitrogen="unknown", source="unknown"),
        nearby_confirmed=[],
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
        context = _build_context(farmer)

        vision_result = None
        try:
            vision_result = diagnose_image(image_bytes, mime_type=image.content_type or "image/jpeg")
        except VisionError as exc:
            _log_fallback("vision_fallback", str(exc))

        fusion_result = fuse(vision_result, context)

        try:
            message = explain_fusion(fusion_result, farmer.lang)
        except ExplainError as exc:
            _log_fallback("explain_fallback", str(exc))
            message = template_message(fusion_result.decision, farmer.lang)

        case = Case(
            farmer_id=farmer_id,
            image_note=image_note,
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

        return {"case_id": case_id, "fusion": fusion_result.model_dump(), "message": message}

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
        "status": case.status,
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
    """The RSK officer's review queue: every case still awaiting a human verdict."""
    try:
        cases = firestore_client.list_cases_by_status(["pending", "escalated"])
        farmers_by_id = {f.id: f for f in firestore_client.list_farmers()}
    except Exception as exc:
        # Surface a real load failure instead of a false-empty queue: an officer
        # must not mistake "database down" for "no cases need review". The
        # dashboard renders 503 as a kind "couldn't load, retry" state.
        _log_event("cases_list_error", "api", str(exc))
        raise HTTPException(status_code=503, detail="cases temporarily unavailable")
    return {"cases": [_case_row(case, farmers_by_id) for case in cases]}


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
    contagious = CONTAGIOUS.get(verdict, False)

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
    """
    season = request.season or current_season()
    try:
        crops = firestore_client.list_crops()
        recommendations = rank_crops(
            crops,
            soil_type=request.resolved_soil(),
            season=season,
            region=request.resolved_region(),
        )
    except Exception as exc:
        _log_event("recommend_error", "deterministic", str(exc))
        raise HTTPException(status_code=503, detail="recommendation temporarily unavailable")

    # AI content layer: warm reason text on top of the deterministic reasons.
    try:
        farmer = firestore_client.get_farmer(request.farmer_id)
        lang = farmer.lang if farmer else "en"
    except Exception:
        lang = "en"
    try:
        reasons = explain_recommendations(recommendations, lang)
        for rec in recommendations:
            if rec.crop in reasons and reasons[rec.crop].strip():
                rec.reason = reasons[rec.crop].strip()
    except RecommendExplainError as exc:
        _log_fallback("recommend_explain_fallback", str(exc))

    return {
        "farmer_id": request.farmer_id,
        "season": season,
        "recommendations": [rec.model_dump() for rec in recommendations],
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
