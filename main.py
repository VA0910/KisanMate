from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import firestore_client
from engine.crop_scorer import score_crops
from engine.fusion import fuse
from explain import ExplainError, explain_fusion, template_message
from models import Case, ContextLocation, Farmer, FusionContext, Soil, Telemetry, Weather
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


class ConfirmRequest(BaseModel):
    case_id: str
    officer_verdict: str


class RecommendRequest(BaseModel):
    farmer_id: str
    soil: str
    groundwater_depth_m: float
    agro_zone: str
    seasonal_rainfall_mm: float


def _build_context(farmer: Farmer) -> FusionContext:
    """Stub context until live weather/soil/nearby-case sourcing exists.

    Uses the farmer's own crop and registered location; weather/soil are marked
    with degraded sources ("zone_normal"/"unknown") rather than invented live
    readings, so the prior table degrades exactly the way it's designed to when
    real data isn't available. nearby_confirmed is left empty pending the
    community alert engine, which is what will populate it.
    """
    return FusionContext(
        crop=farmer.crop,
        location=ContextLocation(lat=farmer.location.lat, lng=farmer.location.lng, resolution="village"),
        weather=Weather(temp_c=28.0, humidity_pct=70.0, rain_48h_mm=0.0, source="zone_normal"),
        soil=Soil(nitrogen="unknown", source="unknown"),
        nearby_confirmed=[],
    )


def _log_fallback(event: str, detail: str) -> None:
    try:
        firestore_client.log_telemetry(
            Telemetry(event=event, layer="ai_content", detail={"error": detail}, fallback_used=True)
        )
    except Exception:
        pass  # telemetry is best-effort; a logging failure must never surface to the farmer


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


@app.post("/api/confirm")
def confirm(request: ConfirmRequest):
    return {
        "case_id": request.case_id,
        "status": "confirmed",
        "officer_verdict": request.officer_verdict,
        "message": "placeholder response - confirmation logic not yet implemented",
    }


@app.get("/api/alerts/{farmer_id}")
def get_alerts(farmer_id: str):
    return {
        "farmer_id": farmer_id,
        "alerts": [],
        "message": "placeholder response - alert lookup not yet implemented",
    }


@app.post("/api/recommend")
def recommend(request: RecommendRequest):
    recommendations = score_crops(
        soil=request.soil,
        groundwater_depth_m=request.groundwater_depth_m,
        agro_zone=request.agro_zone,
        seasonal_rainfall_mm=request.seasonal_rainfall_mm,
    )
    return {
        "farmer_id": request.farmer_id,
        "recommendations": [rec.model_dump() for rec in recommendations],
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}
