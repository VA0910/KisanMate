"""Seed Firestore with the demo scenario farmers from PROJECT_SPEC.md.

Uses fixed document ids and `.set()` (full overwrite) via upsert_farmer, so
running this script twice leaves Firestore in the same state (idempotent).

Coordinates are offset from Guntur town (16.3067 N, 80.4365 E) using
1 deg latitude ~= 111 km and 1 deg longitude ~= 111 km * cos(latitude),
so the seeded farmers sit at roughly the distances the spec calls for.
"""
from datetime import date, timedelta

from firestore_client import upsert_farmer
from models import CurrentCrop, Farmer, FarmerLocation

GUNTUR = (16.3067, 80.4365)

# Ramesh's tomato was planted a whole number of irrigation cadences ago (tomato
# is water_need "medium" -> a 5-day cadence), so an irrigation reminder falls DUE
# TODAY the moment the demo is seeded -- it shows on the home screen at login with
# no action taken. 60 days also puts the crop at its flowering stage.
RAMESH_PLANTING_DATE = (date.today() - timedelta(days=60)).isoformat()

DEMO_FARMERS = [
    Farmer(
        id="ramesh",
        name="Ramesh",
        phone="+91-9876500001",
        lang="hi",
        location=FarmerLocation(lat=GUNTUR[0], lng=GUNTUR[1], mandal="Guntur"),
        crop="tomato",
        land_size_acres=2.5,
        growth_stage="flowering",
        soil_type="black",
        current_crops=[CurrentCrop(crop_id="tomato", planting_date=RAMESH_PLANTING_DATE)],
    ),
    Farmer(
        id="lakshmi",
        name="Lakshmi",
        phone="+91-9876500002",
        lang="te",
        # ~3.0 km north of Ramesh -> should receive the alert (same crop, in radius)
        location=FarmerLocation(lat=16.3337, lng=80.4365, mandal="Pedakakani"),
        crop="tomato",
        land_size_acres=1.8,
        growth_stage="flowering",
    ),
    Farmer(
        id="venkat",
        name="Venkat",
        phone="+91-9876500003",
        lang="te",
        # ~2.0 km east of Ramesh -> should NOT receive it (wrong crop)
        location=FarmerLocation(lat=16.3067, lng=80.4553, mandal="Chinakakani"),
        crop="rice",
        land_size_acres=3.0,
        growth_stage="tillering",
    ),
    Farmer(
        id="sita",
        name="Sita",
        phone="+91-9876500004",
        lang="hi",
        # ~15.0 km south of Ramesh -> should NOT receive it (out of radius)
        location=FarmerLocation(lat=16.1716, lng=80.4365, mandal="Tadikonda"),
        crop="tomato",
        land_size_acres=4.2,
        growth_stage="vegetative",
    ),
]


def seed() -> None:
    for farmer in DEMO_FARMERS:
        doc_id = upsert_farmer(farmer)
        print(f"seeded farmer '{doc_id}' ({farmer.name}, {farmer.crop}, {farmer.location.mandal})")


if __name__ == "__main__":
    seed()
