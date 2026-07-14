"""Mandi (wholesale market) prices via data.gov.in's Agmarknet resource
(PROJECT_SPEC.md context layer + layer-3 fallback).

Real-time commodity prices for the assistant's mandi_price intent. No Gemini is
involved in stating the number: the modal price, market, and date come straight
from the government dataset and are read back verbatim -- an LLM must never be
asked to restate, round, or "recall" a price (a fabrication risk we simply don't
take). If the API key is missing, the request fails/times out, or no row is
found, callers get None and degrade to a "no rate available" message -- this
module NEVER invents a number.
"""
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import requests

import firestore_client

log = logging.getLogger("kisanmate.mandi")

RESOURCE_URL = "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070"
_TIMEOUT = 4
_TTL = 2 * 3600  # 1-3h cache per spec
_CACHE: dict = {}

# Persistent (Firestore-backed) cache for each tier's raw fetch, one layer below
# _CACHE above. Where _CACHE is a fast, per-process, short-lived cache of the
# fully-resolved answer, this survives restarts and is shared across instances,
# and is what the daily pre-warm job (mandi_prewarm.py) populates -- so a
# farmer's first request of the day can still be served from yesterday's poll
# instead of waiting on a live data.gov.in call. Gated on GOOGLE_CLOUD_PROJECT
# (the same signal the rest of the app uses for "Firestore is configured") so
# tests and local runs without GCP set up just skip straight to a live fetch.
_PERSISTENT_TTL = 24 * 3600


def _persistent_cache_enabled() -> bool:
    return bool(os.environ.get("GOOGLE_CLOUD_PROJECT"))


def _persistent_key(commodity: str, state, district, market) -> str:
    return "|".join(_cache_key(commodity, state, district, market))


def _cache_age_seconds(fetched_at: datetime) -> float:
    return (datetime.now(timezone.utc) - fetched_at).total_seconds()


def _persistent_cache_get(key: str) -> Optional[list[dict]]:
    if not _persistent_cache_enabled():
        return None
    try:
        doc = firestore_client.get_mandi_cache(key)
    except Exception as exc:
        log.debug("mandi persistent cache read skipped: %s", exc)
        return None
    if not doc:
        return None
    fetched_at = doc.get("fetched_at")
    if fetched_at is None or _cache_age_seconds(fetched_at) > _PERSISTENT_TTL:
        return None
    return doc.get("records") or []


def _persistent_cache_set(key: str, records: list[dict]) -> None:
    if not _persistent_cache_enabled():
        return
    try:
        firestore_client.set_mandi_cache(key, records)
    except Exception as exc:
        log.debug("mandi persistent cache write skipped: %s", exc)


class MandiError(Exception):
    """Raised on a hard failure to reach the mandi price API (missing key,
    network, timeout, malformed response) -- as opposed to a routine "no rate
    for this commodity/market today", which is a normal None return."""


def _cache_key(commodity: str, state, district, market) -> tuple:
    return (
        commodity.strip().lower(),
        (state or "").strip().lower(),
        (district or "").strip().lower(),
        (market or "").strip().lower(),
    )


def _titlecase(value: Optional[str]) -> Optional[str]:
    """Agmarknet's filters are exact-match against Title Case values (e.g.
    "Tomato", "Andhra Pradesh") -- normalize so a lowercase extraction from the
    intent classifier (or a farmer typing "guntur") still matches real rows."""
    return value.strip().title() if value else None


