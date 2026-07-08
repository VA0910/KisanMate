"""Impure orchestration shared across entry points.

Sits above the pure engine (engine/*) and the persistence layer
(firestore_client): it performs Firestore writes, so it lives here rather than
in engine/, which PROJECT_SPEC.md keeps free of I/O. Both /api/confirm and the
demo runner drive the confirmation gate through the one function here, so the
"officer verdict -> propagation" path can never drift between them.
"""
from typing import Callable, Optional

import firestore_client
from engine.alert_engine import propagation
from engine.prior_table import CONTAGIOUS
from models import Case, Farmer


def confirm_and_propagate(
    case: Case,
    verdict: str,
    farmers: list[Farmer],
    error_logger: Optional[Callable[[Exception], None]] = None,
) -> Optional[dict]:
    """Apply the officer's authoritative verdict to an already-loaded case and,
    if the verdict is a contagious condition, fire the community alert engine.

    The verdict overrides whatever the AI inferred (PROJECT_SPEC.md layer 4):
    status becomes "confirmed" and the verdict becomes the case's condition
    before propagation, so the alert reflects the human decision. Returns an
    alert-summary dict, or None when no area alert fires (not contagious, gate
    not passed, or no matching nearby farmers). A propagation/persistence error
    is reported via error_logger and swallowed -- it must never fail the
    confirmation itself.
    """
    contagious = CONTAGIOUS.get(verdict, False)

    firestore_client.update_case(
        case.id,
        {
            "officer_verdict": verdict,
            "status": "confirmed",
            "condition": verdict,
            "contagious": contagious,
        },
    )
    # Mirror the write onto the in-memory case so propagation sees the verdict
    # (a "confirmed" status is also what passes the alert engine's gate).
    case.officer_verdict = verdict
    case.status = "confirmed"
    case.condition = verdict
    case.contagious = contagious

    if not contagious:
        return None

    try:
        alert = propagation(case, farmers)
        if alert is None:
            return None
        alert_id = firestore_client.create_alert(alert)
        name_by_id = {f.id: f.name for f in farmers}
        return {
            "alert_id": alert_id,
            "tier": alert.tier,
            "condition": alert.condition,
            "recipient_count": len(alert.recipient_ids),
            "recipients": [{"id": rid, "name": name_by_id.get(rid, rid)} for rid in alert.recipient_ids],
        }
    except Exception as exc:
        if error_logger is not None:
            error_logger(exc)
        return None
