"""End-to-end tests for the RSK officer flow (/api/cases, /api/confirm, /api/alerts).

Firestore is replaced with an in-memory fake via monkeypatch, so these run
without any GCP credentials while still exercising the real endpoint + alert
engine wiring. This is the PROJECT_SPEC.md acceptance scenario: confirm Ramesh's
late_blight case and check the alert reaches Farmer B (Lakshmi) but not C or D.
"""
import pytest
from fastapi.testclient import TestClient

import firestore_client
import main
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

client = TestClient(main.app)


def _ramesh_escalated_case() -> Case:
    ramesh = next(f for f in DEMO_FARMERS if f.id == "ramesh")
    context = FusionContext(
        crop="tomato",
        location=ContextLocation(lat=ramesh.location.lat, lng=ramesh.location.lng, resolution="village"),
        weather=Weather(temp_c=28.0, humidity_pct=70.0, rain_48h_mm=0.0, source="zone_normal"),
        soil=Soil(nitrogen="unknown", source="unknown"),
        nearby_confirmed=[],
    )
    fusion = FusionOutput(
        posterior=[
            PosteriorEntry(condition="late_blight", score=0.45, contagious=True),
            PosteriorEntry(condition="early_blight", score=0.35, contagious=True),
            PosteriorEntry(condition="nitrogen_deficiency", score=0.20, contagious=False),
        ],
        top="late_blight",
        confidence=0.45,
        margin=0.10,
        conflict=False,
        decision="escalate_rsk",
        alert_eligible=True,
        evidence=FusionEvidence(vision_top="late_blight", prior_top="early_blight", context_completeness=[]),
    )
    vision = VisionOutput(
        image_quality="good",
        crop_confirmed="tomato",
        candidates=[
            VisionCandidate(
                condition="late_blight",
                confidence=0.45,
                visible_symptoms=["dark water-soaked lesions", "white mold on underside"],
            )
        ],
        notes="",
    )
    return Case(
        id="case-1",
        farmer_id="ramesh",
        image_note="dark spots spreading on lower leaves",
        vision=vision,
        context=context,
        fusion=fusion,
        status="escalated",
        condition="late_blight",
        contagious=True,
    )


@pytest.fixture
def fake_store(monkeypatch):
    cases: dict[str, Case] = {"case-1": _ramesh_escalated_case()}
    alerts: dict = {}

    def get_case(case_id):
        return cases.get(case_id)

    def update_case(case_id, updates):
        case = cases[case_id]
        for key, value in updates.items():
            setattr(case, key, value)

    def list_cases_by_status(statuses):
        return [c for c in cases.values() if c.status in statuses]

    def list_cases_by_farmer(farmer_id):
        return [c for c in cases.values() if c.farmer_id == farmer_id]

    def list_farmers():
        return DEMO_FARMERS

    def create_alert(alert):
        alert_id = "alert-%d" % (len(alerts) + 1)
        alert.id = alert_id
        alerts[alert_id] = alert
        return alert_id

    def list_recent_alerts(limit=50):
        return list(alerts.values())

    def log_telemetry(entry):
        return "telemetry-noop"

    monkeypatch.setattr(firestore_client, "get_case", get_case)
    monkeypatch.setattr(firestore_client, "update_case", update_case)
    monkeypatch.setattr(firestore_client, "list_cases_by_status", list_cases_by_status)
    monkeypatch.setattr(firestore_client, "list_cases_by_farmer", list_cases_by_farmer)
    monkeypatch.setattr(firestore_client, "list_farmers", list_farmers)
    monkeypatch.setattr(firestore_client, "create_alert", create_alert)
    monkeypatch.setattr(firestore_client, "list_recent_alerts", list_recent_alerts)
    monkeypatch.setattr(firestore_client, "log_telemetry", log_telemetry)
    return {"cases": cases, "alerts": alerts}


def test_cases_lists_escalated_case_with_ai_details(fake_store):
    resp = client.get("/api/cases")
    assert resp.status_code == 200
    rows = resp.json()["cases"]
    assert len(rows) == 1
    row = rows[0]
    assert row["farmer_name"] == "Ramesh"
    assert row["crop"] == "tomato"
    assert row["mandal"] == "Guntur"
    assert row["ai_top_condition"] == "late_blight"
    assert row["image_note"] == "dark spots spreading on lower leaves"
    assert "dark water-soaked lesions" in row["visible_symptoms"]


def test_confirm_late_blight_fires_alert_to_farmer_b_only(fake_store):
    resp = client.post("/api/confirm", json={"case_id": "case-1", "officer_verdict": "late_blight"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "confirmed"
    assert data["condition"] == "late_blight"
    assert data["contagious"] is True

    alert = data["alert"]
    assert alert is not None
    assert alert["recipient_count"] == 1
    recipient_ids = [r["id"] for r in alert["recipients"]]
    assert recipient_ids == ["lakshmi"]  # Farmer B
    assert "venkat" not in recipient_ids  # Farmer C, wrong crop
    assert "sita" not in recipient_ids  # Farmer D, out of radius

    # the case is now confirmed and drops out of the review queue
    assert fake_store["cases"]["case-1"].status == "confirmed"
    assert fake_store["cases"]["case-1"].officer_verdict == "late_blight"
    assert client.get("/api/cases").json()["cases"] == []

    # and it shows up in the officer's fired-alerts list, naming Lakshmi
    alerts = client.get("/api/alerts").json()["alerts"]
    assert len(alerts) == 1
    assert alerts[0]["tier"] in ("warning", "alert")
    assert [r["name"] for r in alerts[0]["recipients"]] == ["Lakshmi"]


def test_override_to_noncontagious_condition_fires_no_alert(fake_store):
    resp = client.post("/api/confirm", json={"case_id": "case-1", "officer_verdict": "nitrogen_deficiency"})
    data = resp.json()

    # officer's verdict overrides the AI's late_blight
    assert data["condition"] == "nitrogen_deficiency"
    assert data["contagious"] is False
    assert data["alert"] is None
    assert fake_store["cases"]["case-1"].condition == "nitrogen_deficiency"
    assert fake_store["alerts"] == {}


def test_confirm_missing_case_returns_404(fake_store):
    resp = client.post("/api/confirm", json={"case_id": "nope", "officer_verdict": "late_blight"})
    assert resp.status_code == 404


def test_verdict_notification_fires_once_then_clears(fake_store):
    # Before any officer verdict: the escalated case is not a notification.
    assert client.get("/api/farmers/ramesh/notifications").json()["notifications"] == []

    # Officer confirms -> the verdict becomes an unseen notification for the farmer.
    client.post("/api/confirm", json={"case_id": "case-1", "officer_verdict": "late_blight"})
    notifs = client.get("/api/farmers/ramesh/notifications").json()["notifications"]
    assert len(notifs) == 1
    assert notifs[0]["case_id"] == "case-1"
    assert notifs[0]["condition"] == "late_blight"
    assert notifs[0]["officer_reviewed"] is True

    # Farmer sees the popup and acknowledges -> it must never fire again.
    client.post("/api/cases/case-1/verdict-seen")
    assert fake_store["cases"]["case-1"].verdict_seen is True
    assert client.get("/api/farmers/ramesh/notifications").json()["notifications"] == []
    # ...but it stays visible in My reports, now marked officer-reviewed.
    reports = client.get("/api/farmers/ramesh/cases").json()["cases"]
    assert reports[0]["officer_reviewed"] is True
    assert reports[0]["condition"] == "late_blight"
