"""Conversational farm assistant (PROJECT_SPEC.md layer 2: AI content on top of
deterministic handlers).

One free-form voice/text entry point (main.py's POST /assistant) first asks
Gemini to CLASSIFY the farmer's message -- never to answer it -- then routes to
one of a small, fixed set of HANDLERS. This is an INTENT CLASSIFIER -> HANDLERS
architecture, not a general chatbot: every handler either narrates real,
already-fetched data (crop rankings, live weather, mandi prices) or answers
under hard guardrails (fertiliser, general farming Q&A). Gemini never invents
facts, and every path here degrades to a deterministic, localized template on
failure (PROJECT_SPEC.md layer 3) -- a farmer never sees a raw error.
"""
import json

from google.genai import types

from config import GEMINI_MODEL, get_genai_client
from explain import LANGUAGE_NAMES, scheme_citations
from models import AssistantIntent

# --- intent classification ---------------------------------------------------

INTENT_SYSTEM_INSTRUCTION = """You are the intent router for KisanMate, a farm
assistant for smallholder farmers in India. Do NOT answer the farmer's message --
ONLY classify it, as strict JSON.

Classify into exactly one intent:
- "crop_recommendation": asking what/which crop to grow, plant, or rotate to next.
- "fertilizer_advice": asking specifically about fertiliser, manure, or soil
  nutrients for a crop (NOT pest or disease control -- that is general_farming_qa).
- "mandi_price": asking the market/mandi/selling price of a commodity.
- "weather_advice": asking about rain, weather, or irrigation timing.
- "general_farming_qa": any other genuine Indian-agriculture question, INCLUDING
  pest/disease control or treatment for a crop (e.g. "how do I control X in Y"),
  farming techniques, government agri schemes, soil health, livestock, etc.
- "off_topic": anything NOT about farming, crops, agriculture, agri-weather, or
  agri markets -- e.g. sports, entertainment, general trivia, creative-writing
  requests, or personal/medical/legal topics unrelated to farming.

Also extract, only if clearly stated or implied (else null):
- "crop": the crop mentioned, in lowercase English (e.g. "tomato").
- "commodity": the market commodity mentioned, in lowercase English (usually the
  same as crop, but kept separate since a mandi_price question may name a
  commodity without a "crop" of the farmer's own).
- "location": a place name mentioned (market, town, or district).

Set "on_topic" to false for anything not genuinely about farming/crops/
agri-weather/agri-markets, even if phrased politely.

Also return "lang": the language the farmer actually wrote in ("en", "hi", or "te").

Return ONLY JSON of this exact shape:
{"intent": "...", "on_topic": true|false, "crop": "..."|null, "commodity": "..."|null,
 "location": "..."|null, "lang": "en"|"hi"|"te"}"""


class IntentClassifyError(Exception):
    """Raised when Gemini fails to classify the assistant's intent."""


def classify_intent(text: str, lang: str) -> AssistantIntent:
    """Ask Gemini to classify (never answer) the farmer's free-form message.

    Raises IntentClassifyError on failure; the caller falls back to
    keyword_intent() below so classification always resolves to *some* intent,
    never a raw error (PROJECT_SPEC.md layer 3).
    """
    prompt = (
        f"{INTENT_SYSTEM_INSTRUCTION}\n\n"
        f"The farmer's app is currently set to: {lang}\n\n"
        f"Farmer's message:\n{json.dumps(text)}"
    )
    try:
        client = get_genai_client()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=AssistantIntent,
            ),
        )
        return AssistantIntent.model_validate(json.loads(response.text))
    except Exception as exc:
        raise IntentClassifyError(f"Gemini intent classification failed: {exc}") from exc


# Keyword heuristics used only when Gemini classification itself is unavailable.
_OFF_TOPIC_HINTS = [
    "cricket", "match", "movie", "film", "poem", "song", "joke", "election",
    "actor", "actress", "celebrity", "politics",
]

