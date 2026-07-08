# PROJECT_SPEC.md

## What this is
KisanMate: a voice-first web app for small/marginal farmers in India, in English, Hindi, and Telugu. It runs as ONE FastAPI app on Cloud Run that serves both a static frontend and a JSON API. Database is Firestore. AI is Google Gemini via the google-genai SDK.

## Non-negotiable architecture (the four layers)
1. Deterministic shell: the app's core logic (crop scoring, disease-risk rules, fusion math, alert propagation) is plain Python that runs with NO AI calls. If every AI call fails, the farmer still gets a useful answer from templates.
2. AI content layer: Gemini adds natural-language understanding and explanation on top of the deterministic core. It never replaces the core.
3. Silent fallbacks: when anything fails (bad AI output, missing data, unsupported language), degrade gracefully to the deterministic layer and LOG the event to a telemetry store. Never show the farmer an error or a stack trace.
4. Human override: the farmer can correct any inferred field; an RSK officer can confirm or override any AI diagnosis, and the officer's verdict is authoritative.

## Tech constraints
- Use the `google-genai` package (`from google import genai`). NEVER use the deprecated `google-generativeai`.
- Firestore via `google-cloud-firestore` (`from google.cloud import firestore`). On Cloud Run it auto-authenticates — no key files.
- The app must listen on port 8080.
- The Gemini API key comes from the GEMINI_API_KEY environment variable. Never hardcode secrets.
- Frontend is plain HTML/CSS/JavaScript — NO build step, NO framework, NO npm. Served by FastAPI as static files. It must be lightweight enough for a low-end Android phone on a slow connection.

## Firestore data model
- farmers/{id}: name, phone, lang ("en"|"hi"|"te"), location {lat, lng, mandal}, crop, land_size_acres, growth_stage, soil_type, current_crops [{crop_id, planting_date}]
- crops/{crop_id}: names {en, hi, te}, seasons [kharif|rabi|zaid], soil_types [black|red|alluvial|loam|sandy|clay], water_need [low|medium|high], regions [], cycle_days (int), growth_stages [{name, start_day, care_note}], susceptible_diseases [condition ids e.g. "late_blight"]
- cases/{id}: farmer_id, image_note, vision (see contract), context (see contract), fusion (see contract), status ("pending"|"advised"|"escalated"|"disputed"|"confirmed"), officer_verdict, condition, contagious (bool), created_at
  - "disputed": set when the farmer taps "This isn't right"; surfaces in the officer portal's "Farmer disputed" category. It is NOT an officer verdict (see "Farmer dispute vs officer verdict").
- alerts/{id}: source_case_id, condition, tier ("watch"|"warning"|"alert"), center {lat, lng}, radius_km, recipient_ids [], created_at
- telemetry/{id}: event, layer, detail, fallback_used (bool), created_at

### Farmer profile additions
- `soil_type` and `current_crops [{crop_id, planting_date}]` are added to farmers/{id}. Planting date is REQUIRED per crop and is what makes memory/reminders possible.

### Crops collection (new)
The single `crops/{crop_id}` collection feeds four things: crop recommendations (match soil/season/region), the diagnosis prior and community-alert crop filter (via `susceptible_diseases`), and reminders (via `cycle_days` + `growth_stages`).

## Identity & authentication (replaces the `?farmer=` hack)
- Farmers sign in with phone number + OTP. Farmers have no email and can't create one, so phone is the identity.
- OTP is MOCKED for the prototype: the request-OTP step generates a 4-digit code and returns it so the UI can display it as "Demo OTP: XXXX" (an unattended judge must be able to proceed without a real SMS). Verify checks against the generated code. SMS delivery is the named production step.
- First-time phone number -> create a farmer and go to profile setup. Returning phone -> go straight home. Session persists in localStorage until explicit sign-out; no re-signin until then.
- CRITICAL: keep the existing farmer document IDs (ramesh, lakshmi, venkat, sita) UNCHANGED. Add a `phone` field to each. Auth resolves phone -> existing document. Do NOT rename document IDs or change how cases/alerts reference `farmer_id`, or the demo scenario and hero targeting break.

## Location (device geolocation, NOT Earth Engine)
- Capture real location via the browser's `navigator.geolocation` API and save {lat, lng} to the farmer profile.
- Fallback (silent, logged): on permission denied / error / timeout, show a district picker defaulting to Guntur. This is the location layer's human-override + silent-fallback.

