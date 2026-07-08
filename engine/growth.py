"""Deterministic growth-stage computation (PROJECT_SPEC.md layer 1).

Pure date math, no AI/network calls. Given a crop's planting date and the crop
DB's cycle_days + growth_stages, work out which stage the crop is in today. This
is the memory/reminders foundation and the "current growth stage" that enriches
the Gemini context -- but it never changes the fusion or scoring logic.
"""
from datetime import date, datetime
from typing import Optional


def _parse_date(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        # Accept "YYYY-MM-DD" (and tolerate a full ISO timestamp).
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def days_since_planting(planting_date, today: Optional[date] = None) -> Optional[int]:
    planted = _parse_date(planting_date)
    if planted is None:
        return None
    today = today or date.today()
    return max((today - planted).days, 0)


def _stage_start(stage) -> int:
    """Read start_day off a GrowthStage model or a plain dict."""
    if hasattr(stage, "start_day"):
        return stage.start_day
    return stage.get("start_day", 0)


def _stage_name(stage) -> str:
    if hasattr(stage, "name"):
        return stage.name
    return stage.get("name", "")


def _stage_care_note(stage) -> str:
    if hasattr(stage, "care_note"):
        return stage.care_note
    return stage.get("care_note", "")


def compute_stage(planting_date, cycle_days: int, growth_stages, today: Optional[date] = None) -> Optional[dict]:
    """Return the current growth stage, or None if it can't be computed.

    {"name", "day", "cycle_days", "care_note", "past_harvest"}. The stage is the
    last one whose start_day has been reached; before the first stage's start_day
    we report the first stage. `past_harvest` flags a crop older than its cycle.
    """
    day = days_since_planting(planting_date, today)
    if day is None or not growth_stages:
        return None

    ordered = sorted(growth_stages, key=_stage_start)
    current = ordered[0]
    for stage in ordered:
        if _stage_start(stage) <= day:
            current = stage
        else:
            break

    return {
        "name": _stage_name(current),
        "day": day,
        "cycle_days": cycle_days,
        "care_note": _stage_care_note(current),
        "past_harvest": bool(cycle_days) and day > cycle_days,
    }
