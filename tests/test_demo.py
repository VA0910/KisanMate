"""Tests for the deterministic demo runner and the telemetry log endpoint.

Firestore is faked in memory (monkeypatch), so this proves the whole hero
scenario wires up -- recommendation, escalated late-blight diagnosis, RSK
confirmation, and an alert that reaches Farmer B (Lakshmi) but not C (Venkat) --
plus that the run emits at least one fallback telemetry entry for /log.
"""
import pytest
from fastapi.testclient import TestClient

import demo
import firestore_client
import main
from models import Alert
from seed import DEMO_FARMERS

client = TestClient(main.app)


@pytest.fixture
def fake_store(monkeypatch):
    cases: dict = {}
    alerts: dict = {}
    telemetry: list = []
    seq = {"case": 0, "alert": 0}

    def upsert_farmer(farmer):
        return farmer.id

    def create_case(case):
        seq["case"] += 1
        case_id = "case-%d" % seq["case"]
        case.id = case_id
        cases[case_id] = case
        return case_id

    def get_case(case_id):
        return cases.get(case_id)

    def update_case(case_id, updates):
        case = cases[case_id]
        for key, value in updates.items():
            setattr(case, key, value)

    def list_farmers():
        return DEMO_FARMERS

    def create_alert(alert):
        seq["alert"] += 1
        alert_id = "alert-%d" % seq["alert"]
        alert.id = alert_id
        alerts[alert_id] = alert
        return alert_id

    def list_alerts_for_farmer(farmer_id):
        return [a for a in alerts.values() if farmer_id in a.recipient_ids]

    def list_recent_alerts(limit=50):
        return list(alerts.values())

    def log_telemetry(entry):
        telemetry.append(entry)
        return "telemetry-%d" % len(telemetry)

    def list_recent_telemetry(limit=50):
        return list(reversed(telemetry))[:limit]

    for name, fn in {
        "upsert_farmer": upsert_farmer,
        "create_case": create_case,
        "get_case": get_case,
        "update_case": update_case,
        "list_farmers": list_farmers,
        "create_alert": create_alert,
        "list_alerts_for_farmer": list_alerts_for_farmer,
        "list_recent_alerts": list_recent_alerts,
        "log_telemetry": log_telemetry,
        "list_recent_telemetry": list_recent_telemetry,
    }.items():
        monkeypatch.setattr(firestore_client, name, fn)

    return {"cases": cases, "alerts": alerts, "telemetry": telemetry}


def test_demo_runs_full_hero_story(fake_store):
    result = demo.run_demo_scenario(lang="en")

    # (a) data-grounded recommendation: tomato is Ramesh's best match
    assert result["recommend"]["recommendations"][0]["crop"] == "tomato"

    # (b) diagnosis fuses to late blight and escalates (not a confident "advise")
    diag = result["diagnose"]
    assert diag["condition"] == "late_blight"
    assert diag["fusion"]["top"] == "late_blight"
    assert diag["fusion"]["decision"] == "escalate_rsk"
    assert diag["fusion"]["alert_eligible"] is True
    assert diag["message"]  # a non-empty template message

    # (c) + (d) confirmation fires an alert to Farmer B only
    alert = result["confirm"]["alert"]
    assert alert is not None
    assert [r["id"] for r in alert["recipients"]] == ["lakshmi"]

    assert len(result["alerts"]["lakshmi"]["alerts"]) == 1
    assert result["alerts"]["lakshmi"]["lang"] == "te"  # Telugu, per the spec
    assert result["alerts"]["venkat"]["alerts"] == []  # rice farmer: nothing
    assert result["alerts"]["venkat"]["crop"] == "rice"


def test_demo_emits_fallback_telemetry(fake_store):
    demo.run_demo_scenario(lang="en")
    telemetry = fake_store["telemetry"]

    assert telemetry, "demo should log telemetry"
    fallbacks = [t for t in telemetry if t.fallback_used]
    assert len(fallbacks) >= 1
    events = {t.event for t in telemetry}
    # the story's key beats are all logged
    assert {"crop_recommendation", "diagnosis_fused", "rsk_confirmed", "community_alert_fired"} <= events


def test_demo_diagnosis_message_localizes(fake_store):
    result_te = demo.run_demo_scenario(lang="te")
    result_en = demo.run_demo_scenario(lang="en")
    # different language templates => different message text
    assert result_te["diagnose"]["message"] != result_en["diagnose"]["message"]


def test_telemetry_endpoint_returns_entries(fake_store):
    demo.run_demo_scenario(lang="en")
    resp = client.get("/api/telemetry")
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert entries
    assert any(e["fallback_used"] for e in entries)
    assert {"event", "layer", "fallback_used", "created_at"} <= set(entries[0].keys())


def test_demo_run_endpoint(fake_store):
    resp = client.post("/api/demo/run", json={"lang": "en"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["diagnose"]["condition"] == "late_blight"
    assert [r["id"] for r in data["confirm"]["alert"]["recipients"]] == ["lakshmi"]