## Memory + proactive reminders
- Deterministic: from a crop's `planting_date` + the crop's `cycle_days` and `growth_stages`, compute the current stage and generate reminders (irrigation cadence from `water_need`, stage care notes, harvest window). Real date math, no user trigger.
- Surface them proactively on the home screen when the app opens.
- Production trigger is named as Cloud Scheduler -> an SMS-push endpoint; the prototype computes and shows them on load.

## Profile page (user-side human override)
A screen where the farmer views and edits location, soil_type, current_crops (add/remove, each with planting_date), language, and signs out. Changes persist and re-feed everything downstream.

## Context into Gemini
Build a `context` object from the profile (location, soil, current crop + computed growth stage) matching the existing fusion context contract, and pass it to every Gemini call (diagnosis, recommendation, explanation). Outputs must visibly use it.

## Cinematic intro
After language selection, an auto-playing scripted scene sequence tells the hero story using the real UI components with pre-staged (NOT live-AI) content, then fades into login. Skippable, plays once (localStorage flag), with a "Watch intro" link on login.

## Vision output contract (Gemini must return ONLY this JSON)
{ "image_quality": "good|poor", "crop_confirmed": "tomato|uncertain|<other>",
  "identified_crop": "<crop the model sees in the photo, or 'unidentifiable'>",
  "matches_profile": true,
  "candidates": [ { "condition": "late_blight|early_blight|nitrogen_deficiency|other|healthy", "confidence": 0.0, "visible_symptoms": [] } ],
  "notes": "" }
- `identified_crop`: the plant/crop the model identifies in the photo (vision identifies the plant FIRST). "unidentifiable" when it cannot tell.
- `matches_profile`: true when `identified_crop` matches one of the farmer's `current_crops`. See "Diagnosis — plant identification & multi-crop".

## Fusion input contract
{ "vision": {<vision output>},
  "context": { "crop": "tomato", "location": {"lat":0,"lng":0,"resolution":"plot|village|mandal|zone"},
    "weather": {"temp_c":0,"humidity_pct":0,"rain_48h_mm":0,"source":"live|cache|zone_normal"},
    "soil": {"nitrogen":"low|adequate|unknown","source":"card|cache|unknown"},
    "nearby_confirmed": [ {"condition":"late_blight","distance_km":0,"age_days":0} ] } }

## Fusion output contract
{ "posterior": [ {"condition":"late_blight","score":0.0,"contagious":true} ],
  "top": "late_blight", "confidence": 0.0, "margin": 0.0, "conflict": false,
  "decision": "advise|escalate_rsk", "alert_eligible": true,
  "evidence": { "vision_top": "", "prior_top": "", "context_completeness": [] } }

## Prior table (tomato) — deterministic
prior_score = base + sum(weight * factor_active), then normalize across the three conditions.
Factor -> late_blight / early_blight / nitrogen_deficiency
- base: 1.0 / 1.0 / 1.0
- cool temp 10-20C: +2.0 / 0 / 0
- warm temp 27-35C: -1.5 / +1.5 / 0
- humidity >85%: +2.0 / +1.5 / 0
- rain/leaf-wetness last 48h: +1.5 / +0.5 / 0
- low soil nitrogen: 0 / 0 / +3.0
- nearby confirmed SAME condition: +3.0 / +3.0 / 0
- contagious: yes / yes / NO
posterior(condition) proportional to vision_confidence(condition) * normalized_prior(condition), renormalized.

## Decision rules (in Python, deterministic and auditable)
- decision = "escalate_rsk" if confidence < 0.55 OR margin < 0.15 OR conflict OR image_quality == "poor" OR the plant is unidentifiable (`identified_crop` == "unidentifiable"); else "advise".
- conflict = true when vision_top != prior_top AND both are individually strong (never silently override — escalate).
- Escalate ONLY on genuine uncertainty (the conditions above). Contagiousness alone must NOT escalate the farmer's result — a confident diagnosis of a contagious disease is still "advise" to the farmer.
- alert_eligible = the top condition's contagious flag. This marks candidacy only; the alert engine still applies the confirmation gate before firing. `alert_eligible` / contagiousness must not feed back into the farmer's `decision` or confidence.

## Diagnosis — confidence & escalation (fixes a current bug)
- The farmer-facing result MUST be consistent with confidence:
  - decision = "advise" (confident): show HIGH confidence and give direct, confident advice — with NO mention of the officer and NO "we're not sure" language.
  - decision = "escalate_rsk" (uncertain): show an uncertain/medium confidence indicator and the "needs a closer look — sent to your RSK officer" message.
- HIGH confidence together with "sent to the officer" must NEVER appear together.
- Community alerts stay officer-gated, but that gating is a BACKGROUND step on the officer portal. It must never change or downgrade the farmer's own confident result.

