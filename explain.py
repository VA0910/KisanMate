"""Explain module (PROJECT_SPEC.md layer 2: AI content, on top of the deterministic core).

Turns an already-decided FusionOutput into a warm, plain-language message for the
farmer. It only narrates the fusion decision -- it never changes the diagnosis,
confidence, or decision. If Gemini fails, template_message() is the deterministic
fallback (layer 1 / layer 3 of the spec: templates + silent fallback).
"""
import json

from config import GEMINI_MODEL, get_genai_client
from models import CropRecommendation, FusionContext, FusionOutput

LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "te": "Telugu"}

# Official government scheme portals. Whenever a farmer-facing answer names one
# of these schemes (in any supported language), the real link is attached as a
# citation -- so a "recommendation" never leaves the farmer with just a name to
# remember. Kept separate from Google-Search grounding (assistant.py) since
# these three are known, stable, official URLs that should never depend on a
# search call succeeding.
GOVT_SCHEME_LINKS = [
    {
        "title": "PM-KISAN (Kisan Nidhi)",
        "uri": "https://pmkisan.gov.in/",
        "keywords": [
            "pm-kisan", "pm kisan", "kisan nidhi", "kisan samman nidhi",
            "किसान निधि", "किसान सम्मान निधि", "पीएम-किसान", "पीएम किसान",
            "కిసాన్ నిధి", "పీఎం-కిసాన్", "పీఎం కిసాన్",
        ],
    },
    {
        "title": "Crop Insurance (PMFBY / Kisan Beema)",
        "uri": "https://pmfby.gov.in/",
        "keywords": [
            "pmfby", "fasal bima", "crop insurance", "kisan beema", "kisan bima",
            "फसल बीमा", "किसान बीमा", "पीएमएफबीवाई",
            "పంట బీమా", "కిసాన్ బీమా", "పీఎంఎఫ్‌బీవై",
        ],
    },
    {
        "title": "Soil Health Card",
        "uri": "https://soilhealth.dac.gov.in/",
        "keywords": [
            "soil health card", "soil health scheme",
            "मृदा स्वास्थ्य कार्ड", "मिट्टी स्वास्थ्य कार्ड",
            "నేల ఆరోగ్య కార్డు", "మట్టి ఆరోగ్య కార్డు",
        ],
    },
]


def scheme_citations(*texts: str) -> list:
    """Scan farmer-facing text for a mention of a known government scheme and
    return {"title", "uri"} citations for every scheme actually named, so a
    recommendation that names a scheme always comes with its real link.

    Order-preserving, de-duplicated by uri, never raises (worst case: []).
    """
    haystack = " ".join(t for t in texts if t).lower()
    if not haystack:
        return []
    found = []
    for scheme in GOVT_SCHEME_LINKS:
        if any(kw.lower() in haystack for kw in scheme["keywords"]):
            found.append({"title": scheme["title"], "uri": scheme["uri"]})
    return found

# Localized soil-type labels, so even the deterministic fallbacks (no Gemini)
# still address the farmer's own soil rather than a generic default.
SOIL_LABELS = {
    "en": {"black": "black", "red": "red", "alluvial": "alluvial", "loam": "loam",
           "loamy": "loam", "sandy": "sandy", "clay": "clay"},
    "hi": {"black": "काली", "red": "लाल", "alluvial": "जलोढ़", "loam": "दोमट",
           "loamy": "दोमट", "sandy": "रेतीली", "clay": "चिकनी"},
    "te": {"black": "నల్ల", "red": "ఎర్ర", "alluvial": "ఒండ్రు", "loam": "గరప",
           "loamy": "గరప", "sandy": "ఇసుక", "clay": "బంకమట్టి"},
}


def _soil_label(soil_type, language):
    if not soil_type:
        return None
    return SOIL_LABELS.get(language, SOIL_LABELS["en"]).get(soil_type, soil_type)


def _stage_label(stage):
    return stage.replace("_", " ") if stage else None


