"""Community alert engine (PROJECT_SPEC.md "Community alert engine (the hero)").

Pure Python, no AI/network calls: propagation() takes a confirmed case and the
farmer roster as plain arguments and returns an Alert (or None), so it can be
unit-tested without Firestore. The caller is responsible for persisting the
result via firestore_client.create_alert().
"""
from math import atan2, cos, radians, sin, sqrt
from typing import Optional

from engine.fusion import STRONG_THRESHOLD
from engine.prior_table import CONTAGIOUS, compute_prior
from models import Alert, Case, Farmer, LatLng

EARTH_RADIUS_KM = 6371.0

# PROJECT_SPEC.md fixes the demo distances (~3km should qualify, ~15km should
# not) but not an exact catchment radius; 10km sits comfortably between them.
DEFAULT_RADIUS_KM = 10.0

# "N>=3 independent nearby reports" per PROJECT_SPEC.md's confirmation gate.
AUTO_ELEVATION_REPORT_COUNT = 3


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lng2 - lng1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * atan2(sqrt(a), sqrt(1 - a))


def _confirmation_gate_passed(case: Case) -> bool:
    """(a) officer-confirmed, OR (b) N>=3 corroborating nearby reports, OR (c) strong environment."""
    if case.status == "confirmed":
        return True

    if case.context is not None:
        corroborating = sum(
            1 for nearby in case.context.nearby_confirmed if nearby.condition == case.condition
        )
        if corroborating >= AUTO_ELEVATION_REPORT_COUNT:
            return True

        prior = compute_prior(case.context)
        if case.condition in prior and prior[case.condition] >= STRONG_THRESHOLD:
            return True

    return False


def propagation(
    confirmed_case: Case,
    farmers: list[Farmer],
    radius_km: float = DEFAULT_RADIUS_KM,
) -> Optional[Alert]:
    """Fan a confirmed case out to nearby farmers growing the same susceptible crop.

    Returns None if the condition isn't contagious, the confirmation gate hasn't
    been passed, or nobody within radius_km grows the susceptible crop -- in all
    of those cases no area alert fires (the source farmer's own individual
    diagnosis already went out separately, unaffected by this gate).
    """
    if confirmed_case.condition is None or confirmed_case.context is None:
        return None
    if not CONTAGIOUS.get(confirmed_case.condition, False):
        return None
    if not _confirmation_gate_passed(confirmed_case):
        return None

    center = confirmed_case.context.location
    susceptible_crop = confirmed_case.context.crop

    recipients = [
        farmer
        for farmer in farmers
        if farmer.id != confirmed_case.farmer_id
        and farmer.crop == susceptible_crop
        and _haversine_km(center.lat, center.lng, farmer.location.lat, farmer.location.lng) <= radius_km
    ]
    if not recipients:
        return None

    # Every recipient here already matches the susceptible crop by construction,
    # so "alert" vs "warning" (PROJECT_SPEC.md's tiers) comes down to whether the
    # environment itself also strongly agrees with the confirmed condition.
    prior = compute_prior(confirmed_case.context)
    environment_strong = prior.get(confirmed_case.condition, 0.0) >= STRONG_THRESHOLD
    tier = "alert" if environment_strong else "warning"

    return Alert(
        source_case_id=confirmed_case.id or "",
        condition=confirmed_case.condition,
        tier=tier,
        center=LatLng(lat=center.lat, lng=center.lng),
        radius_km=radius_km,
        recipient_ids=[farmer.id for farmer in recipients if farmer.id],
    )