## Diagnosis — context use
- Diagnosis takes the photo PLUS profile context: soil type, location, current weather/season (derived from today's date + location), and current crops. All of it is passed to vision, fusion, and the explanation.
- Context is supporting information woven into the "why", NEVER a forced prefix. Do NOT begin messages with "For your <soil> soil —". Mention soil only when it is actually relevant to the condition (e.g. nutrient deficiency), and always use the real profile soil — never fabricate or mismatch it.
- The explanation is well-structured: WHAT the condition is, WHY (from the visible symptoms + relevant context), and WHAT TO DO.

## Diagnosis — plant identification & multi-crop
- Vision identifies the plant/crop in the photo FIRST, populating `identified_crop` and `matches_profile` (see the vision contract).
- If `identified_crop` matches one of the farmer's `current_crops`: proceed with that crop's disease profile.
- If it does NOT match any of the farmer's crops: Gemini's identified plant is used, and the diagnosis proceeds using that identified plant's disease profile from the crops DB (or a general assessment if the plant isn't in the DB).
- If the plant is unidentifiable: escalate (per the decision rules above).

## Community alert engine (the hero)
A confirmed contagious case fires anonymized, crop-filtered alerts to nearby farmers, tiered by confidence:
- watch: environment favorable, no confirmed case.
- warning: a confirmed case nearby OR strong environmental signal.
- alert: both true AND recipient grows the susceptible crop.
Confirmation gate: an area alert fires only after (a) an RSK officer confirms the case, OR (b) N>=3 independent nearby reports of the same condition, OR (c) strong environmental agreement. The gate scales with blast radius: one farmer gets an immediate individual answer; a neighborhood alert always passes the gate. Alerts are anonymized ("blight confirmed in your area", never a farmer's name). Recipients are farmers within radius_km whose crop matches the susceptible host.

## Farmer dispute vs officer verdict
- "This isn't right" (farmer) sets the case to a DISPUTED state and places it in the officer portal's "Farmer disputed" category. It is NOT an officer verdict and does NOT trigger community-alert propagation.
- Only the officer's Confirm/Override sets an authoritative verdict; confirming a contagious condition triggers propagation.

## Admin (RSK officer) portal & auth
- A single officer signs in via a SEPARATE admin login at /admin (a fixed demo credential is fine; show the demo credentials on the login page so a judge can enter, and name real auth as production). The officer session is separate from the farmer session. Farmers cannot reach /admin, and the officer is NOT routed through phone/OTP.
- The portal shows flagged cases in TWO categories:
  - (a) "AI needs review" — cases auto-escalated because the AI was uncertain.
  - (b) "Farmer disputed" — cases a farmer flagged as wrong.
  - Each case shows crop, location, photo, the AI's ranked candidates + confidence, and visible symptoms.
- Actions: Confirm (accept the AI's top condition) or Override (pick a different condition). The verdict is authoritative and flows back to the farmer; confirming a contagious condition triggers the community alert.

## Conversational crop recommendation
- The recommendation screen is voice-first and conversational. The farmer speaks a free-form question (e.g. "what should I grow after tomatoes?").
- The transcript + full profile context (soil, location, weather/season from date, current & recent crops) is sent to Gemini, GROUNDED with the relevant crops-DB entries.
- Gemini returns a specific recommended crop (or two) with a clear structured "why" (soil, season/weather, rotation). The answer is read aloud.
- It should feel like a conversation, not a form.

## Design principles (farmer-facing frontend)
- Voice-first: a large microphone button is the primary action; responses are read aloud with text-to-speech; minimal reading required.
- Low literacy: short words, icons + short labels, visuals over text. Large touch targets (min 48px), body text >=18px, high contrast.
- Multilingual: language chosen up front and persisted; every UI string available in en/hi/te.
- Accessible: semantic HTML, ARIA labels, keyboard navigable, alt text on images, color never the only signal, respects prefers-reduced-motion.
- Friendly states everywhere: "listening...", "thinking...", empty states, and errors phrased kindly (never technical).

## Demo scenario data (must be seeded)
- Farmer A "Ramesh": Guntur, tomato, lang hi. Patient zero.
- Farmer B "Lakshmi": ~3 km from A, tomato, lang te. SHOULD receive the alert.
- Farmer C "Venkat": ~2 km from A, rice, lang te. Should NOT receive it (wrong crop).
- Farmer D "Sita": ~15 km from A, tomato, lang hi. Should NOT receive it (out of radius).