_INTENT_KEYWORDS = {
    "mandi_price": {
        "en": ["price", "rate", "mandi", "market price", "sell", "quintal"],
        "hi": ["भाव", "दाम", "मंडी", "कीमत", "रेट"],
        "te": ["ధర", "మార్కెట్", "రేటు", "మండి"],
    },
    "weather_advice": {
        "en": ["rain", "weather", "irrigat", "monsoon", "forecast"],
        "hi": ["बारिश", "मौसम", "सिंचाई"],
        "te": ["వర్షం", "వాతావరణం", "నీరు పెట్ట"],
    },
    "fertilizer_advice": {
        "en": ["fertiliser", "fertilizer", "nutrient", "urea", "manure", "npk", "compost"],
        "hi": ["खाद", "उर्वरक"],
        "te": ["ఎరువు"],
    },
    "crop_recommendation": {
        "en": ["grow", "plant", "recommend", "which crop", "what should i grow",
               "next crop", "rotation"],
        "hi": ["उगाएं", "फसल", "क्या उगाएं", "सुझाव"],
        "te": ["పండించాలి", "పంట", "సలహా"],
    },
}


def keyword_intent(text: str, lang: str) -> AssistantIntent:
    """Deterministic keyword-based intent classification -- the Gemini fallback.

    Explicit off-topic phrasing is refused; otherwise any farming keyword hit
    routes to that intent, and unmatched text defaults to general_farming_qa
    (which re-applies its own on-topic guard) rather than off_topic, so a
    missed keyword never silently refuses a real farming question.
    """
    lower = text.lower()
    if any(hint in lower for hint in _OFF_TOPIC_HINTS):
        return AssistantIntent(intent="off_topic", on_topic=False, lang=lang)

    for intent, by_lang in _INTENT_KEYWORDS.items():
        keywords = by_lang.get(lang, []) + by_lang.get("en", [])
        if any(kw.lower() in lower for kw in keywords):
            return AssistantIntent(intent=intent, on_topic=True, lang=lang)

    return AssistantIntent(intent="general_farming_qa", on_topic=True, lang=lang)


# --- fertilizer advice (guardrailed Gemini, general guidance only) ---------

FERTILIZER_SYSTEM_INSTRUCTION = """You are KisanMate, a farm advisor for
smallholder farmers in India, answering a fertiliser/soil-nutrient question.

Non-negotiable rules:
- Give GENERAL guidance only: nutrient categories (nitrogen/phosphorus/potassium,
  organic matter) and general timing, never a precise dose, brand, or product name.
- Always lead with getting a Soil Health Card / soil test before applying
  fertiliser, and prefer Integrated Pest Management (IPM) / cultural controls
  first for pests and disease.
- NEVER recommend a specific, banned, or restricted pesticide.
- Always defer the EXACT dose/product to the farmer's local RSK officer,
  agriculture extension worker, or their Soil Health Card recommendation.
- Never invent facts. Write in the requested language, short plain sentences
  that read well aloud, under 90 words total.
"""


class FertilizerAdviceError(Exception):
    """Raised when Gemini fails to produce fertiliser guidance."""


def fertilizer_advice(question: str, profile: dict, lang: str) -> dict:
    """Ask Gemini for guardrailed, general fertiliser/pest guidance.

    Returns {"answer_text": str, "citations": [{"title","uri"}]} -- citations are
    the real link for any government scheme (e.g. Soil Health Card) the answer
    names, so the recommendation isn't just a name the farmer has to remember.
    Raises FertilizerAdviceError on failure; the caller falls back to
    fallback_fertilizer_advice() below.
    """
    language_name = LANGUAGE_NAMES.get(lang, "English")
    prompt = (
        f"{FERTILIZER_SYSTEM_INSTRUCTION}\n\nRespond in: {language_name}\n\n"
        f"Farmer's field context:\n{json.dumps(profile)}\n\n"
        f"Farmer's question:\n{question}"
    )
    try:
        client = get_genai_client()
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = (response.text or "").strip()
        if not text:
            raise FertilizerAdviceError("Gemini returned an empty fertiliser answer")
        return {"answer_text": text, "citations": scheme_citations(text)}
    except FertilizerAdviceError:
        raise
    except Exception as exc:
        raise FertilizerAdviceError(f"Gemini fertiliser advice failed: {exc}") from exc


