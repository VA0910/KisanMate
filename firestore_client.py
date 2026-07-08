"""Firestore access layer.

Connects via Application Default Credentials (no key files, per PROJECT_SPEC.md).
On Cloud Run this picks up the service's identity automatically; locally it
uses `gcloud auth application-default login` and the active gcloud project.
"""
from functools import lru_cache
from typing import Optional

from google.cloud import firestore

from models import Alert, Case, Farmer, Telemetry

FARMERS_COLLECTION = "farmers"
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


def list_farmers() -> list[Farmer]:
    docs = get_client().collection(FARMERS_COLLECTION).stream()
    return [Farmer(id=doc.id, **doc.to_dict()) for doc in docs]


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
