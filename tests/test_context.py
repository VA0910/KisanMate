"""Personalization: build_context + the profile-aware AI prompts/fallbacks.

Proves PROJECT_SPEC.md "Context into Gemini": the fusion context is assembled
from the signed-in farmer's profile (soil type + a growth stage computed
deterministically from planting_date and the crop DB), and that both the
diagnosis message and the recommendation reference that soil and growth stage --
even on the deterministic fallback path (no Gemini), which is what runs here.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import firestore_client
import main
from engine.growth import compute_stage
from explain import ExplainError, recommendation_note, template_message
from models import (
    Crop,
    CropNames,
    CurrentCrop,
    Farmer,
    FarmerLocation,
    GrowthStage,
)
from vision import VisionError

client = TestClient(main.app)

TOMATO = Crop(
    id="tomato",
    names=CropNames(en="Tomato", hi="टमाटर", te="టమాటా"),
    seasons=["kharif", "rabi", "zaid"],
    soil_types=["black", "red", "loam"],
    water_need="medium",
    regions=["coastal"],
    cycle_days=120,
    growth_stages=[
        GrowthStage(name="nursery", start_day=0, care_note="x"),
        GrowthStage(name="transplanting", start_day=25, care_note="x"),
        GrowthStage(name="vegetative", start_day=35, care_note="x"),
        GrowthStage(name="flowering", start_day=55, care_note="x"),
        GrowthStage(name="fruiting", start_day=75, care_note="x"),
        GrowthStage(name="harvest", start_day=100, care_note="x"),
    ],
    susceptible_diseases=["late_blight", "early_blight"],
)


def _farmer(lang="en", planted_days_ago=60):
    planting = (date.today() - timedelta(days=planted_days_ago)).isoformat()
    return Farmer(
        id="ramesh",
        name="Ramesh",
        phone="9876500001",
        lang=lang,
        location=FarmerLocation(lat=16.3, lng=80.4, mandal="Guntur"),
        crop="tomato",
        land_size_acres=2.5,
        growth_stage="flowering",
        soil_type="black",
        current_crops=[CurrentCrop(crop_id="tomato", planting_date=planting)],
    )


# --- deterministic growth stage --------------------------------------------------

def test_compute_stage_picks_flowering_at_day_60():
    planting = (date.today() - timedelta(days=60)).isoformat()
    stage = compute_stage(planting, TOMATO.cycle_days, TOMATO.growth_stages)
    assert stage["name"] == "flowering"  # 55 <= 60 < 75
    assert stage["day"] == 60
    assert stage["past_harvest"] is False


def test_compute_stage_none_without_planting_date():
    assert compute_stage(None, TOMATO.cycle_days, TOMATO.growth_stages) is None


# --- place search (geocoder proxy) -----------------------------------------------

def test_places_search_parses_geocoder(monkeypatch):
    # Mock the geocoder so the test is offline and deterministic.
    monkeypatch.setattr(
        main,
        "_geocode_places",
        lambda q: [{"name": "Kanpur, Uttar Pradesh", "lat": 26.46, "lng": 80.32}],
    )
    resp = client.get("/api/places", params={"q": "kanpur"})
    assert resp.status_code == 200
    places = resp.json()["places"]
    assert places and places[0]["name"] == "Kanpur, Uttar Pradesh"
    assert places[0]["lat"] == 26.46 and places[0]["lng"] == 80.32


def test_places_search_short_query_returns_empty():
    assert client.get("/api/places", params={"q": "ka"}).json()["places"] == []


def test_places_search_degrades_on_geocoder_failure(monkeypatch):
    monkeypatch.setattr(main, "_geocode_places", lambda q: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")
    resp = client.get("/api/places", params={"q": "kanpur"})
    assert resp.status_code == 200  # never a 500
    assert resp.json()["places"] == []


# --- reverse geocoding (device location -> district name) ------------------------

def test_places_reverse_parses_geocoder(monkeypatch):
    monkeypatch.setattr(main, "_reverse_geocode", lambda lat, lng: "Kanpur, Uttar Pradesh")
    resp = client.get("/api/places/reverse", params={"lat": 26.46, "lng": 80.32})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Kanpur, Uttar Pradesh"


def test_places_reverse_degrades_on_geocoder_failure(monkeypatch):
    monkeypatch.setattr(main, "_reverse_geocode", lambda lat, lng: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")
    resp = client.get("/api/places/reverse", params={"lat": 26.46, "lng": 80.32})
    assert resp.status_code == 200  # never a 500
    assert resp.json()["name"] is None


# --- build_context ---------------------------------------------------------------

def test_build_context_carries_soil_and_growth_stage(monkeypatch):
    monkeypatch.setattr(firestore_client, "get_crop", lambda cid: TOMATO if cid == "tomato" else None)
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")

    context = main.build_context(_farmer())
    assert context.crop == "tomato"
    assert context.soil.type == "black"
    assert context.growth_stage == "flowering"
    assert context.crop_day == 60
    # deterministic contract fields still present
    assert context.weather.source in ("live", "cache", "zone_normal")
    assert context.soil.nitrogen == "unknown"


# --- deterministic fallbacks (no Gemini) -----------------------------------------

def test_template_message_never_prefixes_with_soil(monkeypatch):
    # PROJECT_SPEC.md: no farmer message may open with "For your <soil> soil".
    monkeypatch.setattr(firestore_client, "get_crop", lambda cid: TOMATO)
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")
    context = main.build_context(_farmer(lang="en"))
    advise = template_message("advise", "en", context)
    escalate = template_message("escalate_rsk", "en", context)
    assert not advise.lower().startswith("for your")
    assert not escalate.lower().startswith("for your")
    # advise is confident: no officer mention; escalate hands off to the officer.
    assert "officer" not in advise.lower()
    assert "officer" in escalate.lower()


def test_recommendation_note_references_soil_and_stage(monkeypatch):
    monkeypatch.setattr(firestore_client, "get_crop", lambda cid: TOMATO)
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")
    context = main.build_context(_farmer(lang="en"))
    note = recommendation_note(context, "en")
    assert "black" in note
    assert "flowering" in note


# --- end-to-end diagnose on the deterministic path -------------------------------

def test_diagnose_message_has_no_soil_prefix_on_fallback(monkeypatch):
    """With Gemini's vision + explain unavailable, the diagnosis falls back to the
    deterministic template -- which must NOT open with "For your <soil> soil" and,
    since vision=None escalates, hands off to the officer."""
    monkeypatch.setattr(firestore_client, "get_farmer", lambda fid: _farmer(lang="en"))
    monkeypatch.setattr(firestore_client, "get_crop", lambda cid: TOMATO)
    monkeypatch.setattr(firestore_client, "create_case", lambda case: "case-1")
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")
    monkeypatch.setattr(main, "diagnose_image", lambda *a, **k: (_ for _ in ()).throw(VisionError("no key")))
    monkeypatch.setattr(main, "explain_fusion", lambda *a, **k: (_ for _ in ()).throw(ExplainError("no key")))

    resp = client.post(
        "/api/diagnose",
        data={"farmer_id": "ramesh"},
        files={"image": ("leaf.jpg", b"not-a-real-image", "image/jpeg")},
    )
    assert resp.status_code == 200
    msg = resp.json()["message"]
    assert not msg.lower().startswith("for your")
    # the deterministic core is unchanged: vision=None still escalates
    assert resp.json()["fusion"]["decision"] == "escalate_rsk"
