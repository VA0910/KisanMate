"""Cycle-based reminders: deterministic date math + the /api/reminders endpoint.

Proves PROJECT_SPEC.md's proactive "memory": from a crop's planting_date and the
crop DB, reminders are computed with no user action, and changing the planting
date changes which reminders appear.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import firestore_client
import main
from engine.reminders import reminders_for_crop, reminders_for_farmer
from models import (
    Crop,
    CropNames,
    CurrentCrop,
    Farmer,
    FarmerLocation,
    GrowthStage,
)

client = TestClient(main.app)

TOMATO = Crop(
    id="tomato",
    names=CropNames(en="Tomato", hi="टमाटर", te="టమాటా"),
    seasons=["kharif"],
    soil_types=["black"],
    water_need="medium",  # -> 5-day irrigation cadence
    regions=["coastal"],
    cycle_days=120,
    growth_stages=[
        GrowthStage(name="nursery", start_day=0, care_note="Keep the bed moist."),
        GrowthStage(name="flowering", start_day=55, care_note="Keep moisture steady."),
        GrowthStage(name="harvest", start_day=100, care_note="Pick ripe fruit."),
    ],
    susceptible_diseases=["late_blight"],
)


def _planted(days_ago):
    return (date.today() - timedelta(days=days_ago)).isoformat()


def test_irrigation_due_today_on_cadence_multiple():
    # 60 days, medium cadence 5 -> 60 % 5 == 0 -> due today.
    rems = reminders_for_crop(TOMATO, _planted(60))
    irrigation = [r for r in rems if r["type"] == "irrigation"]
    assert irrigation and irrigation[0]["days_until"] == 0


def test_changing_planting_date_changes_reminders():
    # Planted on a cadence multiple -> irrigation due today.
    due = reminders_for_crop(TOMATO, _planted(60))
    assert due[0]["type"] == "irrigation" and due[0]["days_until"] == 0
    # Planted today -> next irrigation is a full cadence (5 days) away.
    fresh = reminders_for_crop(TOMATO, _planted(0))
    irrigation = [r for r in fresh if r["type"] == "irrigation"][0]
    assert irrigation["days_until"] == 5


def test_harvest_reminder_appears_near_maturity():
    rems = reminders_for_crop(TOMATO, _planted(TOMATO.cycle_days - 5))
    harvest = [r for r in rems if r["type"] == "harvest"]
    assert harvest and harvest[0]["days_until"] == 5


def test_stage_care_only_right_after_entering_stage():
    # Day 55 is exactly when flowering starts -> its care note surfaces.
    entered = reminders_for_crop(TOMATO, _planted(55))
    assert any(r["type"] == "stage_care" and r["stage"] == "flowering" for r in entered)
    # Day 60 is well into flowering -> the stage-care note no longer surfaces.
    later = reminders_for_crop(TOMATO, _planted(60))
    assert not any(r["type"] == "stage_care" for r in later)


def test_no_reminders_without_crop_doc():
    assert reminders_for_crop(None, _planted(60)) == []


def test_reminders_for_farmer_sorts_and_uses_lookup():
    farmer = Farmer(
        id="ramesh", name="Ramesh", phone="9876500001", lang="en",
        location=FarmerLocation(lat=16.3, lng=80.4, mandal="Guntur"),
        crop="tomato", land_size_acres=2.0, growth_stage="flowering",
        current_crops=[CurrentCrop(crop_id="tomato", planting_date=_planted(60))],
    )
    rems = reminders_for_farmer(farmer, lambda cid: TOMATO if cid == "tomato" else None)
    assert rems and rems[0]["type"] == "irrigation" and rems[0]["days_until"] == 0


# --- endpoint --------------------------------------------------------------------

def _seeded_farmer():
    return Farmer(
        id="ramesh", name="Ramesh", phone="9876500001", lang="en",
        location=FarmerLocation(lat=16.3, lng=80.4, mandal="Guntur"),
        crop="tomato", land_size_acres=2.0, growth_stage="flowering",
        soil_type="black",
        current_crops=[CurrentCrop(crop_id="tomato", planting_date=_planted(60))],
    )


def test_reminders_endpoint_returns_due_today(monkeypatch):
    monkeypatch.setattr(firestore_client, "get_farmer", lambda fid: _seeded_farmer())
    monkeypatch.setattr(firestore_client, "get_crop", lambda cid: TOMATO)
    resp = client.get("/api/reminders/ramesh")
    assert resp.status_code == 200
    reminders = resp.json()["reminders"]
    assert any(r["type"] == "irrigation" and r["days_until"] == 0 for r in reminders)


def test_reminders_endpoint_404_for_unknown_farmer(monkeypatch):
    monkeypatch.setattr(firestore_client, "get_farmer", lambda fid: None)
    assert client.get("/api/reminders/nobody").status_code == 404
