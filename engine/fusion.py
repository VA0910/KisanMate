"""Fusion engine (PROJECT_SPEC.md layer 1: the deterministic shell).

Pure Python, no AI calls. Combines a vision reading (or none, if Gemini failed)
with the deterministic prior, then applies the spec's decision rules. The AI
layer feeds this module inputs -- it must never reach in and change this logic.
"""
from typing import Optional

from engine.prior_table import TOMATO_CONDITIONS, compute_prior, is_contagious
from models import (
    Condition,
    FusionContext,
    FusionEvidence,
    FusionOutput,
    PosteriorEntry,
    VisionOutput,
)

# Calibration constants PROJECT_SPEC.md leaves as judgment calls (it fixes the
# prior table and the confidence/margin floors, but not these two knobs).
STRONG_THRESHOLD = 0.5  # vision confidence / prior share counted as "individually strong"
VISION_EPSILON = 0.01  # avoids a 0/0 posterior when vision gives every tracked condition 0

CONFIDENCE_ESCALATION_FLOOR = 0.55
MARGIN_ESCALATION_FLOOR = 0.15


def _context_completeness(context: FusionContext) -> list[str]:
    return [
        f"weather:{context.weather.source}",
        f"soil:{context.soil.source}",
        f"nearby_confirmed:{len(context.nearby_confirmed)}",
    ]


def fuse(
    vision: Optional[VisionOutput],
    context: FusionContext,
    candidates: Optional[list[str]] = None,
) -> FusionOutput:
    """Fuse a (possibly missing) vision reading with the deterministic prior.

    `candidates` are the conditions to consider -- the identified crop's diseases
    from the crops DB (Option 2), e.g. ["rice_blast","bacterial_leaf_blight",
    "healthy"]. When omitted (or exactly the tomato set) we use the calibrated
    tomato prior; otherwise there is no calibrated environmental prior, so the
    prior is uniform and fusion is vision-led (uncertain readings still escalate).

    Passing vision=None is the fallback path when Gemini's vision call is
    unreachable: image quality is treated as poor and the posterior collapses to
    the prior alone, which -- combined with the decision rule below -- escalates.
    """
    conditions = list(dict.fromkeys(candidates)) if candidates else list(TOMATO_CONDITIONS)
    if not conditions:
        conditions = list(TOMATO_CONDITIONS)

    # Use the calibrated tomato prior only for the tomato condition set; any other
    # crop's disease set gets a uniform (vision-led) prior.
    calibrated = set(conditions) == set(TOMATO_CONDITIONS)
    if calibrated:
        prior = compute_prior(context)
        prior_top = max(prior, key=prior.get)
    else:
        prior = {c: 1.0 / len(conditions) for c in conditions}
        prior_top = "unknown"

    # The plant being unidentifiable is treated like a poor image: we can't
    # responsibly diagnose a plant we can't recognise, so escalate (PROJECT_SPEC.md).
    unidentifiable = vision is not None and vision.identified_crop == "unidentifiable"

    if vision is None:
        image_quality = "poor"
        vision_confidence: dict[str, float] = {}
        vision_top = "unknown"
    else:
        image_quality = "poor" if unidentifiable else vision.image_quality
        vision_confidence = {c.condition: c.confidence for c in vision.candidates}
        vision_top = (
            max(vision.candidates, key=lambda c: c.confidence).condition
            if vision.candidates
            else "unknown"
        )

    # posterior(condition) proportional to vision_confidence(condition) * normalized_prior(condition).
    # +VISION_EPSILON keeps every condition reachable instead of hard-zeroing it, and cancels out of
    # the renormalization when vision contributes nothing -- so vision=None reduces to prior-only.
    combined = {
        c: (vision_confidence.get(c, 0.0) + VISION_EPSILON) * prior[c] for c in conditions
    }
    combined_total = sum(combined.values()) or 1.0
    posterior = {c: score / combined_total for c, score in combined.items()}

    ranked = sorted(posterior.items(), key=lambda item: item[1], reverse=True)
    top, confidence = ranked[0]
    margin = confidence - (ranked[1][1] if len(ranked) > 1 else 0.0)

    vision_strong = vision_confidence.get(vision_top, 0.0) >= STRONG_THRESHOLD
    # Only a calibrated prior can be "strong" enough to conflict with vision.
    prior_strong = calibrated and prior[prior_top] >= STRONG_THRESHOLD
    conflict = vision_top != prior_top and vision_strong and prior_strong

    decision = (
        "escalate_rsk"
        if confidence < CONFIDENCE_ESCALATION_FLOOR
        or margin < MARGIN_ESCALATION_FLOOR
        or conflict
        or image_quality == "poor"
        else "advise"
    )

    return FusionOutput(
        posterior=[
            PosteriorEntry(condition=c, score=posterior[c], contagious=is_contagious(c))
            for c in conditions
        ],
        top=top,
        confidence=confidence,
        margin=margin,
        conflict=conflict,
        decision=decision,
        alert_eligible=is_contagious(top),
        evidence=FusionEvidence(
            vision_top=vision_top,
            prior_top=prior_top,
            context_completeness=_context_completeness(context),
        ),
    )