_FERTILIZER_FALLBACK = {
    "en": lambda crop: (
        f"For {crop or 'your crop'}: get a soil test (Soil Health Card) first, so you know what's "
        "actually low before adding fertiliser. In general, balance nitrogen, phosphorus, and "
        "potassium to the crop's growth stage, and prefer compost or organic manure where you can. "
        "For pests or disease, try IPM steps first (traps, resistant varieties, timely weeding). "
        "Confirm the exact product and dose with your local RSK officer or Krishi Vigyan Kendra."
    ),
    "hi": lambda crop: (
        f"{crop or 'आपकी फ़सल'} के लिए: पहले मिट्टी की जांच (Soil Health Card) करवाएं, ताकि पता चले "
        "असल में किस चीज़ की कमी है। आमतौर पर फ़सल की अवस्था के अनुसार नाइट्रोजन, फास्फोरस और पोटाश "
        "का संतुलन रखें, और जहां संभव हो जैविक खाद अपनाएं। कीट या बीमारी के लिए पहले IPM उपाय आज़माएं। "
        "सही दवा और मात्रा के लिए अपने नज़दीकी RSK अधिकारी या कृषि विज्ञान केंद्र से पुष्टि करें।"
    ),
    "te": lambda crop: (
        f"{crop or 'మీ పంట'} కోసం: ముందుగా మట్టి పరీక్ష (Soil Health Card) చేయించండి, దేని లోపం ఉందో "
        "తెలుసుకోవడానికి. సాధారణంగా పంట దశకు తగినట్టు నత్రజని, భాస్వరం, పొటాష్ సమతుల్యం చేయండి, వీలైతే "
        "సేంద్రియ ఎరువులు వాడండి. పురుగులు/వ్యాధుల కోసం ముందుగా IPM పద్ధతులు ప్రయత్నించండి. సరైన మందు, "
        "మోతాదు కోసం మీ సమీప RSK అధికారి లేదా కృషి విజ్ఞాన కేంద్రాన్ని సంప్రదించండి."
    ),
}


def fallback_fertilizer_advice(crop, lang: str) -> dict:
    """Deterministic fallback (Gemini unavailable) -- still soil-test-first and
    IPM-first, still defers the exact dose. Never calls Gemini."""
    fn = _FERTILIZER_FALLBACK.get(lang, _FERTILIZER_FALLBACK["en"])
    text = fn(crop)
    return {"answer_text": text, "citations": scheme_citations(text)}


# --- general farming Q&A (Gemini + Google Search grounding) -----------------

GENERAL_QA_SYSTEM_INSTRUCTION = """You are KisanMate, a farm advisor for
smallholder farmers in India. Answer ONLY questions about Indian agriculture:
crops, pests and diseases, soil, irrigation, weather's effect on farming,
government agri schemes, and farm markets. If the question is not really about
farming/agriculture, say so briefly and redirect the farmer -- do not answer it.

Rules you must always follow:
- Prefer Integrated Pest Management (IPM) / cultural and biological controls
  first; mention chemical control only in general terms, never a specific
  product, brand, or exact dose -- tell the farmer to confirm the exact
  product/dose with a local agriculture extension officer or Krishi Vigyan Kendra.
- NEVER recommend a banned or restricted pesticide.
- Never invent facts, figures, scheme names, or prices you are not given; hedge
  when unsure.
- Write in the requested language, in short plain sentences that read well aloud
  to someone with limited literacy. Keep the whole answer under 100 words.
"""

_HEDGE_SUFFIX = {
    "en": " (General guidance — please confirm locally.)",
    "hi": " (सामान्य सलाह — कृपया स्थानीय रूप से पुष्टि करें।)",
    "te": " (సాధారణ సలహా — దయచేసి స్థానికంగా నిర్ధారించుకోండి.)",
}


class FarmingQAError(Exception):
    """Raised only when BOTH the grounded and ungrounded Gemini attempts fail."""


