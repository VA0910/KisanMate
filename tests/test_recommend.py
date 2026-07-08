"""Conversational, voice-first crop recommendation: /api/recommend/ask.

Firestore/Gemini are faked so these run offline. Covers the grounded Gemini path,
the deterministic fallback (grounded + rotation-aware), and no-500 robustness.
"""
import pytest
from fastapi.testclient import TestClient

import firestore_client
import main
from explain import RecommendExplainError
from models import (
    CurrentCrop,
    Crop,
    CropNames,
    Farmer,
    FarmerLocation,
    GrowthStage,
)

client = TestClient(main.app)


def _crop(cid, en, water="medium", soils=("black",), diseases=()):
    return Crop(
        id=cid, names=CropNames(en=en, hi=en, te=en),
        seasons=["kharif"], soil_types=list(soils), water_need=water, regions=["coastal"],
        cycle_days=100, growth_stages=[GrowthStage(name="veg", start_day=0, care_note="x")],
        susceptible_diseases=list(diseases),
    )


CROPS = [
    _crop("tomato", "Tomato", diseases=["late_blight"]),
    _crop("black_gram", "Black Gram", water="low"),
    _crop("brinjal", "Brinjal"),
]


def _ramesh():
    return Farmer(
        id="ramesh", name="Ramesh", phone="9876500001", lang="en",
        location=FarmerLocation(lat=16.3, lng=80.4, mandal="Guntur"),
        crop="tomato", land_size_acres=2.0, growth_stage="flowering", soil_type="black",
        current_crops=[CurrentCrop(crop_id="tomato", planting_date="2026-05-01")],
    )


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(firestore_client, "get_farmer", lambda fid: _ramesh())
    monkeypatch.setattr(firestore_client, "list_crops", lambda: CROPS)
    monkeypatch.setattr(firestore_client, "get_crop", lambda cid: next((c for c in CROPS if c.id == cid), None))
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")


def test_ask_uses_grounded_gemini_answer(store, monkeypatch):
    captured = {}

    def fake_convo(question, profile, grounding, language):
        captured["profile"] = profile
        captured["grounding_ids"] = [g["crop_id"] for g in grounding]
        return {
            "recommendations": [{"crop_id": "black_gram", "crop_name": "Black Gram",
                                 "why": "A nitrogen-fixing rotation after tomato on your black soil."}],
            "spoken": "Try black gram next — it restores nitrogen after tomatoes.",
        }

    monkeypatch.setattr(main, "recommend_conversational", fake_convo)
    resp = client.post("/api/recommend/ask", json={"farmer_id": "ramesh", "question": "what after tomatoes?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["recommendations"][0]["crop_id"] == "black_gram"
    assert body["spoken"]
    # the full profile context was passed to Gemini
    assert captured["profile"]["soil_type"] == "black"
    assert captured["profile"]["season"] in ("kharif", "rabi", "zaid")
    assert captured["profile"]["current_crops"] == ["tomato"]
    # grounding came from the crops DB
    assert "black_gram" in captured["grounding_ids"]


def test_ask_falls_back_deterministically_when_gemini_fails(store, monkeypatch):
    monkeypatch.setattr(main, "recommend_conversational",
                        lambda *a, **k: (_ for _ in ()).throw(RecommendExplainError("down")))
    resp = client.post("/api/recommend/ask",
                       json={"farmer_id": "ramesh", "question": "what should I grow after tomatoes?"})
    assert resp.status_code == 200
    body = resp.json()
    recs = body["recommendations"]
    assert recs and all(r["crop_id"] in {"tomato", "black_gram", "brinjal"} for r in recs)  # grounded
    # rotation-aware, soil-aware deterministic reason
    blob = " ".join(r["why"] for r in recs).lower()
    assert "black soil" in blob and "tomato" in blob
    assert body["spoken"]


def test_ask_never_500s_when_crops_unavailable(store, monkeypatch):
    monkeypatch.setattr(firestore_client, "list_crops", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    resp = client.post("/api/recommend/ask", json={"farmer_id": "ramesh", "question": "hi"})
    assert resp.status_code == 503
    assert "detail" in resp.json()
