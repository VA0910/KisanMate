"""Diagnosis pipeline: confidence/escalation consistency, contagiousness,
plant identification & multi-crop routing (PROJECT_SPEC.md)."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import firestore_client
import main
from engine.fusion import fuse
from engine.prior_table import TOMATO_CONDITIONS, is_contagious
from explain import ExplainError
from models import (
    ContextLocation,
    Crop,
    CropNames,
    CurrentCrop,
    Farmer,
    FarmerLocation,
    FusionContext,
    GrowthStage,
    Soil,
    VisionCandidate,
    VisionOutput,
    Weather,
)
from vision import VisionError

client = TestClient(main.app)


def _confident_late_blight_vision(**over):
    return VisionOutput(
        image_quality="good",
        crop_confirmed="tomato",
        identified_crop=over.get("identified_crop", "tomato"),
        candidates=[
            VisionCandidate(condition="late_blight", confidence=0.85, visible_symptoms=["dark water-soaked lesions"]),
            VisionCandidate(condition="early_blight", confidence=0.15, visible_symptoms=[]),
            VisionCandidate(condition="nitrogen_deficiency", confidence=0.05, visible_symptoms=[]),
        ],
    )


def _cool_wet_context():
    return FusionContext(
        crop="tomato",
        location=ContextLocation(lat=16.3, lng=80.4, resolution="village"),
        weather=Weather(temp_c=19.0, humidity_pct=92.0, rain_48h_mm=12.0, source="live"),
        soil=Soil(nitrogen="unknown", source="unknown"),
        nearby_confirmed=[],
    )


# --- escalation rule -------------------------------------------------------------

def test_confident_contagious_case_is_advise_not_escalated():
    """A confident diagnosis of a CONTAGIOUS disease is still 'advise' to the
    farmer -- contagiousness must not force escalation."""
    result = fuse(_confident_late_blight_vision(), _cool_wet_context())
    assert result.top == "late_blight"
    assert result.decision == "advise"
    assert result.alert_eligible is True  # contagious -> alert-eligible, but NOT escalated


def test_unidentifiable_plant_escalates():
    result = fuse(_confident_late_blight_vision(identified_crop="unidentifiable"), _cool_wet_context())
    assert result.decision == "escalate_rsk"


def test_low_margin_case_escalates():
    """Vision split evenly between two conditions -> low margin -> escalate."""
    vision = VisionOutput(
        image_quality="good", crop_confirmed="tomato", identified_crop="tomato",
        candidates=[
            VisionCandidate(condition="late_blight", confidence=0.5),
            VisionCandidate(condition="early_blight", confidence=0.5),
        ],
    )
    context = FusionContext(
        crop="tomato", location=ContextLocation(lat=16.3, lng=80.4, resolution="village"),
        weather=Weather(temp_c=25.0, humidity_pct=60.0, rain_48h_mm=0.0, source="zone_normal"),
        soil=Soil(nitrogen="unknown", source="unknown"), nearby_confirmed=[],
    )
    assert fuse(vision, context).decision == "escalate_rsk"


# --- crop-driven candidate diseases (Option 2) -----------------------------------

def test_is_contagious_generalizes_beyond_tomato():
    assert is_contagious("late_blight") is True
    assert is_contagious("nitrogen_deficiency") is False
    assert is_contagious("healthy") is False and is_contagious("other") is False
    # named diseases from the crops DB are treated as contagious
    assert is_contagious("rice_blast") is True
    assert is_contagious("bacterial_leaf_blight") is True


def test_diagnosis_candidates_are_crop_driven():
    assert main._diagnosis_candidates("tomato", ["late_blight", "early_blight"]) == list(TOMATO_CONDITIONS)
    assert main._diagnosis_candidates("rice", ["rice_blast", "bacterial_leaf_blight"]) == [
        "rice_blast", "bacterial_leaf_blight", "healthy",
    ]
    # no disease data -> fall back to the calibrated tomato set
    assert main._diagnosis_candidates("wheat", []) == list(TOMATO_CONDITIONS)


def test_fuse_over_non_tomato_candidates_is_vision_led():
    vision = VisionOutput(
        image_quality="good", crop_confirmed="rice", identified_crop="rice", matches_profile=True,
        candidates=[
            VisionCandidate(condition="rice_blast", confidence=0.8, visible_symptoms=["diamond lesions"]),
            VisionCandidate(condition="bacterial_leaf_blight", confidence=0.2),
        ],
    )
    ctx = FusionContext(
        crop="rice", location=ContextLocation(lat=16.3, lng=80.4, resolution="village"),
        weather=Weather(temp_c=28, humidity_pct=80, rain_48h_mm=5, source="live"),
        soil=Soil(nitrogen="unknown", source="unknown"),
    )
    result = fuse(vision, ctx, candidates=["rice_blast", "bacterial_leaf_blight", "sheath_blight", "healthy"])
    assert result.top == "rice_blast"           # vision-led (no calibrated prior off-tomato)
    assert result.decision == "advise"          # confident + clear margin, no false conflict
    conds = {p.condition: p.contagious for p in result.posterior}
    assert conds["rice_blast"] is True and conds["healthy"] is False
    assert result.alert_eligible is True


# --- plant identification & multi-crop routing -----------------------------------

WHEAT = Crop(
    id="wheat", names=CropNames(en="Wheat", hi="गेहूं", te="గోధుమ"),
    seasons=["rabi"], soil_types=["loam"], water_need="medium", regions=["irrigated"],
    cycle_days=125, growth_stages=[GrowthStage(name="sowing", start_day=0, care_note="x")],
    susceptible_diseases=["leaf_rust", "stripe_rust"],
)


def _tomato_farmer():
    return Farmer(
        id="ramesh", name="Ramesh", phone="9876500001", lang="en",
        location=FarmerLocation(lat=16.3, lng=80.4, mandal="Guntur"),
        crop="tomato", land_size_acres=2.0, growth_stage="flowering", soil_type="black",
        current_crops=[CurrentCrop(crop_id="tomato", planting_date="2026-05-01")],
    )


def test_resolve_matching_crop_sets_matches_profile_true():
    ctx = FusionContext(
        crop="tomato", location=ContextLocation(lat=16.3, lng=80.4, resolution="village"),
        weather=Weather(temp_c=25, humidity_pct=60, rain_48h_mm=0, source="zone_normal"),
        soil=Soil(nitrogen="unknown", source="unknown"), nearby_confirmed=[],
    )
    vision = _confident_late_blight_vision(identified_crop="tomato")
    main._resolve_diagnosed_crop(vision, _tomato_farmer(), ctx)
    assert vision.matches_profile is True
    assert ctx.crop == "tomato"  # unchanged


def test_resolve_non_matching_crop_repoints_to_identified_profile(monkeypatch):
    monkeypatch.setattr(firestore_client, "get_crop", lambda cid: WHEAT if cid == "wheat" else None)
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")
    ctx = FusionContext(
        crop="tomato", location=ContextLocation(lat=16.3, lng=80.4, resolution="village"),
        weather=Weather(temp_c=25, humidity_pct=60, rain_48h_mm=0, source="zone_normal"),
        soil=Soil(nitrogen="unknown", source="unknown"), nearby_confirmed=[],
    )
    vision = _confident_late_blight_vision(identified_crop="wheat")
    main._resolve_diagnosed_crop(vision, _tomato_farmer(), ctx)
    assert vision.matches_profile is False
    assert ctx.crop == "wheat"  # repointed to the identified plant
    assert ctx.susceptible_diseases == ["leaf_rust", "stripe_rust"]


# --- endpoint: confident result is confident, no officer -------------------------

def test_diagnose_advise_has_no_officer_mention(monkeypatch):
    """A clear (advise) result: no officer mention even on the deterministic
    fallback message, and decision stays 'advise'."""
    monkeypatch.setattr(firestore_client, "get_farmer", lambda fid: _tomato_farmer())
    monkeypatch.setattr(firestore_client, "get_crop", lambda cid: None)
    monkeypatch.setattr(firestore_client, "create_case", lambda case: "case-1")
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")
    # Pin a cool/wet context so the confident late_blight vision agrees with the
    # prior (no conflict) and the decision is genuinely "advise".
    monkeypatch.setattr(main, "build_context", lambda farmer: _cool_wet_context())
    monkeypatch.setattr(main, "diagnose_image", lambda *a, **k: _confident_late_blight_vision())
    # Force the fallback path so we assert the deterministic advise template.
    monkeypatch.setattr(main, "explain_fusion", lambda *a, **k: (_ for _ in ()).throw(ExplainError("no key")))

    resp = client.post(
        "/api/diagnose",
        data={"farmer_id": "ramesh"},
        files={"image": ("leaf.jpg", b"img", "image/jpeg")},
    )
    body = resp.json()
    assert body["fusion"]["decision"] == "advise"
    assert "officer" not in body["message"].lower()


def _rice_farmer():
    return Farmer(
        id="venkat", name="Venkat", phone="9876500003", lang="en",
        location=FarmerLocation(lat=16.3, lng=80.45, mandal="Guntur"),
        crop="rice", land_size_acres=3.0, growth_stage="tillering", soil_type="clay",
        current_crops=[CurrentCrop(crop_id="rice", planting_date="2026-06-01")],
    )


def test_diagnose_non_tomato_uses_crop_specific_disease(monkeypatch):
    """A rice farmer's photo is diagnosed against RICE diseases from the crops DB,
    not the hardcoded tomato list."""
    rice_ctx = FusionContext(
        crop="rice", location=ContextLocation(lat=16.3, lng=80.45, resolution="village"),
        weather=Weather(temp_c=28, humidity_pct=85, rain_48h_mm=8, source="live"),
        soil=Soil(nitrogen="unknown", source="unknown"),
        susceptible_diseases=["rice_blast", "bacterial_leaf_blight", "sheath_blight"],
    )
    rice_vision = VisionOutput(
        image_quality="good", crop_confirmed="rice", identified_crop="rice", matches_profile=True,
        candidates=[
            VisionCandidate(condition="rice_blast", confidence=0.82, visible_symptoms=["spindle lesions"]),
            VisionCandidate(condition="sheath_blight", confidence=0.18),
        ],
    )
    monkeypatch.setattr(firestore_client, "get_farmer", lambda fid: _rice_farmer())
    monkeypatch.setattr(firestore_client, "create_case", lambda case: "case-r")
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")
    monkeypatch.setattr(main, "build_context", lambda farmer: rice_ctx)
    captured = {}

    def fake_vision(image_bytes, mime_type="image/jpeg", candidate_conditions=None):
        captured["cands"] = candidate_conditions or []
        return rice_vision

    monkeypatch.setattr(main, "diagnose_image", fake_vision)
    monkeypatch.setattr(main, "explain_fusion", lambda *a, **k: (_ for _ in ()).throw(ExplainError("no key")))

    resp = client.post("/api/diagnose", data={"farmer_id": "venkat"},
                       files={"image": ("leaf.jpg", b"img", "image/jpeg")})
    body = resp.json()
    assert body["fusion"]["top"] == "rice_blast"                 # diagnosed a RICE disease
    assert "rice_blast" in captured["cands"]                     # vision was scoped to rice diseases
    assert "late_blight" not in captured["cands"]                # not the tomato list
    # Structured explanation is returned with the three parts, none mentioning the officer.
    expl = body["explanation"]
    assert set(expl) == {"what", "why", "what_to_do"}
    assert all(expl.values())
    assert "officer" not in (expl["what"] + expl["why"] + expl["what_to_do"]).lower()


def test_diagnose_escalate_explanation_mentions_officer(monkeypatch):
    monkeypatch.setattr(firestore_client, "get_farmer", lambda fid: _tomato_farmer())
    monkeypatch.setattr(firestore_client, "get_crop", lambda cid: None)
    monkeypatch.setattr(firestore_client, "create_case", lambda case: "case-1")
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")
    # Vision unavailable -> vision=None -> escalate; explain unavailable -> template.
    monkeypatch.setattr(main, "diagnose_image", lambda *a, **k: (_ for _ in ()).throw(VisionError("no key")))
    monkeypatch.setattr(main, "explain_fusion", lambda *a, **k: (_ for _ in ()).throw(ExplainError("no key")))
    resp = client.post(
        "/api/diagnose", data={"farmer_id": "ramesh"},
        files={"image": ("leaf.jpg", b"img", "image/jpeg")},
    )
    body = resp.json()
    assert body["fusion"]["decision"] == "escalate_rsk"
    assert "officer" in body["explanation"]["what_to_do"].lower()


# --- farmer dispute wiring -------------------------------------------------------

def test_dispute_marks_disputed_without_verdict_or_alert(monkeypatch):
    from models import Case
    updates = {}
    alerts = []
    case = Case(id="c1", farmer_id="ramesh", status="escalated")
    monkeypatch.setattr(firestore_client, "get_case", lambda cid: case)
    monkeypatch.setattr(firestore_client, "update_case", lambda cid, u: updates.update(u))
    monkeypatch.setattr(firestore_client, "create_alert", lambda a: alerts.append(a) or "a1")
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")

    resp = client.post("/api/cases/c1/dispute")
    assert resp.status_code == 200
    assert resp.json()["status"] == "disputed"
    assert updates == {"status": "disputed"}  # ONLY status -- no officer_verdict
    assert alerts == []  # dispute never propagates an alert


def test_cases_queue_includes_disputed_category(monkeypatch):
    captured = {}

    def fake_list(statuses):
        captured["st"] = statuses
        return []

    monkeypatch.setattr(firestore_client, "list_cases_by_status", fake_list)
    monkeypatch.setattr(firestore_client, "list_farmers", lambda: [])
    client.get("/api/cases")
    assert "disputed" in captured["st"] and "escalated" in captured["st"]
