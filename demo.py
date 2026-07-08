"""Deterministic hero-scenario runner for the unattended judge demo.

Plays the full PROJECT_SPEC.md story on the seeded farmers -- (a) Ramesh gets a
data-grounded crop recommendation, (b) his sick-leaf photo is diagnosed as late
blight and escalated, (c) an RSK officer confirms, (d) Lakshmi (tomato, 3km) is
alerted while Venkat (rice, next door) is not -- using ONLY the deterministic
engine. No Gemini call is made, so the outcome is byte-for-byte reproducible
offline. That is not a shortcut: it is exactly the layer-1 fallback path the
system takes whenever the AI layer is unavailable, which is why the run emits
`fallback_used=True` telemetry -- the /log view then tells the same story of
fallbacks firing invisibly and gracefully.
"""
import firestore_client
import services
from engine.crop_scorer import score_crops
from engine.fusion import fuse
from explain import template_message
from models import (
    Case,
    ContextLocation,
    FusionContext,
    Soil,
    Telemetry,
    VisionCandidate,
    VisionOutput,
    Weather,
)
from seed import DEMO_FARMERS

# Ramesh's field profile (Guntur, Krishna delta): concrete, data-grounded inputs
# for the deterministic crop scorer -- black soil, shallow-ish water, good rain.
RAMESH_FIELD = {
    "soil": "black",
    "groundwater_depth_m": 8.0,
    "agro_zone": "coastal",
    "seasonal_rainfall_mm": 900.0,
}

# Deterministic vision fixture. The camera alone leans early_blight, but the
# cool/wet prior (below) pulls the fused verdict to late_blight -- and because
# the two disagree on a contagious disease, fusion escalates instead of guessing.
# (Verified: top=late_blight, decision=escalate_rsk.)
DEMO_VISION = VisionOutput(
    image_quality="good",
    crop_confirmed="tomato",
    candidates=[
        VisionCandidate(
            condition="early_blight",
            confidence=0.55,
            visible_symptoms=["concentric ring spots", "yellowing around spots"],
        ),
        VisionCandidate(
            condition="late_blight",
            confidence=0.30,
            visible_symptoms=["dark water-soaked lesions", "pale mould on leaf underside"],
        ),
        VisionCandidate(condition="nitrogen_deficiency", confidence=0.10, visible_symptoms=[]),
    ],
    notes="Deterministic demo fixture.",
)

DEMO_IMAGE_NOTE = "Dark patches spreading on the lower leaves"


def _demo_context(farmer) -> FusionContext:
    """Cool, humid, recently-rained conditions -- the environment that strongly
    favours late blight (the prior half of the fusion story)."""
    return FusionContext(
        crop="tomato",
        location=ContextLocation(lat=farmer.location.lat, lng=farmer.location.lng, resolution="village"),
        weather=Weather(temp_c=19.0, humidity_pct=92.0, rain_48h_mm=10.0, source="cache"),
        soil=Soil(nitrogen="unknown", source="unknown"),
        nearby_confirmed=[],
    )


def _log(event: str, layer: str, detail: dict, fallback_used: bool = False) -> None:
    try:
        firestore_client.log_telemetry(
            Telemetry(event=event, layer=layer, detail=detail, fallback_used=fallback_used)
        )
    except Exception:
        pass  # telemetry is best-effort and must never break the demo


def _farmer(farmer_id: str):
    return next((f for f in DEMO_FARMERS if f.id == farmer_id), None)


def _alert_payload(alerts: list) -> list[dict]:
    alerts.sort(key=lambda a: (a.created_at is not None, a.created_at), reverse=True)
    return [
        {"condition": a.condition, "tier": a.tier, "radius_km": a.radius_km, "created_at": a.created_at}
        for a in alerts
    ]


def reset_demo() -> dict:
    """Return the app to a clean, known state for the next judge run.

    Wipes demo-generated cases, alerts, and telemetry, then re-seeds the demo
    farmers (idempotent). Logs a single `demo_reset` marker afterwards so /log
    starts from a clear, self-explaining baseline.
    """
    cleared = {}
    for name in (
        firestore_client.CASES_COLLECTION,
        firestore_client.ALERTS_COLLECTION,
        firestore_client.TELEMETRY_COLLECTION,
    ):
        cleared[name] = firestore_client.clear_collection(name)

    for farmer in DEMO_FARMERS:
        firestore_client.upsert_farmer(farmer)

    _log("demo_reset", "demo", {"cleared": cleared, "farmers": len(DEMO_FARMERS)})
    return {"status": "reset", "cleared": cleared, "farmers": [f.id for f in DEMO_FARMERS]}