def _context_facts(context):
    """A compact dict of the farmer's field context for a Gemini prompt.

    This is SUPPORTING information the model may weave into the "why" when
    relevant -- it is never a mandatory prefix (see EXPLAIN_SYSTEM_INSTRUCTION)."""
    if context is None:
        return None
    facts = {}
    if getattr(context, "crop", None):
        facts["crop"] = context.crop
    soil_type = getattr(getattr(context, "soil", None), "type", None)
    if soil_type:
        facts["soil_type"] = soil_type
    if getattr(context, "growth_stage", None):
        facts["growth_stage"] = _stage_label(context.growth_stage)
    if getattr(context, "crop_day", None) is not None:
        facts["days_since_planting"] = context.crop_day
    if getattr(context, "season", None):
        facts["season"] = context.season
    weather = getattr(context, "weather", None)
    if weather is not None:
        facts["weather"] = {
            "temp_c": weather.temp_c,
            "humidity_pct": weather.humidity_pct,
            "rain_48h_mm": weather.rain_48h_mm,
        }
    if getattr(context, "susceptible_diseases", None):
        facts["crop_susceptible_diseases"] = context.susceptible_diseases
    return facts or None


def recommendation_note(context, language):
    """A localized one-line note for the recommendation result that references the
    farmer's soil and (if known) their current crop's growth stage."""
    if context is None:
        return ""
    soil = _soil_label(getattr(getattr(context, "soil", None), "type", None), language)
    stage = _stage_label(getattr(context, "growth_stage", None))
    crop = getattr(context, "crop", None)
    if language == "hi":
        note = ""
        if crop and stage:
            note += f"आपकी {crop} फ़सल अभी {stage} अवस्था में है। "
        note += f"ये आपकी {soil} मिट्टी के लिए अच्छी हैं:" if soil else "ये फ़सलें आपके लिए अच्छी हैं:"
        return note
    if language == "te":
        note = ""
        if crop and stage:
            note += f"మీ {crop} పంట ప్రస్తుతం {stage} దశలో ఉంది. "
        note += f"ఇవి మీ {soil} నేలకు బాగుంటాయి:" if soil else "ఇవి మీకు బాగుంటాయి:"
        return note
    note = ""
    if crop and stage:
        note += f"Your {crop} is at the {stage} stage. "
    note += f"These suit your {soil} soil:" if soil else "These suit your field:"
    return note

EXPLAIN_SYSTEM_INSTRUCTION = """You are KisanMate, a friendly farm assistant speaking to a
smallholder farmer in India. You are given a crop diagnosis result ALREADY decided by a separate
rule-based system. Explain it warmly and simply -- do not diagnose anything yourself, and never
change the given condition, confidence, or decision.

Return a STRUCTURED answer as JSON with three fields:
- "what": what the condition is, in plain words (one or two short sentences).
- "why": the reason, drawn from the visible symptoms and any field context that is actually relevant.
- "what_to_do": the next step.

Rules you must always follow:
- Write only in the requested language, in short plain sentences that read aloud well to someone with
  limited literacy. Keep the three fields together under 80 words.
- If the decision is "advise" (the system is CONFIDENT): give direct, confident advice. Do NOT mention
  any RSK officer, and do NOT use "we're not sure" / hedging language.
- If the decision is "escalate_rsk" (the system is UNSURE): say kindly that a local RSK (Raitha Saradhi
  Kendra) officer will take a closer look before the farmer acts, and do not state the diagnosis as if certain.
- Weave the farmer's field context into the WHY naturally, and ONLY when it is actually relevant to this
  condition (for example, mention soil for a nutrient deficiency). NEVER begin the message with
  "For your <soil> soil" or any mechanical list of context. Use only the real soil/context you are given;
  never invent, mismatch, or change it.
- If you mention spraying a pesticide/fungicide: tell the farmer to check the weather first (not before
  rain or in high wind) and to confirm the exact product and dose with an agriculture extension worker.
  Never state a specific chemical name, brand, or dose yourself.
"""

