from engine.alert_engine import propagation
from models import Case, ContextLocation, FusionContext, NearbyConfirmed, Soil, Weather
from seed import DEMO_FARMERS


def _tomato_context(lat: float, lng: float, nearby_confirmed=None) -> FusionContext:
    return FusionContext(
        crop="tomato",
        location=ContextLocation(lat=lat, lng=lng, resolution="village"),
        weather=Weather(temp_c=28.0, humidity_pct=70.0, rain_48h_mm=0.0, source="zone_normal"),
        soil=Soil(nitrogen="unknown", source="unknown"),
        nearby_confirmed=nearby_confirmed or [],
    )


def _ramesh_location() -> tuple[float, float]:
    ramesh = next(f for f in DEMO_FARMERS if f.id == "ramesh")
    return ramesh.location.lat, ramesh.location.lng


def test_propagation_selects_only_in_range_same_crop_farmers():
    """PROJECT_SPEC.md demo scenario: a confirmed late_blight case at Farmer A
    (Ramesh) should reach Farmer B (Lakshmi, ~3km, tomato) but not Farmer C
    (Venkat, ~2km, rice -- wrong crop) or Farmer D (Sita, ~15km -- too far).
    """
    lat, lng = _ramesh_location()
    case = Case(
        id="case-ramesh-1",
        farmer_id="ramesh",
        status="confirmed",  # passes the gate via RSK officer confirmation
        condition="late_blight",
        contagious=True,
        context=_tomato_context(lat, lng),
    )

    alert = propagation(case, DEMO_FARMERS)

    assert alert is not None
    assert alert.condition == "late_blight"
    assert alert.recipient_ids == ["lakshmi"]
    assert "venkat" not in alert.recipient_ids  # wrong crop (rice)
    assert "sita" not in alert.recipient_ids  # out of radius (~15km)
    assert "ramesh" not in alert.recipient_ids  # source farmer, not a recipient


def test_propagation_blocked_without_confirmation_gate():
    """Not officer-confirmed, no corroborating reports, no strong environmental
    signal -- the confirmation gate must block the area alert entirely.
    """
    lat, lng = _ramesh_location()
    case = Case(
        id="case-ramesh-2",
        farmer_id="ramesh",
        status="pending",
        condition="late_blight",
        contagious=True,
        context=_tomato_context(lat, lng),
    )

    assert propagation(case, DEMO_FARMERS) is None


def test_propagation_auto_elevates_with_three_corroborating_reports():
    """N>=3 independent nearby reports of the same condition auto-elevate the
    gate even without an officer confirmation yet.
    """
    lat, lng = _ramesh_location()
    case = Case(
        id="case-ramesh-3",
        farmer_id="ramesh",
        status="pending",
        condition="late_blight",
        contagious=True,
        context=_tomato_context(
            lat,
            lng,
            nearby_confirmed=[
                NearbyConfirmed(condition="late_blight", distance_km=1.0, age_days=1),
                NearbyConfirmed(condition="late_blight", distance_km=2.0, age_days=2),
                NearbyConfirmed(condition="late_blight", distance_km=1.5, age_days=1),
            ],
        ),
    )

    alert = propagation(case, DEMO_FARMERS)

    assert alert is not None
    assert alert.recipient_ids == ["lakshmi"]
