import pytest

from engine.fusion import fuse
from models import ContextLocation, FusionContext, Soil, VisionCandidate, VisionOutput, Weather


def test_fusion_demo_photo_favors_late_blight():
    """PROJECT_SPEC.md demo scenario: cool, humid, recently-rained tomato plot
    with a vision reading that itself leans late_blight but isn't overwhelming.
    The environmental prior should reinforce vision's top pick.
    """
    vision = VisionOutput(
        image_quality="good",
        crop_confirmed="tomato",
        candidates=[
            VisionCandidate(condition="late_blight", confidence=0.45, visible_symptoms=["dark lesions"]),
            VisionCandidate(condition="early_blight", confidence=0.35, visible_symptoms=[]),
            VisionCandidate(condition="nitrogen_deficiency", confidence=0.20, visible_symptoms=[]),
        ],
        notes="",
    )
    context = FusionContext(
        crop="tomato",
        location=ContextLocation(lat=16.3067, lng=80.4365, resolution="village"),
        weather=Weather(temp_c=19.0, humidity_pct=92.0, rain_48h_mm=12.0, source="live"),
        soil=Soil(nitrogen="unknown", source="unknown"),
        nearby_confirmed=[],
    )

    result = fuse(vision, context)

    assert result.top == "late_blight"
    assert result.confidence == pytest.approx(0.70, abs=0.03)
