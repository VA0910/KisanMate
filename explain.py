"""Explain module (PROJECT_SPEC.md layer 2: AI content, on top of the deterministic core).

Turns an already-decided FusionOutput into a warm, plain-language message for the
farmer. It only narrates the fusion decision -- it never changes the diagnosis,
confidence, or decision. If Gemini fails, template_message() is the deterministic
fallback (layer 1 / layer 3 of the spec: templates + silent fallback).
"""
import json

from google import genai

from config import GEMINI_API_KEY, GEMINI_MODEL
from models import CropRecommendation, FusionOutput

LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "te": "Telugu"}

EXPLAIN_SYSTEM_INSTRUCTION = """You are KisanMate, a friendly farm assistant speaking to a
smallholder farmer in India. You are given a crop diagnosis result that has ALREADY been
decided by a separate rule-based system. Explain it warmly and simply -- do not diagnose
anything yourself.

Rules you must always follow:
- Write only in the requested language, in short plain sentences that still make sense
  when read aloud to someone with limited literacy.
- Never invent or change the diagnosis, confidence, or decision -- only explain the one given to you.
- If the decision is "escalate_rsk", say clearly and kindly that a local RSK (Raitha Saradhi
  Kendra) officer should look at this before the farmer acts on it, and do not state a confident
  diagnosis as if it were certain.
- If you mention spraying or any pesticide/fungicide as a next step, you must:
  (a) tell the farmer to check the weather first (do not spray before rain or in high wind), and
  (b) tell them to confirm the exact product and dosage with the RSK officer or an agriculture
      extension worker.
- Never state a specific chemical name, brand, or dosage/quantity yourself.
- Keep the whole message under 80 words.
"""

# Deterministic fallback templates (layer 1), used only if the Gemini call above fails.
_TEMPLATES = {
    "en": {
        "advise": (
            "Your crop check is done. Please follow the suggested care steps. "
            "If you plan to spray anything, check the weather first and confirm "
            "the product and amount with your local RSK officer."
        ),
        "escalate_rsk": (
            "We are not fully sure about this case yet. Please show this photo to "
            "your local RSK officer so they can confirm it and guide your next step."
        ),
    },
    "hi": {
        "advise": (
            "आपकी फ़सल की जांच पूरी हो गई है। बताए गए उपाय अपनाएं। "
            "अगर आप कोई दवा छिड़कने वाले हैं, तो पहले मौसम देखें और मात्रा "
            "अपने नज़दीकी RSK अधिकारी से पुष्टि करें।"
        ),
        "escalate_rsk": (
            "अभी हमें इस मामले में पूरा भरोसा नहीं है। कृपया यह फोटो अपने "
            "नज़दीकी RSK अधिकारी को दिखाएं ताकि वे पुष्टि कर सकें और सही सलाह दे सकें।"
        ),
    },
    "te": {
        "advise": (
            "మీ పంట పరిశీలన పూర్తయింది. సూచించిన జాగ్రత్తలు పాటించండి. "
            "మందు పిచికారీ చేయాలనుకుంటే, ముందు వాతావరణం చూసి, మోతాదు గురించి "
            "మీ సమీప RSK అధికారిని సంప్రదించండి."
        ),
        "escalate_rsk": (
            "ఈ విషయంలో మాకు ఇంకా పూర్తి నమ్మకం లేదు. దయచేసి ఈ ఫోటోను మీ సమీప "
            "RSK అధికారికి చూపించి నిర్ధారణ, సరైన సలహా పొందండి."
        ),
    },
}


class ExplainError(Exception):
    """Raised when Gemini fails to produce an explanation message."""


class RecommendExplainError(Exception):
    """Raised when Gemini fails to produce recommendation reason text."""


RECOMMEND_SYSTEM_INSTRUCTION = """You are KisanMate, a friendly farm assistant speaking to a
smallholder farmer in India. A separate rule-based system has ALREADY ranked crops for this
farmer's field. For each crop you are given the deterministic criteria it matched (some of:
soil type, current season, area). Your only job is to write one short, warm reason per crop
explaining why it suits this farmer's field.

Rules you must always follow:
- Write only in the requested language, in one short plain sentence per crop that makes sense
  read aloud to someone with limited literacy.
- Ground every reason ONLY in the matched criteria you are given (soil / season / area). Never
  invent facts, yields, prices, chemicals, or dosages.
- Do not re-rank the crops or contradict the ranking; only explain it.
- Keep each reason under 20 words.
- Return ONLY a JSON object mapping each crop id to its reason string, nothing else."""


def explain_recommendations(recommendations: list[CropRecommendation], language: str) -> dict:
    """Ask Gemini to write a warm reason per ranked crop. Raises RecommendExplainError on failure.

    Returns {crop_id: reason_text}. The deterministic reason already on each
    recommendation is the fallback the caller keeps if this raises.
    """
    language_name = LANGUAGE_NAMES.get(language, "English")
    items = [
        {
            "crop_id": rec.crop,
            "crop_name": rec.names.en if rec.names else rec.crop,
            "matched": rec.matched,
        }
        for rec in recommendations
    ]
    prompt = (
        f"{RECOMMEND_SYSTEM_INSTRUCTION}\n\n"
        f"Respond in: {language_name}\n\n"
        f"Crops and the criteria each matched (JSON):\n{json.dumps(items)}"
    )
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = (response.text or "").strip()
        if not text:
            raise RecommendExplainError("Gemini returned an empty recommendation explanation")
        # Tolerate a ```json fenced block around the object.
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"):]
        reasons = json.loads(text)
        if not isinstance(reasons, dict):
            raise RecommendExplainError("Gemini recommendation explanation was not a JSON object")
        return {str(k): str(v) for k, v in reasons.items()}
    except RecommendExplainError:
        raise
    except Exception as exc:
        raise RecommendExplainError(f"Gemini recommendation explain call failed: {exc}") from exc


def template_message(decision: str, language: str) -> str:
    """Deterministic per-language fallback message; never calls Gemini."""
    templates = _TEMPLATES.get(language, _TEMPLATES["en"])
    return templates.get(decision, templates["escalate_rsk"])


def explain_fusion(fusion: FusionOutput, language: str) -> str:
    """Ask Gemini to narrate an already-decided fusion result. Raises ExplainError on failure."""
    language_name = LANGUAGE_NAMES.get(language, "English")
    prompt = (
        f"{EXPLAIN_SYSTEM_INSTRUCTION}\n\n"
        f"Respond in: {language_name}\n\n"
        f"Diagnosis result (JSON, for your reference only -- do not repeat it verbatim):\n"
        f"{fusion.model_dump_json()}"
    )
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = (response.text or "").strip()
        if not text:
            raise ExplainError("Gemini returned an empty explanation")
        return text
    except ExplainError:
        raise
    except Exception as exc:
        raise ExplainError(f"Gemini explain call failed: {exc}") from exc