def _extract_citations(response) -> list:
    """Up to 3 {title, uri} citations from the response's grounding metadata,
    or [] if there is none (never raises -- citations are a bonus, not load-bearing)."""
    citations = []
    try:
        for candidate in response.candidates or []:
            meta = getattr(candidate, "grounding_metadata", None)
            chunks = getattr(meta, "grounding_chunks", None) if meta else None
            for chunk in chunks or []:
                web = getattr(chunk, "web", None)
                if web and web.uri:
                    citations.append({"title": web.title or web.domain or web.uri, "uri": web.uri})
    except Exception:
        return []
    return citations[:3]


def _merge_citations(*groups: list) -> list:
    """Combine citation lists, de-duplicated by uri, first occurrence wins.
    Known government scheme links go first so they always survive the cap."""
    merged = []
    seen = set()
    for group in groups:
        for c in group or []:
            if c["uri"] not in seen:
                seen.add(c["uri"])
                merged.append(c)
    return merged[:4]


def general_farming_qa(question: str, lang: str) -> dict:
    """Ask Gemini, grounded in Google Search, for a general Indian-farming answer.

    Returns {"answer_text": str, "citations": [{"title","uri"}]}. If grounding
    is unavailable, retries once WITHOUT it (still guardrailed) and appends an
    explicit hedge. Raises FarmingQAError only if both attempts fail -- the
    caller then uses farming_qa_unavailable_text() below.
    """
    language_name = LANGUAGE_NAMES.get(lang, "English")
    prompt = (
        f"{GENERAL_QA_SYSTEM_INSTRUCTION}\n\nRespond in: {language_name}\n\n"
        f"Farmer's question:\n{question}"
    )
    try:
        client = get_genai_client()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )
        text = (response.text or "").strip()
        if not text:
            raise FarmingQAError("Gemini returned an empty grounded answer")
        citations = _merge_citations(scheme_citations(question, text), _extract_citations(response))
        return {"answer_text": text, "citations": citations}
    except Exception as grounded_exc:
        pass  # fall through to the ungrounded retry below

    try:
        client = get_genai_client()
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = (response.text or "").strip()
        if not text:
            raise FarmingQAError("Gemini returned an empty ungrounded answer")
        text += _HEDGE_SUFFIX.get(lang, _HEDGE_SUFFIX["en"])
        return {"answer_text": text, "citations": scheme_citations(question, text)}
    except Exception as ungrounded_exc:
        raise FarmingQAError(
            f"Gemini farming Q&A failed (grounded: {grounded_exc}; ungrounded: {ungrounded_exc})"
        ) from ungrounded_exc


# --- localized templates (no Gemini -- used for weather/mandi narration and
# every graceful-degradation message) ----------------------------------------

def weather_advice_text(forecast, lang: str) -> str:
    """Narrate a live weather.Forecast directly -- the numbers are real and
    speak for themselves, so no Gemini call is needed (and none of the
    hallucination risk that would come with one)."""
    mm = forecast.precip_next7_mm
    pct = forecast.rain_prob_max_pct
    if forecast.dry_spell:
        templates = {
            "en": f"No significant rain expected over the next 7 days (about {mm:.0f} mm, "
                  f"up to {pct:.0f}% chance) — plan to irrigate.",
            "hi": f"अगले 7 दिनों में उल्लेखनीय बारिश की उम्मीद नहीं है (करीब {mm:.0f} मिमी, अधिकतम "
                  f"{pct:.0f}% संभावना) — सिंचाई की योजना बनाएं।",
            "te": f"రాబోయే 7 రోజుల్లో గణనీయమైన వర్షం పడే అవకాశం లేదు (సుమారు {mm:.0f} మిమీ, గరిష్టంగా "
                  f"{pct:.0f}% అవకాశం) — నీరు పెట్టడానికి ప్రణాళిక వేసుకోండి.",
        }
    else:
        templates = {
            "en": f"Rain is likely in the next 7 days (about {mm:.0f} mm expected, up to "
                  f"{pct:.0f}% chance) — you may be able to hold off on irrigation.",
            "hi": f"अगले 7 दिनों में बारिश की संभावना है (करीब {mm:.0f} मिमी, अधिकतम {pct:.0f}% "
                  f"संभावना) — सिंचाई थोड़ी रोक सकते हैं।",
            "te": f"రాబోయే 7 రోజుల్లో వర్షం పడే అవకాశం ఉంది (సుమారు {mm:.0f} మిమీ, గరిష్టంగా "
                  f"{pct:.0f}% అవకాశం) — నీరు పెట్టడం కొంచెం ఆపవచ్చు.",
        }
    return templates.get(lang, templates["en"])


