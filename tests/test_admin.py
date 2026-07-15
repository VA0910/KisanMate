"""Officer portal + admin auth (PROJECT_SPEC.md): separate login, two case
categories (AI needs review / Farmer disputed), photo storage, and confirm->alert.

Firestore/Gemini are faked in memory so these run without credentials.
"""
import pytest
from fastapi.testclient import TestClient

import firestore_client
import main
from explain import ExplainError
from models import (
    Case,
    ContextLocation,
    FusionContext,
    FusionEvidence,
    FusionOutput,
    PosteriorEntry,
    Soil,
    VisionCandidate,
    VisionOutput,
    Weather,
)
from seed import DEMO_FARMERS
from vision import VisionError

client = TestClient(main.app)


# --- admin auth + pages ----------------------------------------------------------

def test_admin_and_rsk_pages_served():
    assert client.get("/admin").status_code == 200
    assert client.get("/rsk").status_code == 200  # legacy alias


def test_admin_demo_credentials_shown():
    body = client.get("/api/admin/demo-credentials").json()
    assert body["username"] and body["password"]


def test_admin_login_success_and_failure():
    ok = client.post("/api/admin/login", json={"username": "officer", "password": "rsk2024"})
    assert ok.status_code == 200 and ok.json()["ok"] is True and ok.json()["token"]
    bad = client.post("/api/admin/login", json={"username": "officer", "password": "wrong"})
    assert bad.status_code == 401


# --- photo storage on diagnose ---------------------------------------------------

def _tomato_farmer():
    return next(f for f in DEMO_FARMERS if f.id == "ramesh")


def test_diagnose_stores_downscaled_photo(monkeypatch):
    captured = {}
    monkeypatch.setattr(firestore_client, "get_farmer", lambda fid: _tomato_farmer())
    monkeypatch.setattr(firestore_client, "get_crop", lambda cid: None)
    monkeypatch.setattr(firestore_client, "create_case", lambda case: captured.setdefault("case", case) or "c1")
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")
    monkeypatch.setattr(main, "diagnose_image", lambda *a, **k: (_ for _ in ()).throw(VisionError("x")))
    monkeypatch.setattr(main, "explain_fusion", lambda *a, **k: (_ for _ in ()).throw(ExplainError("x")))

    resp = client.post(
        "/api/diagnose",
        data={"farmer_id": "ramesh"},
        files={"image": ("leaf.jpg", b"a-small-photo", "image/jpeg")},
    )
    assert resp.status_code == 200
    assert captured["case"].image_data.startswith("data:image/jpeg;base64,")


def test_diagnose_skips_oversized_photo(monkeypatch):
    captured = {}
    monkeypatch.setattr(firestore_client, "get_farmer", lambda fid: _tomato_farmer())
    monkeypatch.setattr(firestore_client, "get_crop", lambda cid: None)
    monkeypatch.setattr(firestore_client, "create_case", lambda case: captured.setdefault("case", case) or "c1")
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")
    monkeypatch.setattr(main, "diagnose_image", lambda *a, **k: (_ for _ in ()).throw(VisionError("x")))
    monkeypatch.setattr(main, "explain_fusion", lambda *a, **k: (_ for _ in ()).throw(ExplainError("x")))

    big = b"x" * (main._MAX_STORED_IMAGE_BYTES + 1)
    client.post("/api/diagnose", data={"farmer_id": "ramesh"}, files={"image": ("leaf.jpg", big, "image/jpeg")})
    assert captured["case"].image_data is None  # too large -> not stored, diagnosis still works


# --- two categories: AI needs review + Farmer disputed ---------------------------

def _fusion(top, decision):
    return FusionOutput(
        posterior=[
            PosteriorEntry(condition="late_blight", score=0.45, contagious=True),
            PosteriorEntry(condition="early_blight", score=0.35, contagious=True),
            PosteriorEntry(condition="nitrogen_deficiency", score=0.20, contagious=False),
        ],
        top=top, confidence=0.45, margin=0.10, conflict=False, decision=decision,
        alert_eligible=True,
        evidence=FusionEvidence(vision_top=top, prior_top="early_blight", context_completeness=[]),
    )