# Deterministic fallback templates (layer 1), used only if the Gemini call fails.
# Structured into what / why / what_to_do so the UI can render them cleanly.
# "advise" is the CONFIDENT path: no RSK officer mention, no hedging.
_TEMPLATE_PARTS = {
    "en": {
        "advise": {
            "what": "We checked your crop and the result looks clear.",
            "why": "This is based on the signs seen in your photo.",
            "what_to_do": (
                "Follow the care steps for this condition. If you plan to spray, check the "
                "weather first and use the right product and dose for it."
            ),
        },
        "escalate_rsk": {
            "what": "This case needs a closer look.",
            "why": "We are not fully sure from the photo alone.",
            "what_to_do": (
                "We've sent this photo to your local RSK officer, who will confirm it and "
                "guide your next step."
            ),
        },
    },
    "hi": {
        "advise": {
            "what": "हमने आपकी फ़सल की जांच की और नतीजा साफ़ है।",
            "why": "यह आपकी फोटो में दिखे लक्षणों पर आधारित है।",
            "what_to_do": (
                "इस समस्या के लिए बताए गए उपाय अपनाएं। अगर दवा छिड़कनी हो तो पहले मौसम देखें और "
                "सही दवा व सही मात्रा का उपयोग करें।"
            ),
        },
        "escalate_rsk": {
            "what": "इस मामले में और जांच ज़रूरी है।",
            "why": "अकेले फोटो से हमें पूरा भरोसा नहीं है।",
            "what_to_do": (
                "हमने यह फोटो आपके नज़दीकी RSK अधिकारी को भेज दिया है, जो इसकी पुष्टि करके सही सलाह देंगे।"
            ),
        },
    },
    "te": {
        "advise": {
            "what": "మేము మీ పంటను పరిశీలించాం, ఫలితం స్పష్టంగా ఉంది.",
            "why": "ఇది మీ ఫోటోలో కనిపించిన లక్షణాల ఆధారంగా ఉంది.",
            "what_to_do": (
                "ఈ సమస్యకు సూచించిన జాగ్రత్తలు పాటించండి. మందు పిచికారీ చేయాలంటే ముందు వాతావరణం చూసి, "
                "సరైన మందు, సరైన మోతాదు వాడండి."
            ),
        },
        "escalate_rsk": {
            "what": "ఈ విషయంలో మరింత పరిశీలన అవసరం.",
            "why": "ఫోటో మాత్రమే చూసి మాకు పూర్తి నమ్మకం లేదు.",
            "what_to_do": (
                "ఈ ఫోటోను మీ సమీప RSK అధికారికి పంపాము; వారు దీన్ని నిర్ధారించి సరైన సలహా ఇస్తారు."
            ),
        },
    },
}


def combine_explanation(explanation: dict) -> str:
    """Flatten a structured explanation into one string (for text-to-speech)."""
    if not explanation:
        return ""
    parts = [explanation.get("what"), explanation.get("why"), explanation.get("what_to_do")]
    return " ".join(p.strip() for p in parts if p and p.strip())


def template_explanation(decision: str, language: str) -> dict:
    """Deterministic structured fallback ({what, why, what_to_do}); never calls Gemini."""
    by_lang = _TEMPLATE_PARTS.get(language, _TEMPLATE_PARTS["en"])
    return dict(by_lang.get(decision, by_lang["escalate_rsk"]))


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
- You are given the farmer's field context (their soil type, and their current crop's growth
  stage). Reference the farmer's soil naturally in the reasons so they feel personal.
- Keep each reason under 20 words.
- Return ONLY a JSON object mapping each crop id to its reason string, nothing else."""


def explain_recommendations(
    recommendations: list[CropRecommendation], language: str, context: FusionContext = None
) -> dict:
    """Ask Gemini to write a warm reason per ranked crop, personalized to the
    farmer's field context. Raises RecommendExplainError on failure.

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
    facts = _context_facts(context)
    context_block = (
        f"\n\nFarmer's field context (reference their soil naturally):\n{json.dumps(facts)}"
        if facts
        else ""
    )
    prompt = (
        f"{RECOMMEND_SYSTEM_INSTRUCTION}\n\n"
        f"Respond in: {language_name}"
        f"{context_block}\n\n"
        f"Crops and the criteria each matched (JSON):\n{json.dumps(items)}"
    )
    try:
        client = get_genai_client()
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


# --- conversational crop recommendation (voice-first) ----------------------------

SEASON_LABELS = {
    "en": {"kharif": "kharif (monsoon)", "rabi": "rabi (winter)", "zaid": "zaid (summer)"},
    "hi": {"kharif": "खरीफ़ (मानसून)", "rabi": "रबी (सर्दी)", "zaid": "ज़ायद (गर्मी)"},
    "te": {"kharif": "ఖరీఫ్ (వర్షాకాలం)", "rabi": "రబీ (శీతాకాలం)", "zaid": "జాయద్ (వేసవి)"},
}