def weather_advice_unavailable_text(lang: str) -> str:
    templates = {
        "en": "I couldn't reach the live weather service right now. Please try again in a little while.",
        "hi": "अभी मौसम की जानकारी नहीं मिल पा रही। कृपया थोड़ी देर बाद फिर कोशिश करें।",
        "te": "ప్రస్తుతం వాతావరణ సమాచారం అందుబాటులో లేదు. దయచేసి కొద్దిసేపటి తర్వాత మళ్ళీ ప్రయత్నించండి.",
    }
    return templates.get(lang, templates["en"])


def mandi_price_text(price: dict, lang: str) -> str:
    """State the fetched price verbatim -- never rephrased by Gemini."""
    commodity = price.get("commodity") or ""
    market = price.get("market") or price.get("district") or price.get("state") or ""
    date = price.get("arrival_date") or ""
    modal = price.get("modal_price_per_quintal") or 0.0
    templates = {
        "en": f"The modal price for {commodity} in {market} on {date} was ₹{modal:.0f} per quintal.",
        "hi": f"{market} में {date} को {commodity} का औसत भाव ₹{modal:.0f} प्रति क्विंटल था।",
        "te": f"{market}లో {date} నాడు {commodity} సగటు ధర క్వింటాల్‌కు ₹{modal:.0f}.",
    }
    return templates.get(lang, templates["en"])


def mandi_no_rate_text(commodity, lang: str) -> str:
    templates = {
        "en": f"No rate available today for {commodity or 'that commodity'}. Please try again later.",
        "hi": f"{commodity or 'इस वस्तु'} के लिए आज कोई भाव उपलब्ध नहीं है। कृपया बाद में फिर कोशिश करें।",
        "te": f"{commodity or 'ఈ వస్తువు'}కు ఈరోజు ధర అందుబాటులో లేదు. దయచేసి తర్వాత మళ్ళీ ప్రయత్నించండి.",
    }
    return templates.get(lang, templates["en"])


def mandi_need_commodity_text(lang: str) -> str:
    templates = {
        "en": "Please tell me which crop's price you'd like to know.",
        "hi": "कृपया बताएं आप किस फ़सल का भाव जानना चाहते हैं।",
        "te": "దయచేసి ఏ పంట ధర తెలుసుకోవాలనుకుంటున్నారో చెప్పండి.",
    }
    return templates.get(lang, templates["en"])


def off_topic_refusal_text(lang: str) -> str:
    templates = {
        "en": "I can only help with farming — crops, fertiliser, weather, mandi prices, and crop "
              "problems. What would you like to know about your farm?",
        "hi": "मैं सिर्फ़ खेती से जुड़ी मदद कर सकता हूँ — फ़सल, खाद, मौसम, मंडी भाव और फ़सल की समस्याएं। "
              "आप अपने खेत के बारे में क्या जानना चाहेंगे?",
        "te": "నేను వ్యవసాయానికి సంబంధించిన విషయాల్లో మాత్రమే సహాయం చేయగలను — పంటలు, ఎరువులు, "
              "వాతావరణం, మండి ధరలు, పంట సమస్యలు. మీ పొలం గురించి ఏమి తెలుసుకోవాలనుకుంటున్నారు?",
    }
    return templates.get(lang, templates["en"])


def farming_qa_unavailable_text(lang: str) -> str:
    templates = {
        "en": "I couldn't find a good answer right now. Please try again, or ask your local RSK officer.",
        "hi": "अभी अच्छा जवाब नहीं मिल पाया। कृपया फिर कोशिश करें, या अपने नज़दीकी RSK अधिकारी से पूछें।",
        "te": "ప్రస్తుతం సరైన సమాధానం దొరకలేదు. దయచేసి మళ్ళీ ప్రయత్నించండి, లేదా మీ సమీప RSK అధికారిని అడగండి.",
    }
    return templates.get(lang, templates["en"])
