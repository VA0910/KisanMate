"""Vision module (PROJECT_SPEC.md layer 2: AI content, on top of the deterministic core).

Wraps a single Gemini vision call. This module never decides anything -- it only
turns a photo into a VisionOutput (or raises) for fusion.fuse() to combine with
the deterministic prior. Callers are expected to treat any VisionError as "no
usable vision reading" and call fusion.fuse(vision=None, ...) instead.
"""
import json

from google import genai
from google.genai import types
from pydantic import ValidationError

from config import GEMINI_API_KEY, GEMINI_MODEL
from models import VisionOutput

VISION_PROMPT = """You are an agronomy vision assistant helping diagnose tomato crop
photos taken by smallholder farmers in India.

Look at the attached photo and assess it. Score every condition you can reasonably
judge with its own confidence, not just the single most likely one, since a downstream
system combines your scores with local weather and soil data. Base confidence only on
what is visibly in the photo.

Return ONLY JSON matching this shape, with no extra commentary:
{
  "image_quality": "good" or "poor" (poor = blurry, too dark, too far away, or the
    plant/leaf is not clearly visible),
  "crop_confirmed": the crop you see, e.g. "tomato", or "uncertain" if you cannot tell,
  "candidates": [
    {"condition": one of "late_blight", "early_blight", "nitrogen_deficiency", "other", "healthy",
     "confidence": a number from 0.0 to 1.0,
     "visible_symptoms": ["short phrases describing what you actually see"]}
  ],
  "notes": "any short additional observation"
}
"""


class VisionError(Exception):
    """Raised when Gemini vision is unreachable or never produces a usable VisionOutput."""


def _client() -> genai.Client:
    return genai.Client(api_key=GEMINI_API_KEY)


def _call_gemini_vision(image_bytes: bytes, mime_type: str, extra_instruction: str = "") -> VisionOutput:
    prompt = VISION_PROMPT if not extra_instruction else f"{VISION_PROMPT}\n\n{extra_instruction}"
    response = _client().models.generate_content(
        model=GEMINI_MODEL,
        contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime_type), prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=VisionOutput,
        ),
    )
    return VisionOutput.model_validate(json.loads(response.text))


def diagnose_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> VisionOutput:
    """Diagnose an image with Gemini, retrying once if the first reply is invalid JSON.

    Raises VisionError if Gemini is unreachable, or still returns unusable output
    after the retry. Any other failure (network, auth, API error) is not retried.
    """
    try:
        return _call_gemini_vision(image_bytes, mime_type)
    except (json.JSONDecodeError, ValidationError):
        pass  # malformed/invalid-shape JSON -- worth one retry with a stronger reminder
    except Exception as exc:
        raise VisionError(f"Gemini vision call failed: {exc}") from exc

    try:
        return _call_gemini_vision(
            image_bytes,
            mime_type,
            extra_instruction="Reminder: reply with valid JSON only, matching the exact shape above.",
        )
    except (json.JSONDecodeError, ValidationError) as exc:
        raise VisionError(f"Gemini returned invalid JSON twice: {exc}") from exc
    except Exception as exc:
        raise VisionError(f"Gemini vision call failed on retry: {exc}") from exc
