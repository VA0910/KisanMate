"""Robustness pass: silent fallbacks and no-500 error handling.

Proves PROJECT_SPEC.md layer 3 end to end -- breaking Gemini (vision + explain),
sending a garbage image, and having no live weather each still produce a useful
farmer-facing result AND a logged fallback, and that no endpoint leaks a 500.
Firestore + Gemini are faked in memory so this runs offline.
"""
import pytest
from fastapi.testclient import TestClient

import firestore_client
import main
from explain import ExplainError
from models import Farmer, FarmerLocation
from vision import VisionError

client = TestClient(main.app)

RAMESH = Farmer(
    id="ramesh",
    name="Ramesh",
    phone="+91-9876500001",
    lang="hi",
    location=FarmerLocation(lat=16.3067, lng=80.4365, mandal="Guntur"),
    crop="tomato",
    land_size_acres=2.5,
    growth_stage="flowering",
)


@pytest.fixture
def fake_store(monkeypatch):
    created_cases: list = []
    telemetry: list = []

    monkeypatch.setattr(firestore_client, "get_farmer", lambda fid: RAMESH if fid == "ramesh" else None)

    def create_case(case):
        case.id = "case-%d" % (len(created_cases) + 1)
        created_cases.append(case)
        return case.id

    monkeypatch.setattr(firestore_client, "create_case", create_case)
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: telemetry.append(e) or "t")
    # make sure no live weather is configured (default), so the weather fallback fires
    monkeypatch.delenv("WEATHER_API_KEY", raising=False)
    return {"cases": created_cases, "telemetry": telemetry}


def _post_diagnose(content=b"\x89PNG\r\n\x1a\nnot-a-real-image", content_type="image/jpeg"):
    return client.post(
        "/api/diagnose",
        data={"farmer_id": "ramesh"},
        files={"image": ("leaf.jpg", content, content_type)},
    )


def _events(telemetry):
    return {t.event for t in telemetry}


def test_broken_gemini_key_still_diagnoses(fake_store, monkeypatch):
    """Gemini unreachable (both vision and explain fail) -> deterministic result."""
    monkeypatch.setattr(main, "diagnose_image", lambda *a, **k: (_ for _ in ()).throw(VisionError("no key")))
    monkeypatch.setattr(main, "explain_fusion", lambda *a, **k: (_ for _ in ()).throw(ExplainError("no key")))

    resp = _post_diagnose()
    assert resp.status_code == 200
    body = resp.json()

    # a useful, farmer-facing message (the Hindi escalate template) and a stored case
    assert body["message"]
    assert body["case_id"] is not None
    assert body["fusion"]["decision"] == "escalate_rsk"  # vision=None always escalates

    events = _events(fake_store["telemetry"])
    assert "vision_fallback" in events
    assert "explain_fallback" in events
    assert all(t.fallback_used for t in fake_store["telemetry"] if t.event in ("vision_fallback", "explain_fallback"))


def test_garbage_image_degrades_gracefully(fake_store, monkeypatch):
    """A garbage/unreadable image (Gemini rejects it) still yields a kind result."""
    monkeypatch.setattr(main, "diagnose_image", lambda *a, **k: (_ for _ in ()).throw(VisionError("bad image")))
    monkeypatch.setattr(main, "explain_fusion", lambda *a, **k: (_ for _ in ()).throw(ExplainError("no key")))

    resp = _post_diagnose(content=b"this is definitely not an image", content_type="text/plain")
    assert resp.status_code == 200
    assert resp.json()["message"]
    assert "vision_fallback" in _events(fake_store["telemetry"])


def test_missing_weather_logs_fallback_and_uses_zone_normal(fake_store, monkeypatch):
    """No live weather provider -> zone-normal defaults + a logged fallback."""
    # vision/explain don't matter here; force them to fail so the flow is offline
    monkeypatch.setattr(main, "diagnose_image", lambda *a, **k: (_ for _ in ()).throw(VisionError("x")))
    monkeypatch.setattr(main, "explain_fusion", lambda *a, **k: (_ for _ in ()).throw(ExplainError("x")))

    resp = _post_diagnose()
    assert resp.status_code == 200

    weather_events = [t for t in fake_store["telemetry"] if t.event == "weather_fallback"]
    assert len(weather_events) == 1
    assert weather_events[0].fallback_used is True
    assert weather_events[0].layer == "context_data"

    # the stored case fell back to zone-normal weather
    assert fake_store["cases"][0].context.weather.source == "zone_normal"


def test_diagnose_never_500s_when_firestore_dies(monkeypatch):
    """Even if Firestore is down before we know the farmer, no raw error leaks."""
    monkeypatch.setattr(firestore_client, "get_farmer", lambda fid: (_ for _ in ()).throw(RuntimeError("db down")))
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")
    resp = _post_diagnose()
    assert resp.status_code == 200  # graceful fallback, not a 500
    assert resp.json()["message"]  # English escalate template


def test_confirm_never_500s_when_firestore_dies(monkeypatch):
    monkeypatch.setattr(firestore_client, "get_case", lambda cid: (_ for _ in ()).throw(RuntimeError("db down")))
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")
    resp = client.post("/api/confirm", json={"case_id": "x", "officer_verdict": "late_blight"})
    assert resp.status_code == 503  # structured, not a 500 stack trace
    assert "detail" in resp.json()


def test_recommend_never_500s(monkeypatch):
    monkeypatch.setattr(main, "score_crops", lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")
    resp = client.post(
        "/api/recommend",
        json={
            "farmer_id": "ramesh",
            "soil": "black",
            "groundwater_depth_m": 8.0,
            "agro_zone": "coastal",
            "seasonal_rainfall_mm": 900.0,
        },
    )
    assert resp.status_code == 503
    assert "detail" in resp.json()


def test_demo_reset_clears_and_reseeds(monkeypatch):
    cleared = {}
    upserted = []
    telemetry = []
    monkeypatch.setattr(firestore_client, "clear_collection", lambda name, **k: cleared.setdefault(name, 3))
    monkeypatch.setattr(firestore_client, "upsert_farmer", lambda f: upserted.append(f.id) or f.id)
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: telemetry.append(e) or "t")

    resp = client.post("/api/demo/reset")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "reset"
    assert set(cleared.keys()) == {"cases", "alerts", "telemetry"}
    assert set(upserted) == {"ramesh", "lakshmi", "venkat", "sita"}
    assert any(t.event == "demo_reset" for t in telemetry)
