"""Explain module (PROJECT_SPEC.md layer 2: AI content, on top of the deterministic core).

Turns an already-decided FusionOutput into a warm, plain-language message for the
farmer. It only narrates the fusion decision -- it never changes the diagnosis,
confidence, or decision. If Gemini fails, template_message() is the deterministic
fallback (layer 1 / layer 3 of the spec: templates + silent fallback).
"""
from google import genai

from config import GEMINI_API_KEY, GEMINI_MODEL
from models import FusionOutput

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
