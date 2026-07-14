"""Reverse-geocoding helpers (Nominatim), shared by the location picker
(main.py), the assistant's mandi-price filters, and the daily mandi pre-warm
job (mandi_prewarm.py).
"""
import json
import logging
import urllib.parse
import urllib.request
from typing import Optional

import firestore_client

log = logging.getLogger("kisanmate.geocode")

NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"


def reverse_geocode_lookup(lat: float, lng: float) -> dict:
    """Raw Nominatim reverse-geocode response for a coordinate."""
    params = urllib.parse.urlencode(
        {"lat": lat, "lon": lng, "format": "jsonv2", "addressdetails": 1, "zoom": 12}
    )
    req = urllib.request.Request(
        f"{NOMINATIM_REVERSE_URL}?{params}",
        headers={"User-Agent": "KisanMate/1.0 (agriculture assistant demo)"},
    )
    with urllib.request.urlopen(req, timeout=6) as resp:
        return json.load(resp)


def reverse_geocode(lat: float, lng: float) -> Optional[str]:
    """Human-readable place name for a coordinate. Returns None if nothing
    usable comes back."""
    item = reverse_geocode_lookup(lat, lng)
    addr = item.get("address", {})
    locality = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("suburb")
        or addr.get("county")
        or ""
    )
    region = addr.get("state") or addr.get("state_district") or ""
    label = ", ".join(p for p in (locality, region) if p) or item.get("display_name")
    return label or None


def farmer_admin_area(lat: float, lng: float) -> tuple[Optional[str], Optional[str]]:
    """(state, district) for a coordinate. Returns (None, None) on any failure
    -- callers already treat that as "no location hint"."""
    try:
        addr = reverse_geocode_lookup(lat, lng).get("address", {})
    except Exception:
        return None, None
    return addr.get("state"), (addr.get("state_district") or addr.get("county"))


def resolve_farmer_district(farmer) -> tuple[Optional[str], Optional[str]]:
    """A farmer's (state, district), resolved once and cached on their profile
    (location_state/location_district) so repeat mandi-price lookups and the
    daily pre-warm job don't re-hit Nominatim for a location that essentially
    never changes."""
    if farmer.location_state and farmer.location_district:
        return farmer.location_state, farmer.location_district
    state, district = farmer_admin_area(farmer.location.lat, farmer.location.lng)
    if farmer.id and (state or district):
        try:
            firestore_client.update_farmer(
                farmer.id, {"location_state": state, "location_district": district}
            )
        except Exception as exc:
            log.warning("could not persist location for farmer %s: %s", farmer.id, exc)
    return state, district
