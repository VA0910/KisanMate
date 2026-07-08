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
from engine.crop_scorer import score_crops
from engine.fusion import fuse
from engine.prior_table import CONTAGIOUS
from explain import ExplainError, explain_fusion, template_message
from models import Case, ContextLocation, Farmer, FusionContext, Soil, Telemetry
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
    farmer_id: str
    soil: str
    groundwater_depth_m: float
    agro_zone: str
    seasonal_rainfall_mm: float


class DemoRequest(BaseModel):
    lang: str = "en"


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


@app.post("/api/recommend")
def recommend(request: RecommendRequest):
    # Pure rule engine (no I/O), but wrapped anyway so no endpoint can ever leak a
    # 500: on the off chance scoring raises, the app shows its kind retry state.
    try:
        recommendations = score_crops(
            soil=request.soil,
            groundwater_depth_m=request.groundwater_depth_m,
            agro_zone=request.agro_zone,
            seasonal_rainfall_mm=request.seasonal_rainfall_mm,
        )
    except Exception as exc:
        _log_event("recommend_error", "deterministic", str(exc))
        raise HTTPException(status_code=503, detail="recommendation temporarily unavailable")
    return {
        "farmer_id": request.farmer_id,
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