def _fetch(commodity: str, state: Optional[str] = None, district: Optional[str] = None,
           market: Optional[str] = None, limit: int = 10) -> list[dict]:
    """One filtered call to the resource. Raises MandiError on any hard failure."""
    api_key = os.environ.get("DATA_GOV_API_KEY")
    if not api_key:
        raise MandiError("DATA_GOV_API_KEY is not configured")

    params = {"api-key": api_key, "format": "json", "limit": limit}
    commodity = _titlecase(commodity)
    state = _titlecase(state)
    district = _titlecase(district)
    market = _titlecase(market)
    if commodity:
        params["filters[commodity]"] = commodity
    if state:
        params["filters[state]"] = state
    if district:
        params["filters[district]"] = district
    if market:
        params["filters[market]"] = market

    try:
        resp = requests.get(RESOURCE_URL, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise MandiError(f"mandi price fetch failed: {exc}") from exc

    if isinstance(data, dict) and data.get("error"):
        raise MandiError(f"data.gov.in error: {data['error']}")
    return (data.get("records") or []) if isinstance(data, dict) else []


def _fetch_cached(commodity: str, state=None, district=None, market=None,
                   limit: int = 10, force: bool = False) -> list[dict]:
    """`_fetch()`, backed by the persistent cache above at this tier's own key.
    `force=True` (used by the daily pre-warm job) ignores any existing cache
    entry and always makes a live call, so a farmer's morning query still sees
    a fresh row rather than yesterday's pre-warmed one going stale unnoticed."""
    key = _persistent_key(commodity, state, district, market)
    if not force:
        cached = _persistent_cache_get(key)
        if cached is not None:
            return cached

    records = _fetch(commodity, state=state, district=district, market=market, limit=limit)
    _persistent_cache_set(key, records)
    return records


def warm(commodity: str, state: Optional[str] = None, district: Optional[str] = None) -> None:
    """Force-refresh the persistent cache for one (commodity, state, district)
    tier. Called by the daily pre-warm job (mandi_prewarm.py) for the districts
    real farmers are in; everything else still goes through the ordinary
    on-demand path in get_price(). Raises MandiError on a hard failure -- the
    caller (mandi_prewarm.run()) is expected to catch and log per-pair so one
    bad row doesn't abort the whole run."""
    _fetch_cached(commodity, state=state, district=district, force=True)


def _best_record(records: list[dict]) -> Optional[dict]:
    """The most recent row (by arrival_date) that actually has a modal price."""
    usable = [r for r in records if r.get("modal_price")]
    if not usable:
        return None
    usable.sort(key=lambda r: r.get("arrival_date", ""), reverse=True)
    r = usable[0]
    try:
        modal = float(r["modal_price"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "commodity": r.get("commodity"),
        "variety": r.get("variety"),
        "market": r.get("market"),
        "district": r.get("district"),
        "state": r.get("state"),
        "arrival_date": r.get("arrival_date"),
        "modal_price_per_quintal": modal,
        "min_price_per_quintal": _safe_float(r.get("min_price")),
        "max_price_per_quintal": _safe_float(r.get("max_price")),
    }


def _safe_float(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def get_price(commodity: str, state: Optional[str] = None, district: Optional[str] = None,
              market: Optional[str] = None) -> Optional[dict]:
    """The commodity's latest modal price, preferring the narrowest known
    location and widening a tier at a time if there's no row today. Returns
    None (never a fabricated number) if nothing is found at any tier.

    `district` and `market` are NOT ANDed together as one query -- a district
    has many markets, so requiring an exact simultaneous match on both would
    silently miss real data reported under a different market in the same
    district. Each tier tries only the location fields it actually has:
      1. market (+ state/district, if also known)
      2. district (+ state, if also known)
      3. state alone

    Raises MandiError only for a hard failure (missing key/network/timeout) --
    callers should treat that the same as a None result for the farmer-facing
    message, but may want to log it differently for telemetry.
    """
    commodity = (commodity or "").strip()
    if not commodity:
        return None

    key = _cache_key(commodity, state, district, market)
    now = time.time()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]

    tiers = []
    if market:
        tiers.append({"state": state, "district": district, "market": market})
    if district:
        tiers.append({"state": state, "district": district})
    if state:
        tiers.append({"state": state})

    result = None
    for tier_kwargs in tiers:
        records = _fetch_cached(commodity, **tier_kwargs)
        result = _best_record(records)
        if result:
            break

    _CACHE[key] = (now + _TTL, result)
    return result