def _season_label(season, language):
    if not season:
        return None
    return SEASON_LABELS.get(language, SEASON_LABELS["en"]).get(season, season)


RECOMMEND_CONVO_SYSTEM_INSTRUCTION = """You are KisanMate, a warm farm advisor talking WITH a
smallholder farmer in India -- a conversation, not a form. The farmer has asked a free-form
question (voice or text). Answer it specifically and practically.

You are given:
- the farmer's question,
- their field context (soil type, location, current season + weather, and their current/recent crops),
- a GROUNDED list of candidate crops from our database (this is the ONLY set you may recommend from).

Rules you must always follow:
- Recommend ONE or TWO specific crops, chosen ONLY from the grounded candidate list. NEVER invent a
  crop or recommend one that is not in that list.
- For each recommended crop, give a clear "why" that references, where relevant: the farmer's SOIL, the
  current SEASON/WEATHER, and crop ROTATION from their previous/current crop (e.g. a legume after a
  heavy feeder like tomato). Ground it in the candidate data; do not invent yields, prices, or chemicals.
- Also write a short "spoken" answer (2-3 short sentences) that sounds like a helpful conversation and
  reads well aloud, in the requested language.
- Write in the requested language, in plain words for limited literacy.
- Return ONLY JSON of this exact shape, nothing else:
  {"recommendations":[{"crop_id":"<id from the list>","crop_name":"<localized name>","why":"..."}],
   "spoken":"..."}"""


def recommend_conversational(question, profile, grounding, language):
    """Ask Gemini for a specific, grounded, rotation-aware recommendation.

    `grounding` is the candidate crop shortlist (the ONLY crops the model may
    pick). Returns {"recommendations":[{crop_id,crop_name,why}], "spoken": str},
    filtered to the grounded crop ids. Raises RecommendExplainError on failure so
    the caller can fall back to the deterministic recommendation.
    """
    language_name = LANGUAGE_NAMES.get(language, "English")
    allowed = {c.get("crop_id") for c in grounding}
    prompt = (
        f"{RECOMMEND_CONVO_SYSTEM_INSTRUCTION}\n\n"
        f"Respond in: {language_name}\n\n"
        f"Farmer's question:\n{json.dumps(question)}\n\n"
        f"Field context:\n{json.dumps(profile)}\n\n"
        f"Grounded candidate crops (recommend ONLY from these):\n{json.dumps(grounding)}"
    )
    try:
        client = get_genai_client()
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = (response.text or "").strip()
        if not text:
            raise RecommendExplainError("Gemini returned an empty recommendation")
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"):]
        data = json.loads(text)
        recs_in = data.get("recommendations") if isinstance(data, dict) else None
        if not isinstance(recs_in, list):
            raise RecommendExplainError("Gemini recommendation was not the expected shape")

        recs = []
        for r in recs_in:
            if not isinstance(r, dict):
                continue
            cid = str(r.get("crop_id", "")).strip()
            # Enforce grounding: silently drop any hallucinated crop.
            if cid and cid not in allowed:
                continue
            why = str(r.get("why", "")).strip()
            name = str(r.get("crop_name", "")).strip() or cid
            if cid and why:
                recs.append({"crop_id": cid, "crop_name": name, "why": why})
        if not recs:
            raise RecommendExplainError("Gemini recommended nothing from the grounded list")

        spoken = str(data.get("spoken", "")).strip() or " ".join(r["why"] for r in recs)
        return {"recommendations": recs[:2], "spoken": spoken}
    except RecommendExplainError:
        raise
    except Exception as exc:
        raise RecommendExplainError(f"Gemini conversational recommend failed: {exc}") from exc


