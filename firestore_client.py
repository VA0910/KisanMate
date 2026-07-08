"""Firestore access layer.

Connects via Application Default Credentials (no key files, per PROJECT_SPEC.md).
On Cloud Run this picks up the service's identity automatically; locally it
uses `gcloud auth application-default login` and the active gcloud project.
"""
from functools import lru_cache
from typing import Optional

from google.cloud import firestore

from models import Alert, Case, Crop, Farmer, Telemetry

FARMERS_COLLECTION = "farmers"
CROPS_COLLECTION = "crops"
CASES_COLLECTION = "cases"
ALERTS_COLLECTION = "alerts"
TELEMETRY_COLLECTION = "telemetry"


@lru_cache(maxsize=None)
def get_client() -> firestore.Client:
    return firestore.Client()


# --- farmers -----------------------------------------------------------------

def upsert_farmer(farmer: Farmer) -> str:
    """Write a farmer, overwriting any existing doc with the same id (idempotent)."""
    collection = get_client().collection(FARMERS_COLLECTION)
    doc_ref = collection.document(farmer.id) if farmer.id else collection.document()
    doc_ref.set(farmer.model_dump(exclude={"id"}))
    return doc_ref.id


def get_farmer(farmer_id: str) -> Optional[Farmer]:
    doc = get_client().collection(FARMERS_COLLECTION).document(farmer_id).get()
    if not doc.exists:
        return None
    return Farmer(id=doc.id, **doc.to_dict())


def normalize_phone(phone: str) -> str:
    """Reduce a phone number to comparable digits: strip formatting and any
    country code, keeping the last 10 digits (Indian mobile length).

    So "+91-9876500001", "91 98765 00001", and "9876500001" all match, letting
    a farmer type their bare 10-digit number to reach their seeded document.
    """
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return digits[-10:] if len(digits) > 10 else digits


def update_farmer(farmer_id: str, updates: dict) -> None:
    """Apply a partial update to a farmer document (profile edits).

    Only the provided keys are written, so unrelated fields (name, phone, the
    seeded crop, etc.) are preserved. Nested fields like `location` and
    `current_crops` are passed as whole values.
    """
    get_client().collection(FARMERS_COLLECTION).document(farmer_id).update(updates)


def get_farmer_by_phone(phone: str) -> Optional[Farmer]:
    """Resolve a phone number to an existing farmer document (phone is identity).

    Matches on the normalized number so the stored format (e.g. "+91-...") need
    not equal what the farmer typed. The farmer set is tiny, so a scan is fine
    and avoids depending on an exact-string index.
    """
    target = normalize_phone(phone)
    if not target:
        return None
    for farmer in list_farmers():
        if normalize_phone(farmer.phone) == target:
            return farmer
    return None


def list_farmers() -> list[Farmer]:
    docs = get_client().collection(FARMERS_COLLECTION).stream()
    return [Farmer(id=doc.id, **doc.to_dict()) for doc in docs]


# --- crops --------------------------------------------------------------------

def upsert_crop(crop: Crop) -> str:
    """Write a crop, overwriting any existing doc with the same id (idempotent)."""
    collection = get_client().collection(CROPS_COLLECTION)
    doc_ref = collection.document(crop.id) if crop.id else collection.document()
    doc_ref.set(crop.model_dump(exclude={"id"}))
    return doc_ref.id


def get_crop(crop_id: str) -> Optional[Crop]:
    doc = get_client().collection(CROPS_COLLECTION).document(crop_id).get()
    if not doc.exists:
        return None
    return Crop(id=doc.id, **doc.to_dict())


def list_crops() -> list[Crop]:
    docs = get_client().collection(CROPS_COLLECTION).stream()
    return [Crop(id=doc.id, **doc.to_dict()) for doc in docs]


# --- cases ---------------------------------------------------------------------

def create_case(case: Case) -> str:
    doc_ref = get_client().collection(CASES_COLLECTION).document()
    data = case.model_dump(exclude={"id", "created_at"})
    data["created_at"] = firestore.SERVER_TIMESTAMP
    doc_ref.set(data)
    return doc_ref.id


def get_case(case_id: str) -> Optional[Case]:
    doc = get_client().collection(CASES_COLLECTION).document(case_id).get()
    if not doc.exists:
        return None
    return Case(id=doc.id, **doc.to_dict())


def list_cases_by_status(statuses: list[str]) -> list[Case]:
    """Cases whose status is in `statuses` (e.g. the RSK officer's review queue).

    Uses a single-field `in` filter (no composite index needed) and sorts newest-
    first in Python so we don't require a Firestore composite index on
    status + created_at.
    """
    query = get_client().collection(CASES_COLLECTION).where(
        filter=firestore.FieldFilter("status", "in", statuses)
    )
    cases = [Case(id=doc.id, **doc.to_dict()) for doc in query.stream()]
    cases.sort(key=lambda c: (c.created_at is not None, c.created_at), reverse=True)
    return cases


def update_case(case_id: str, updates: dict) -> None:
    get_client().collection(CASES_COLLECTION).document(case_id).update(updates)


# --- alerts ----------------------------------------------------------------------

def create_alert(alert: Alert) -> str:
    doc_ref = get_client().collection(ALERTS_COLLECTION).document()
    data = alert.model_dump(exclude={"id", "created_at"})
    data["created_at"] = firestore.SERVER_TIMESTAMP
    doc_ref.set(data)
    return doc_ref.id


def get_alert(alert_id: str) -> Optional[Alert]:
    doc = get_client().collection(ALERTS_COLLECTION).document(alert_id).get()
    if not doc.exists:
        return None
    return Alert(id=doc.id, **doc.to_dict())


def list_alerts_for_farmer(farmer_id: str) -> list[Alert]:
    query = get_client().collection(ALERTS_COLLECTION).where(
        filter=firestore.FieldFilter("recipient_ids", "array_contains", farmer_id)
    )
    return [Alert(id=doc.id, **doc.to_dict()) for doc in query.stream()]


def list_recent_alerts(limit: int = 50) -> list[Alert]:
    """All fired alerts, newest first (the RSK officer's fired-alerts panel)."""
    query = (
        get_client()
        .collection(ALERTS_COLLECTION)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    return [Alert(id=doc.id, **doc.to_dict()) for doc in query.stream()]


# --- telemetry ---------------------------------------------------------------------

def clear_collection(name: str, batch_size: int = 300) -> int:
    """Delete every document in a collection, in batches. Returns the count.

    Used by the demo reset to wipe generated cases/alerts/telemetry. Seeded
    farmers are re-upserted separately, not cleared here.
    """
    collection = get_client().collection(name)
    deleted = 0
    while True:
        docs = list(collection.limit(batch_size).stream())
        if not docs:
            return deleted
        batch = get_client().batch()
        for doc in docs:
            batch.delete(doc.reference)
        batch.commit()
        deleted += len(docs)


def log_telemetry(entry: Telemetry) -> str:
    doc_ref = get_client().collection(TELEMETRY_COLLECTION).document()
    data = entry.model_dump(exclude={"id", "created_at"})
    data["created_at"] = firestore.SERVER_TIMESTAMP
    doc_ref.set(data)
    return doc_ref.id


def list_recent_telemetry(limit: int = 50) -> list[Telemetry]:
    query = (
        get_client()
        .collection(TELEMETRY_COLLECTION)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    return [Telemetry(id=doc.id, **doc.to_dict()) for doc in query.stream()]
