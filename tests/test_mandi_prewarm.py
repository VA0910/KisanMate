"""mandi_prewarm.py: the daily mandi-cache pre-warm job.

Covers the two things that matter for "cache districts around real farmers,
not the whole country": the job is scoped to farmers' own registered crops
and resolved districts, crop ids get mapped to Agmarknet's display-name
commodities (not the raw underscored id), and one bad pair never aborts the
whole run.
"""
import firestore_client
import geocode
import mandi
import mandi_prewarm
from models import Crop, CropNames, CurrentCrop, Farmer, FarmerLocation

TOMATO = Crop(id="tomato", names=CropNames(en="Tomato", hi="टमाटर", te="టమాటా"),
              water_need="medium", cycle_days=100)
BLACK_GRAM = Crop(id="black_gram", names=CropNames(en="Black Gram", hi="उड़द", te="మినుము"),
                   water_need="low", cycle_days=70)


def _farmer(fid, crop, current_crop_ids=(), lat=16.3, lng=80.4):
    return Farmer(
        id=fid, name=fid, phone="9876500001", lang="en",
        location=FarmerLocation(lat=lat, lng=lng, mandal="Guntur"),
        crop=crop, land_size_acres=1.0, growth_stage="veg",
        current_crops=[CurrentCrop(crop_id=cid, planting_date="2026-05-01") for cid in current_crop_ids],
    )


def test_farmer_commodities_maps_crop_id_to_display_name():
    crop_names = {"tomato": "Tomato", "black_gram": "Black Gram"}
    farmer = _farmer("f1", crop="tomato", current_crop_ids=["black_gram"])
    assert mandi_prewarm._farmer_commodities(farmer, crop_names) == {"Tomato", "Black Gram"}


def test_farmer_commodities_skips_unknown_crop_ids():
    farmer = _farmer("f1", crop="mystery_crop")
    assert mandi_prewarm._farmer_commodities(farmer, {}) == set()


def test_run_warms_only_pairs_tied_to_real_farmers(monkeypatch):
    monkeypatch.setattr(firestore_client, "list_crops", lambda: [TOMATO, BLACK_GRAM])
    monkeypatch.setattr(firestore_client, "list_farmers", lambda: [
        _farmer("f1", crop="tomato"),
        _farmer("f2", crop="black_gram"),
    ])
    monkeypatch.setattr(
        geocode, "resolve_farmer_district",
        lambda farmer: ("Andhra Pradesh", "Guntur"),
    )
    warmed = []
    monkeypatch.setattr(mandi, "warm", lambda commodity, state=None, district=None: warmed.append(
        (commodity, state, district)
    ))
    monkeypatch.setattr(mandi_prewarm.time, "sleep", lambda s: None)

    result = mandi_prewarm.run()

    assert result == {"attempted": 2, "succeeded": 2, "failed": 0}
    assert set(warmed) == {
        ("Tomato", "Andhra Pradesh", "Guntur"),
        ("Black Gram", "Andhra Pradesh", "Guntur"),
    }


def test_run_skips_farmers_with_no_resolved_district(monkeypatch):
    monkeypatch.setattr(firestore_client, "list_crops", lambda: [TOMATO])
    monkeypatch.setattr(firestore_client, "list_farmers", lambda: [_farmer("f1", crop="tomato")])
    monkeypatch.setattr(geocode, "resolve_farmer_district", lambda farmer: (None, None))
    monkeypatch.setattr(mandi, "warm", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("should not warm without a district")
    ))

    assert mandi_prewarm.run() == {"attempted": 0, "succeeded": 0, "failed": 0}


def test_run_continues_after_one_pair_fails(monkeypatch):
    monkeypatch.setattr(firestore_client, "list_crops", lambda: [TOMATO, BLACK_GRAM])
    monkeypatch.setattr(firestore_client, "list_farmers", lambda: [
        _farmer("f1", crop="tomato"),
        _farmer("f2", crop="black_gram"),
    ])
    monkeypatch.setattr(geocode, "resolve_farmer_district", lambda farmer: ("Andhra Pradesh", "Guntur"))

    def flaky_warm(commodity, state=None, district=None):
        if commodity == "Tomato":
            raise mandi.MandiError("data.gov.in down")

    monkeypatch.setattr(mandi, "warm", flaky_warm)
    monkeypatch.setattr(mandi_prewarm.time, "sleep", lambda s: None)

    result = mandi_prewarm.run()
    assert result == {"attempted": 2, "succeeded": 1, "failed": 1}