def fallback_recommendation(candidates, soil_type, season, prev_crop_name, language):
    """Deterministic recommendation when Gemini is unavailable.

    `candidates` are crop dicts {crop_id, names(dict)} already ranked for the
    field. Builds the same {recommendations, spoken} shape with a localized,
    grounded template "why" (soil + season + rotation), so voice/UI are identical
    to the AI path.
    """
    soil = _soil_label(soil_type, language)
    season_txt = _season_label(season, language)
    picks = candidates[:2]
    recs = []
    for c in picks:
        names = c.get("names") or {}
        name = names.get(language) or names.get("en") or c.get("crop_id")
        recs.append({"crop_id": c.get("crop_id"), "crop_name": name, "why": _fallback_why(soil, season_txt, prev_crop_name, language)})

    if language == "hi":
        lead = "आपके खेत के लिए ये अच्छे विकल्प हैं: "
    elif language == "te":
        lead = "మీ పొలానికి ఇవి మంచి ఎంపికలు: "
    else:
        lead = "Good options for your field: "
    spoken = lead + ", ".join(r["crop_name"] for r in recs) + "."
    return {"recommendations": recs, "spoken": spoken}


def _fallback_why(soil, season_txt, prev_crop, language):
    if language == "hi":
        parts = []
        if soil:
            parts.append("आपकी " + soil + " मिट्टी")
        if season_txt:
            parts.append("अभी के " + season_txt + " मौसम")
        why = ("इनके लिए अच्छी: " + " और ".join(parts)) if parts else "आपके खेत के लिए अच्छी"
        if prev_crop:
            why += "; " + prev_crop + " के बाद अच्छी अदला-बदली"
        return why + "।"
    if language == "te":
        parts = []
        if soil:
            parts.append("మీ " + soil + " నేలకు")
        if season_txt:
            parts.append("ప్రస్తుత " + season_txt + " సీజన్‌కు")
        why = ("వీటికి బాగుంటుంది: " + ", ".join(parts)) if parts else "మీ పొలానికి బాగుంటుంది"
        if prev_crop:
            why += "; " + prev_crop + " తర్వాత మంచి మార్పిడి"
        return why + "."
    parts = []
    if soil:
        parts.append("your " + soil + " soil")
    if season_txt:
        parts.append("the current " + season_txt + " season")
    why = ("Suited to " + " and ".join(parts)) if parts else "A solid choice for your field"
    if prev_crop:
        why += ", and a healthy rotation after " + prev_crop
    return why + "."


def template_message(decision: str, language: str, context=None) -> str:
    """Deterministic per-language fallback message (a single combined string).

    Confident for "advise" (no officer mention), escalation for "escalate_rsk".
    Never prefixed with soil/stage. `context` is accepted for a stable signature
    but intentionally unused. Used for text-to-speech and the last-resort paths.
    """
    return combine_explanation(template_explanation(decision, language))


def explain_fusion(
    fusion: FusionOutput,
    language: str,
    context: FusionContext = None,
    visible_symptoms: list = None,
) -> dict:
    """Ask Gemini to narrate an already-decided fusion result as a STRUCTURED
    {what, why, what_to_do} answer, weaving in relevant field context. Raises
    ExplainError on failure (the caller falls back to template_explanation)."""
    language_name = LANGUAGE_NAMES.get(language, "English")
    facts = _context_facts(context)
    context_block = (
        f"\n\nFarmer's field context (weave into WHY only when relevant; do not list it, "
        f"do not open with it):\n{json.dumps(facts)}"
        if facts
        else ""
    )
    symptoms_block = (
        f"\n\nVisible symptoms the camera saw (use these for WHY): {json.dumps(visible_symptoms)}"
        if visible_symptoms
        else ""
    )
    prompt = (
        f"{EXPLAIN_SYSTEM_INSTRUCTION}\n\n"
        f"Respond in: {language_name}"
        f"{context_block}"
        f"{symptoms_block}\n\n"
        f'Return ONLY JSON of this exact shape: {{"what": "...", "why": "...", "what_to_do": "..."}}\n\n'
        f"Diagnosis result (JSON, for your reference only -- do not repeat it verbatim):\n"
        f"{fusion.model_dump_json()}"
    )
    try:
        client = get_genai_client()
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = (response.text or "").strip()
        if not text:
            raise ExplainError("Gemini returned an empty explanation")
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"):]
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ExplainError("Gemini explanation was not a JSON object")
        explanation = {k: str(data.get(k, "")).strip() for k in ("what", "why", "what_to_do")}
        if not any(explanation.values()):
            raise ExplainError("Gemini explanation had no content")
        return explanation
    except ExplainError:
        raise
    except Exception as exc:
        raise ExplainError(f"Gemini explain call failed: {exc}") from exc
