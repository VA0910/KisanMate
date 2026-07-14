"""Daily pre-warm of the mandi price cache (PROJECT_SPEC.md context layer),
triggered once a day by the admin refresh-cache endpoint (main.py).

Not a blind crawl of every commodity x every district in India: only the
(commodity, state, district) tiers tied to a real farmer's own registered
crops are refreshed, so the daily fan-out is bounded by farmer count, not the
full Agmarknet catalogue. Everything else (a commodity/district nobody near
here grows, or is asking about) still gets served by mandi.get_price()'s own
on-demand persistent cache -- refreshed lazily on next ask instead of
proactively, per PROJECT_SPEC.md's layered-fallback approach.
"""
import logging
import time

import firestore_client
import geocode
import mandi

log = logging.getLogger("kisanmate.mandi_prewarm")

_THROTTLE_SECONDS = 0.5  # be polite to data.gov.in's per-key rate limit


def _farmer_commodities(farmer, crop_names: dict) -> set[str]:
    """The English display names (Agmarknet's commodity naming, e.g. "Black
    Gram") for every crop id the farmer grows -- crop.names.en, not the raw
    underscored crop id, which _titlecase() would mangle ("black_gram" ->
    "Black_Gram", not a real Agmarknet commodity)."""
    crop_ids = {farmer.crop} | {c.crop_id for c in farmer.current_crops}
    return {crop_names[cid] for cid in crop_ids if crop_names.get(cid)}


def run() -> dict:
    """Refresh the persistent mandi cache for every (commodity, state, district)
    tied to a real farmer. Returns attempt/success/failure counts for the
    caller (the admin endpoint) to report; never raises itself -- a single
    farmer/commodity failure is logged and skipped so one bad pair can't abort
    the whole run."""
    crop_names = {crop.id: crop.names.en for crop in firestore_client.list_crops() if crop.id}

    jobs: set[tuple[str, str, str]] = set()
    for farmer in firestore_client.list_farmers():
        state, district = geocode.resolve_farmer_district(farmer)
        if not district:
            continue
        for commodity in _farmer_commodities(farmer, crop_names):
            jobs.add((commodity, state or "", district))

    attempted = succeeded = failed = 0
    for commodity, state, district in jobs:
        attempted += 1
        try:
            mandi.warm(commodity, state=state or None, district=district)
            succeeded += 1
        except Exception as exc:
            failed += 1
            log.warning("mandi pre-warm failed for %s/%s/%s: %s", commodity, state, district, exc)
        time.sleep(_THROTTLE_SECONDS)

    log.info("mandi pre-warm done: attempted=%d succeeded=%d failed=%d", attempted, succeeded, failed)
    return {"attempted": attempted, "succeeded": succeeded, "failed": failed}
