"""The farm assistant: POST /assistant (intent classifier -> handlers).

Firestore/Gemini/mandi/weather are faked so these run offline. Covers each
intent's happy path, each handler's graceful degradation, the off-topic
refusal, and the classifier's own keyword-heuristic fallback.
"""
import pytest
from fastapi.testclient import TestClient

import assistant as assistant_mod
import firestore_client
import geocode
import main
import mandi
import weather as weather_source
from models import (
    AssistantIntent,
    Crop,
    CropNames,
    CurrentCrop,
    Farmer,
    FarmerLocation,
    Forecast,
    GrowthStage,
)

client = TestClient(main.app)

TOMATO = Crop(
    id="tomato", names=CropNames(en="Tomato", hi="टमाटर", te="టమాటా"),
    seasons=["kharif"], soil_types=["black"], water_need="medium", regions=["coastal"],
    cycle_days=100, growth_stages=[GrowthStage(name="veg", start_day=0, care_note="x")],
    susceptible_diseases=["late_blight"],
)
BLACK_GRAM = Crop(
    id="black_gram", names=CropNames(en="Black Gram", hi="उड़द", te="మినుము"),
    seasons=["kharif"], soil_types=["black"], water_need="low", regions=["coastal"],
    cycle_days=70, growth_stages=[], susceptible_diseases=[],
)


def _ramesh():
    return Farmer(
        id="ramesh", name="Ramesh", phone="9876500001", lang="en",
        location=FarmerLocation(lat=16.3067, lng=80.4365, mandal="Guntur"),
        crop="tomato", land_size_acres=2.0, growth_stage="flowering", soil_type="black",
        current_crops=[CurrentCrop(crop_id="tomato", planting_date="2026-05-01")],
    )


@pytest.fixture
def store(monkeypatch):
    telemetry = []
    monkeypatch.setattr(firestore_client, "get_farmer", lambda fid: _ramesh() if fid == "ramesh" else None)
    monkeypatch.setattr(firestore_client, "list_crops", lambda: [TOMATO, BLACK_GRAM])
    monkeypatch.setattr(firestore_client, "get_crop", lambda cid: {"tomato": TOMATO, "black_gram": BLACK_GRAM}.get(cid))
    monkeypatch.setattr(firestore_client, "log_telemetry", lambda e: telemetry.append(e) or "t")
    # Reverse-geocoding hits the network by default; keep tests offline unless a
    # test overrides it to exercise the mandi location-resolution path.
    monkeypatch.setattr(geocode, "farmer_admin_area", lambda lat, lng: (None, None))
    return telemetry


def _ask(text, lang="en", farmer_id="ramesh"):
    return client.post("/assistant", json={"text": text, "lang": lang, "farmer_id": farmer_id})


def _events(telemetry):
    return {t.event for t in telemetry}


# --- crop_recommendation -----------------------------------------------------

def test_crop_recommendation_reuses_existing_engine(store, monkeypatch):
    monkeypatch.setattr(
        assistant_mod, "classify_intent",
        lambda text, lang: AssistantIntent(intent="crop_recommendation", on_topic=True, lang=lang),
    )
    monkeypatch.setattr(
        main, "recommend_conversational",
        lambda question, profile, grounding, language: {
            "recommendations": [{"crop_id": "black_gram", "crop_name": "Black Gram",
                                  "why": "A nitrogen-fixing rotation after tomato."}],
            "spoken": "Try black gram next after tomatoes.",
        },
    )
    resp = _ask("What should I grow after tomato?")
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "crop_recommendation"
    assert "black gram" in body["answer_text"].lower()
    assert body["data"]["recommendations"][0]["crop_id"] == "black_gram"


# --- weather_advice ------------------------------------------------------------