def _vision():
    return VisionOutput(
        image_quality="good", crop_confirmed="tomato", identified_crop="tomato", matches_profile=True,
        candidates=[
            VisionCandidate(condition="late_blight", confidence=0.45, visible_symptoms=["dark lesions"]),
            VisionCandidate(condition="early_blight", confidence=0.35, visible_symptoms=[]),
        ],
    )


def _case(cid, farmer_id, status):
    ramesh = _tomato_farmer()
    ctx = FusionContext(
        crop="tomato",
        location=ContextLocation(lat=ramesh.location.lat, lng=ramesh.location.lng, resolution="village"),
        weather=Weather(temp_c=19.0, humidity_pct=92.0, rain_48h_mm=12.0, source="cache"),
        soil=Soil(nitrogen="unknown", source="unknown"),
    )
    return Case(
        id=cid, farmer_id=farmer_id, image_note="spots on leaves",
        image_data="data:image/jpeg;base64,AAAA",
        vision=_vision(), context=ctx, fusion=_fusion("late_blight", "escalate_rsk"),
        status=status, condition="late_blight", contagious=True,
    )


@pytest.fixture
def fake_store(monkeypatch):
    cases = {
        "review-1": _case("review-1", "ramesh", "escalated"),
        "disputed-1": _case("disputed-1", "venkat", "disputed"),
    }
    alerts: dict = {}

    monkeypatch.setattr(firestore_client, "get_case", lambda cid: cases.get(cid))
    monkeypatch.setattr(
        firestore_client, "list_cases_by_status",
        lambda statuses: [c for c in cases.values() if c.status in statuses],
    )
    monkeypatch.setattr(firestore_client, "list_farmers", lambda: DEMO_FARMERS)

    def update_case(cid, updates):
        for k, v in updates.items():
            setattr(cases[cid], k, v)

    def create_alert(alert):
        alert.id = "alert-%d" % (len(alerts) + 1)
        alerts[alert.id] = alert
        return alert.id

    monkeypatch.setattr(firestore_client, "update_case", update_case)
    monkeypatch.setattr(firestore_client, "create_alert", create_alert)
    monkeypatch.setattr(firestore_client, "list_recent_alerts", lambda limit=50: list(alerts.values()))
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: "t")
    return {"cases": cases, "alerts": alerts}


def test_cases_expose_both_categories_with_photo_and_candidates(fake_store):
    rows = client.get("/api/cases").json()["cases"]
    by_status = {r["status"]: r for r in rows}
    assert "escalated" in by_status and "disputed" in by_status
    review = by_status["escalated"]
    assert review["photo"].startswith("data:image")  # photo shown in the portal
    assert [c["condition"] for c in review["candidates"]]  # ranked candidates present
    assert review["visible_symptoms"]


def test_case_flags_photo_not_analyzed_when_vision_missing(fake_store, monkeypatch):
    # A case whose vision is None (Gemini couldn't read the photo) must be flagged
    # so the portal doesn't present the prior-only top as a confident AI diagnosis.
    no_vision = _case("review-1", "ramesh", "escalated")
    no_vision.vision = None
    monkeypatch.setattr(
        firestore_client, "list_cases_by_status",
        lambda statuses: [no_vision] if "escalated" in statuses else [],
    )
    rows = client.get("/api/cases").json()["cases"]
    review = next(r for r in rows if r["status"] == "escalated")
    assert review["photo_analyzed"] is False
    assert review["candidates"] == []


def test_confirm_review_case_fires_alert_to_lakshmi(fake_store):
    resp = client.post("/api/confirm", json={"case_id": "review-1", "officer_verdict": "late_blight"})
    data = resp.json()
    assert data["status"] == "confirmed" and data["condition"] == "late_blight"
    assert data["alert"] is not None
    recipient_ids = [r["id"] for r in data["alert"]["recipients"]]
    assert "lakshmi" in recipient_ids       # tomato, ~3 km -> alerted
    assert "venkat" not in recipient_ids    # rice -> not alerted
    assert "sita" not in recipient_ids      # out of radius -> not alerted