def run_demo_scenario(lang: str = "en") -> dict:
    """Run the whole story end to end and return the per-step data the frontend
    replays. `lang` controls the language of the farmer-facing diagnosis message
    (the deterministic template)."""
    # 0. Make the run self-contained: (re)seed the demo farmers (idempotent).
    for farmer in DEMO_FARMERS:
        firestore_client.upsert_farmer(farmer)
    _log("demo_started", "demo", {"scenario": "community_alert_hero"})

    ramesh = _farmer("ramesh")
    lakshmi = _farmer("lakshmi")
    venkat = _farmer("venkat")

    # (a) Data-grounded crop recommendation (pure rule engine).
    recommendations = score_crops(**RAMESH_FIELD)
    _log(
        "crop_recommendation",
        "deterministic",
        {"farmer": "ramesh", "top": recommendations[0].crop, "field": RAMESH_FIELD},
    )

    # (b) Diagnose the sick leaf on the deterministic path. Logging vision +
    # explain as fallbacks is honest: this is the exact route taken when Gemini
    # is unavailable, and it keeps the demo reproducible.
    _log(
        "vision_fallback",
        "ai_content",
        {"error": "Demo runs offline on the deterministic engine; Gemini vision not called."},
        fallback_used=True,
    )
    context = _demo_context(ramesh)
    fusion = fuse(DEMO_VISION, context)
    message = template_message(fusion.decision, lang)
    _log(
        "explain_fallback",
        "ai_content",
        {"error": "Explanation served from the deterministic template; Gemini explain not called."},
        fallback_used=True,
    )

    top_contagious = next((p.contagious for p in fusion.posterior if p.condition == fusion.top), False)
    case = Case(
        farmer_id="ramesh",
        image_note=DEMO_IMAGE_NOTE,
        vision=DEMO_VISION,
        context=context,
        fusion=fusion,
        status="advised" if fusion.decision == "advise" else "escalated",
        condition=fusion.top,
        contagious=top_contagious,
    )
    case_id = firestore_client.create_case(case)
    case.id = case_id
    _log(
        "diagnosis_fused",
        "deterministic",
        {"top": fusion.top, "confidence": round(fusion.confidence, 3), "decision": fusion.decision},
    )

    # (c) RSK officer confirms late blight -- the authoritative human verdict
    #     that also passes the alert engine's confirmation gate.
    farmers = firestore_client.list_farmers()
    alert_summary = services.confirm_and_propagate(
        case,
        "late_blight",
        farmers,
        error_logger=lambda exc: _log("alert_propagation_error", "alert_engine", {"error": str(exc)}),
    )
    _log("rsk_confirmed", "human_override", {"case_id": case_id, "verdict": "late_blight"})
    if alert_summary is not None:
        _log(
            "community_alert_fired",
            "alert_engine",
            {"tier": alert_summary["tier"], "recipients": alert_summary["recipient_count"]},
        )

    # (d) Who received it: Lakshmi (tomato, in range) yes, Venkat (rice) no.
    lakshmi_alerts = firestore_client.list_alerts_for_farmer("lakshmi")
    venkat_alerts = firestore_client.list_alerts_for_farmer("venkat")
    _log(
        "demo_completed",
        "demo",
        {"lakshmi_alerts": len(lakshmi_alerts), "venkat_alerts": len(venkat_alerts)},
    )

    return {
        "recommend": {
            "farmer_name": ramesh.name,
            "recommendations": [rec.model_dump() for rec in recommendations],
        },
        "diagnose": {
            "farmer_name": ramesh.name,
            "farmer_lang": ramesh.lang,
            "case_id": case_id,
            "condition": fusion.top,
            "fusion": fusion.model_dump(),
            "message": message,
        },
        "confirm": {"verdict": "late_blight", "alert": alert_summary},
        "alerts": {
            "lakshmi": {
                "farmer_name": lakshmi.name,
                "lang": lakshmi.lang,
                "crop": lakshmi.crop,
                "alerts": _alert_payload(lakshmi_alerts),
            },
            "venkat": {
                "farmer_name": venkat.name,
                "lang": venkat.lang,
                "crop": venkat.crop,
                "alerts": _alert_payload(venkat_alerts),
            },
        },
    }