def test_weather_advice_dry_spell(store, monkeypatch):
    monkeypatch.setattr(
        assistant_mod, "classify_intent",
        lambda text, lang: AssistantIntent(intent="weather_advice", on_topic=True, lang=lang),
    )
    monkeypatch.setattr(
        weather_source, "get_forecast",
        lambda lat, lng: Forecast(precip_next7_mm=1.0, rain_prob_max_pct=10.0, dry_spell=True, source="live"),
    )
    resp = _ask("Will it rain this week?")
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "weather_advice"
    assert "irrigate" in body["answer_text"].lower()


def test_weather_advice_failure_degrades_silently(store, monkeypatch):
    monkeypatch.setattr(
        assistant_mod, "classify_intent",
        lambda text, lang: AssistantIntent(intent="weather_advice", on_topic=True, lang=lang),
    )
    monkeypatch.setattr(
        weather_source, "get_forecast",
        lambda *a, **k: (_ for _ in ()).throw(weather_source.WeatherError("timeout")),
    )
    resp = _ask("Will it rain this week?")
    assert resp.status_code == 200
    assert resp.json()["answer_text"]  # a real, friendly message -- never a raw error


# --- mandi_price ---------------------------------------------------------------

def test_mandi_price_real_rate(store, monkeypatch):
    monkeypatch.setattr(
        assistant_mod, "classify_intent",
        lambda text, lang: AssistantIntent(
            intent="mandi_price", on_topic=True, commodity="tomato", location="Guntur", lang=lang
        ),
    )
    monkeypatch.setattr(
        mandi, "get_price",
        lambda commodity, state=None, district=None, market=None: {
            "commodity": "Tomato", "variety": "Local", "market": "Guntur", "district": "Guntur",
            "state": "Andhra Pradesh", "arrival_date": "08/07/2026",
            "modal_price_per_quintal": 1800.0, "min_price_per_quintal": 1500.0, "max_price_per_quintal": 2000.0,
        },
    )
    resp = _ask("Today's tomato price in Guntur")
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "mandi_price"
    assert "1800" in body["answer_text"] and "Guntur" in body["answer_text"]
    assert body["data"]["modal_price_per_quintal"] == 1800.0


def test_mandi_price_no_rate_available(store, monkeypatch):
    monkeypatch.setattr(
        assistant_mod, "classify_intent",
        lambda text, lang: AssistantIntent(intent="mandi_price", on_topic=True, commodity="tomato", lang=lang),
    )
    monkeypatch.setattr(mandi, "get_price", lambda *a, **k: None)
    resp = _ask("Today's tomato price")
    assert resp.status_code == 200
    body = resp.json()
    assert "no rate" in body["answer_text"].lower()
    assert "1800" not in body["answer_text"]  # never a fabricated number


def test_mandi_price_hard_failure_logs_fallback(store, monkeypatch):
    monkeypatch.setattr(
        assistant_mod, "classify_intent",
        lambda text, lang: AssistantIntent(intent="mandi_price", on_topic=True, commodity="tomato", lang=lang),
    )
    monkeypatch.setattr(mandi, "get_price", lambda *a, **k: (_ for _ in ()).throw(mandi.MandiError("bad key")))
    resp = _ask("Today's tomato price")
    assert resp.status_code == 200
    assert "no rate" in resp.json()["answer_text"].lower()
    fallback_events = [t for t in store if t.layer == "mandi" and t.fallback_used]
    assert fallback_events


# --- fertilizer_advice -----------------------------------------------------------

def test_fertilizer_advice_is_guardrailed(store, monkeypatch):
    monkeypatch.setattr(
        assistant_mod, "classify_intent",
        lambda text, lang: AssistantIntent(intent="fertilizer_advice", on_topic=True, crop="wheat", lang=lang),
    )
    monkeypatch.setattr(
        assistant_mod, "fertilizer_advice",
        lambda question, profile, lang: {
            "answer_text": "Get a soil test first. Balance nitrogen with the crop stage; "
                            "confirm exact dose with your RSK officer.",
        },
    )
    resp = _ask("Which fertiliser for my wheat?")
    assert resp.status_code == 200
    body = resp.json()
    blob = body["answer_text"].lower()
    assert "soil test" in blob and "rsk" in blob
    # never a precise dose/product
    assert "kg/acre" not in blob and "ml/l" not in blob


def test_fertilizer_advice_falls_back_deterministically(store, monkeypatch):
    monkeypatch.setattr(
        assistant_mod, "classify_intent",
        lambda text, lang: AssistantIntent(intent="fertilizer_advice", on_topic=True, crop="wheat", lang=lang),
    )
    monkeypatch.setattr(
        assistant_mod, "fertilizer_advice",
        lambda *a, **k: (_ for _ in ()).throw(assistant_mod.FertilizerAdviceError("down")),
    )
    resp = _ask("Which fertiliser for my wheat?")
    assert resp.status_code == 200
    blob = resp.json()["answer_text"].lower()
    assert "soil test" in blob or "soil health card" in blob


# --- general_farming_qa -----------------------------------------------------------

def test_general_farming_qa_grounded_with_citation(store, monkeypatch):
    monkeypatch.setattr(
        assistant_mod, "classify_intent",
        lambda text, lang: AssistantIntent(intent="general_farming_qa", on_topic=True, crop="rice", lang=lang),
    )
    monkeypatch.setattr(
        assistant_mod, "general_farming_qa",
        lambda question, lang: {
            "answer_text": "Use IPM: pheromone traps and resistant varieties first for stem borer in rice.",
            "citations": [{"title": "ICAR stem borer guide", "uri": "https://icar.gov.in/stem-borer"}],
        },
    )
    resp = _ask("How do I control stem borer in rice?")
    assert resp.status_code == 200
    body = resp.json()
    assert "ipm" in body["answer_text"].lower()
    assert body["citations"] and body["citations"][0]["uri"].startswith("https://")


def test_general_farming_qa_degrades_when_gemini_down(store, monkeypatch):
    monkeypatch.setattr(
        assistant_mod, "classify_intent",
        lambda text, lang: AssistantIntent(intent="general_farming_qa", on_topic=True, lang=lang),
    )
    monkeypatch.setattr(
        assistant_mod, "general_farming_qa",
        lambda *a, **k: (_ for _ in ()).throw(assistant_mod.FarmingQAError("down")),
    )
    resp = _ask("How do I control stem borer in rice?")
    assert resp.status_code == 200
    assert resp.json()["answer_text"]
    assert resp.json()["citations"] == []


# --- off-topic refusal -----------------------------------------------------------

@pytest.mark.parametrize("text", ["Who won the cricket match?", "Write me a poem"])
def test_off_topic_is_refused_gracefully(store, monkeypatch, text):
    monkeypatch.setattr(
        assistant_mod, "classify_intent",
        lambda t, lang: AssistantIntent(intent="off_topic", on_topic=False, lang=lang),
    )
    resp = _ask(text)
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "off_topic"
    assert "farming" in body["answer_text"].lower()


# --- classifier's own graceful degradation ----------------------------------------

def test_classify_failure_falls_back_to_keyword_heuristic(store, monkeypatch):
    monkeypatch.setattr(
        assistant_mod, "classify_intent",
        lambda *a, **k: (_ for _ in ()).throw(assistant_mod.IntentClassifyError("Gemini down")),
    )
    monkeypatch.setattr(
        mandi, "get_price",
        lambda commodity, state=None, district=None, market=None: {
            "commodity": "Tomato", "market": "Guntur", "arrival_date": "08/07/2026",
            "modal_price_per_quintal": 1800.0,
        },
    )
    resp = _ask("What is the mandi rate for tomato?")
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "mandi_price"  # keyword heuristic caught "mandi rate"

    fallback_events = [t for t in store if t.event == "assistant_classify_fallback"]
    assert fallback_events and fallback_events[0].fallback_used is True


def test_empty_text_is_refused_without_calling_gemini(store, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("classify_intent should not be called for empty text")
    monkeypatch.setattr(assistant_mod, "classify_intent", _boom)
    resp = _ask("")
    assert resp.status_code == 200
    assert resp.json()["intent"] == "off_topic"
