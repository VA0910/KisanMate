/**
 * App state machine: screen routing, i18n application, and all the
 * screen-specific wiring (Home voice router, Diagnose, Recommend, Alerts).
 * Talks to the backend only through window.KM_API; talks to voice only
 * through window.KM_SPEECH; never touches strings except through KM_STRINGS.
 */
(function () {
  "use strict";

  var $ = document.getElementById.bind(document);

  var SESSION_KEY = "kisanmate_session";
  // The screens that make up the signed-in app (bottom nav shows only on these).
  var APP_SCREENS = { home: true, diagnose: true, recommend: true, alerts: true, profile: true, reports: true };

  var SOIL_OPTIONS = ["alluvial", "black", "loamy", "red", "sandy"];
  var ZONE_OPTIONS = ["delta", "coastal", "upland", "semi_arid"];
  var RAINFALL_OPTIONS = ["low", "medium", "high"];
  var GROUNDWATER_OPTIONS = ["shallow", "medium", "deep"];
  var RAINFALL_MM = { low: 300, medium: 650, high: 1000 };
  var GROUNDWATER_M = { shallow: 3, medium: 10, deep: 20 };
  var CROP_EMOJI = { rice: "🌾", tomato: "🍅", chili: "🌶️", cotton: "🌱", groundnut: "🥜", maize: "🌽" };
  var TIER_ICON = {
    watch: "icon-info-circle", warning: "icon-alert-triangle", alert: "icon-alert-octagon",
    confirmed: "icon-check-circle"
  };

  var SECTION_IDS = {
    welcome: "screen-welcome",
    language: "screen-language",
    phone: "screen-phone",
    otp: "screen-otp",
    "profile-setup": "screen-profile-setup",
    profile: "screen-profile",
    home: "screen-home",
    diagnose: "screen-diagnose",
    recommend: "screen-recommend",
    alerts: "screen-alerts",
    reports: "screen-reports"
  };

  var state = {
    lang: localStorage.getItem("kisanmate_lang"),
    session: loadSession(),
    farmerId: null,
    screen: null,
    micState: "idle",
    diagnosePhotoFile: null,
    lastDiagnoseCaseId: null,
    lastDiagnoseMessage: "",
    recommendSelections: { soil: null, zone: null, rainfall: null, groundwater: null },
    pendingPhone: null,
    demoActive: false
  };

  if (state.lang && !window.KM_STRINGS[state.lang]) state.lang = null;

  // Optional dev override: ?farmer=<id> signs in as that farmer without OTP.
  // The localStorage session remains the real source of truth (PROJECT_SPEC.md).
  (function applyDevFarmerOverride() {
    var fromQuery = new URLSearchParams(window.location.search).get("farmer");
    if (fromQuery) {
      saveSession({ id: fromQuery, name: "", phone: "", lang: state.lang || "en" });
    }
  })();

  if (state.session && state.session.farmer) state.farmerId = state.session.farmer.id;

  // ---- session (phone + OTP sign-in, persisted in localStorage) -----------------

  function loadSession() {
    try {
      var raw = localStorage.getItem(SESSION_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function saveSession(farmer) {
    state.session = { farmer: farmer, at: Date.now() };
    state.farmerId = farmer.id;
    localStorage.setItem(SESSION_KEY, JSON.stringify(state.session));
    // If the farmer has a saved language, adopt it as the app language.
    if (farmer.lang && window.KM_STRINGS[farmer.lang]) {
      state.lang = farmer.lang;
      localStorage.setItem("kisanmate_lang", farmer.lang);
    }
  }

  function clearSession() {
    state.session = null;
    state.farmerId = null;
    localStorage.removeItem(SESSION_KEY);
  }

  function isSignedIn() {
    return !!(state.session && state.session.farmer && state.session.farmer.id);
  }

  function dict() {
    return window.KM_STRINGS[state.lang] || window.KM_STRINGS.en;
  }
  function t(key) {
    return dict()[key];
  }
  function localeTag() {
    return window.KM_LOCALE_TAGS[state.lang] || "en-IN";
  }
  function friendlyMessageFor(err, screenKey) {
    if (err && err.kind === "timeout") return t("friendlyErrorSlow");
    return t(screenKey);
  }

  // ---- i18n ----------------------------------------------------------------

  function setLang(code) {
    state.lang = code;
    localStorage.setItem("kisanmate_lang", code);
    applyStrings();
    rebuildRecommendOptionGroups();
  }

  function applyStrings() {
    var d = dict();
    document.documentElement.lang = state.lang || "en";
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      var val = d[el.getAttribute("data-i18n")];
      if (typeof val === "string") el.textContent = val;
    });
    document.querySelectorAll("[data-i18n-aria]").forEach(function (el) {
      var val = d[el.getAttribute("data-i18n-aria")];
      if (typeof val === "string") el.setAttribute("aria-label", val);
    });
    document.querySelectorAll("[data-i18n-alt]").forEach(function (el) {
      var val = d[el.getAttribute("data-i18n-alt")];
      if (typeof val === "string") el.setAttribute("alt", val);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) {
      var val = d[el.getAttribute("data-i18n-placeholder")];
      if (typeof val === "string") el.setAttribute("placeholder", val);
    });
    document.title = d.appName + " – " + d.tagline;
    if (!window.KM_SPEECH.sttAvailable) {
      var micHint = $("mic-hint");
      if (micHint) micHint.textContent = t("micUnavailableHint");
    }
    markCurrentLanguageCard();
    updateHomeGreeting();
    updateHeaderButtons();
  }

  function updateHomeGreeting() {
    var heading = $("home-heading");
    if (!heading) return;
    var greeting = t("homeGreeting");
    var name = state.session && state.session.farmer ? state.session.farmer.name : "";
    heading.textContent = name ? greeting + ", " + name : greeting;
  }

  function updateHeaderButtons() {
    // The account button (which opens the profile page) shows only when signed in.
    var profileBtn = $("open-profile-btn");
    if (profileBtn) profileBtn.hidden = !isSignedIn();
  }

  function markCurrentLanguageCard() {
    document.querySelectorAll(".lang-card").forEach(function (btn) {
      if (btn.getAttribute("data-lang") === state.lang) btn.setAttribute("aria-current", "true");
      else btn.removeAttribute("aria-current");
    });
  }

  // ---- routing ---------------------------------------------------------------

  var SCREEN_ENTER_HOOKS = {
    home: loadReminders,
    diagnose: resetDiagnoseScreen,
    recommend: resetRecommendScreen,
    alerts: loadAlerts,
    reports: loadMyCases
  };

  function showScreen(name, skipHook) {
    // Release the camera if we're leaving the diagnose screen with it still open.
    if (name !== "diagnose" && state.cameraStream) stopCamera();

    Object.keys(SECTION_IDS).forEach(function (key) {
      var el = $(SECTION_IDS[key]);
      if (el) el.hidden = key !== name;
    });
    state.screen = name;

    // The bottom nav shows only on the signed-in app screens -- never during
    // onboarding (welcome/language/phone/otp/profile) or the guided demo.
    var bottomNav = $("bottom-nav");
    if (bottomNav) bottomNav.hidden = !APP_SCREENS[name] || state.demoActive;
    document.querySelectorAll(".nav-btn").forEach(function (btn) {
      if (btn.getAttribute("data-target") === name) btn.setAttribute("aria-current", "page");
      else btn.removeAttribute("aria-current");
    });

    // Enter hooks (e.g. loading alerts) are skipped during the demo, which
    // supplies its own data instead of hitting the live endpoints.
    var hook = SCREEN_ENTER_HOOKS[name];
    if (hook && !skipHook) hook();

    var section = $(SECTION_IDS[name]);
    var heading = section && section.querySelector("h1, h2");
    if (heading && !state.demoActive) {
      if (!heading.hasAttribute("tabindex")) heading.setAttribute("tabindex", "-1");
      heading.focus();
    }
    window.scrollTo(0, 0);
  }

  // ---- toast ------------------------------------------------------------------

  var toastTimer = null;
  function toast(msg) {
    var el = $("toast");
    if (!el) return;
    el.textContent = msg;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.textContent = ""; }, 4000);
  }

  // ---- shared: segmented bar visual (confidence / crop score) -----------------

  function renderSegments(container, filled, total) {
    container.innerHTML = "";
    for (var i = 0; i < total; i++) {
      var span = document.createElement("span");
      span.className = "segment" + (i < filled ? " segment-filled" : "");
      container.appendChild(span);
    }
  }

  function confidenceWord(c) {
    if (c >= 0.7) return t("confidenceHigh");
    if (c >= 0.4) return t("confidenceMedium");
    return t("confidenceLow");
  }

  // Localized label for a condition id; crop-DB disease ids (e.g. "rice_blast")
  // that aren't in the i18n table are prettified ("Rice Blast") rather than shown raw.
  function prettyCondition(code) {
    if (!code) return "";
    var d = dict();
    if (d.conditions && d.conditions[code]) return d.conditions[code];
    return code.replace(/_/g, " ").replace(/\b\w/g, function (m) { return m.toUpperCase(); });
  }

  function setIconUse(useEl, symbolId) {
    useEl.setAttribute("href", "#" + symbolId);
  }

  // ---- Home ---------------------------------------------------------------------

  // Proactive cycle-based reminders: fetched automatically when home opens and
  // rendered as a card at the top -- no button, no user trigger (the date math
  // runs server-side from each crop's planting_date).
  function loadReminders() {
    var card = $("reminders-card");
    if (!card) return;
    if (!isSignedIn() || state.demoActive) { card.hidden = true; return; }
    window.KM_API.getReminders(state.farmerId)
      .then(renderReminders)
      .catch(function () { card.hidden = true; }); // silent: reminders are a bonus, never an error
  }

  var REMINDER_EMOJI = { irrigation: "💧", stage_care: "🌱", harvest: "🌾" };
  var DRY_SPELL_EMOJI = "☀️";

  function reminderCropName(rem) {
    var names = rem.crop_names || {};
    return names[state.lang] || names.en || rem.crop_id || "";
  }

  function reminderText(rem) {
    var crop = reminderCropName(rem);
    var n = rem.days_until;
    if (rem.type === "irrigation") {
      if (rem.dry_spell) return t("reminderIrrigateDrySpell")(crop);
      return n <= 0 ? t("reminderIrrigateToday")(crop) : t("reminderIrrigateIn")(crop, n);
    }
    if (rem.type === "harvest") {
      return n <= 0 ? t("reminderHarvestNow")(crop) : t("reminderHarvestIn")(crop, n);
    }
    // stage_care: the crop's current-stage care note (English, from the crop DB)
    return t("reminderStageCare")(crop) + " " + (rem.care_note || "");
  }

  function renderReminders(data) {
    var card = $("reminders-card");
    var list = $("reminders-list");
    if (!card || !list) return;
    var reminders = (data && data.reminders) || [];
    if (!reminders.length) { card.hidden = true; return; }

    list.innerHTML = "";
    reminders.forEach(function (rem) {
      // A "due today" badge marks time-critical actions (watering/harvest);
      // stage-care advice isn't time-critical, so it isn't badged.
      var isDue = rem.days_until <= 0 && rem.type !== "stage_care";
      var li = document.createElement("li");
      li.className = "reminder-item reminder-" + rem.type + (isDue ? " reminder-due" : "");

      var emoji = document.createElement("span");
      emoji.className = "reminder-emoji";
      emoji.setAttribute("aria-hidden", "true");
      emoji.textContent = (rem.type === "irrigation" && rem.dry_spell) ? DRY_SPELL_EMOJI : (REMINDER_EMOJI[rem.type] || "🔔");
      li.appendChild(emoji);

      var text = document.createElement("span");
      text.className = "reminder-text";
      text.textContent = reminderText(rem);
      li.appendChild(text);

      if (isDue) {
        var badge = document.createElement("span");
        badge.className = "reminder-badge";
        badge.textContent = t("dueToday");
        li.appendChild(badge);
      }

      list.appendChild(li);
    });
    card.hidden = false;
  }

  function wireHome() {
    $("mic-btn").addEventListener("click", handleMicTap);
    $("go-diagnose-btn").addEventListener("click", function () { showScreen("diagnose"); });
    $("go-recommend-btn").addEventListener("click", function () { showScreen("recommend"); });
    $("go-reports-btn").addEventListener("click", function () { showScreen("reports"); });

    if (!window.KM_SPEECH.sttAvailable) {
      $("mic-hint").textContent = t("micUnavailableHint");
      $("mic-btn").setAttribute("aria-disabled", "true");
    }
  }

  function setMicVisual(newState) {
    state.micState = newState;
    var btn = $("mic-btn");
    var statusEl = $("mic-status-text");
    if (newState === "listening") {
      btn.classList.add("mic-listening");
      btn.setAttribute("aria-pressed", "true");
      statusEl.textContent = t("listening");
    } else {
      btn.classList.remove("mic-listening");
      btn.setAttribute("aria-pressed", "false");
      statusEl.textContent = t("tapAndSpeak");
    }
  }

  function matchIntent(transcript) {
    var lower = transcript.toLowerCase();
    var commands = window.KM_VOICE_COMMANDS[state.lang] || window.KM_VOICE_COMMANDS.en;
    var order = ["diagnose", "recommend", "alerts"];
    for (var i = 0; i < order.length; i++) {
      var keywords = commands[order[i]];
      for (var j = 0; j < keywords.length; j++) {
        if (lower.indexOf(keywords[j].toLowerCase()) !== -1) return order[i];
      }
    }
    return null;
  }

  function handleMicTap() {
    if (!window.KM_SPEECH.sttAvailable) return;
    if (state.micState === "listening") {
      window.KM_SPEECH.stopListening();
      setMicVisual("idle");
      return;
    }
    setMicVisual("listening");
    window.KM_SPEECH.listenOnce(localeTag(), {
      onResult: function (transcript) {
        var intent = matchIntent(transcript);
        if (intent === "diagnose") {
          window.KM_SPEECH.speak(t("voiceOpeningDiagnose"), localeTag());
          showScreen("diagnose");
        } else if (intent === "recommend") {
          window.KM_SPEECH.speak(t("voiceOpeningRecommend"), localeTag());
          showScreen("recommend");
        } else if (intent === "alerts") {
          window.KM_SPEECH.speak(t("voiceOpeningAlerts"), localeTag());
          showScreen("alerts");
        } else {
          toast(t("voiceNotUnderstood"));
          window.KM_SPEECH.speak(t("voiceNotUnderstood"), localeTag());
        }
      },
      onError: function (kind) {
        if (kind === "not-allowed" || kind === "service-not-allowed") {
          toast(t("micPermissionDenied"));
        } else if (kind === "unsupported") {
          $("mic-hint").textContent = t("micUnavailableHint");
        } else {
          toast(t("voiceNotUnderstood"));
        }
      },
      onEnd: function () { setMicVisual("idle"); }
    });
  }

  // ---- Diagnose ---------------------------------------------------------------

  function wireDiagnose() {
    $("photo-input").addEventListener("change", function (e) {
      var file = e.target.files && e.target.files[0];
      if (file) setDiagnosePhoto(file);
    });
    $("open-camera-btn").addEventListener("click", openCamera);
    $("capture-photo-btn").addEventListener("click", capturePhoto);
    $("cancel-camera-btn").addEventListener("click", stopCamera);
    $("change-photo-btn").addEventListener("click", resetDiagnoseScreen);
    $("submit-diagnose-btn").addEventListener("click", submitDiagnose);
    $("diagnose-retry-btn").addEventListener("click", submitDiagnose);
    $("new-photo-btn").addEventListener("click", resetDiagnoseScreen);
    $("not-right-btn").addEventListener("click", handleNotRight);
    $("play-audio-btn").addEventListener("click", function () {
      if (state.lastDiagnoseMessage) window.KM_SPEECH.speak(state.lastDiagnoseMessage, localeTag());
    });
    if (!window.KM_SPEECH.ttsAvailable) $("play-audio-btn").hidden = true;
  }

  function setDiagnosePhoto(file) {
    state.diagnosePhotoFile = file;
    $("photo-preview").src = URL.createObjectURL(file);
    $("photo-preview-wrap").hidden = false;
    $("capture-choices").hidden = true;
    $("submit-diagnose-btn").disabled = false;
  }

  // ---- camera capture (getUserMedia) with silent fall back to upload ----------

  function openCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      cameraUnavailable("unsupported");
      return;
    }
    navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } })
      .then(function (stream) {
        state.cameraStream = stream;
        var video = $("camera-video");
        video.srcObject = stream;
        $("camera-view").hidden = false;
        $("capture-choices").hidden = true;
        $("photo-preview-wrap").hidden = true;
        $("capture-photo-btn").focus();
      })
      .catch(function (err) { cameraUnavailable((err && err.name) || "denied"); });
  }

  // Silent fallback (never an error to the farmer): hide the camera option, keep
  // upload, and log why (PROJECT_SPEC.md layer 3).
  function cameraUnavailable(reason) {
    stopCamera();
    var btn = $("open-camera-btn");
    if (btn) btn.hidden = true;
    window.KM_API.logTelemetry({
      event: "camera_fallback", layer: "diagnose",
      detail: { reason: reason }, fallback_used: true
    });
  }

  function stopCamera() {
    if (state.cameraStream) {
      state.cameraStream.getTracks().forEach(function (t) { t.stop(); });
      state.cameraStream = null;
    }
    var video = $("camera-video");
    if (video) video.srcObject = null;
    $("camera-view").hidden = true;
    if (!state.diagnosePhotoFile) $("capture-choices").hidden = false;
  }

  function capturePhoto() {
    var video = $("camera-video");
    var canvas = $("camera-canvas");
    if (!video.videoWidth) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob(function (blob) {
      if (!blob) return;
      var file = new File([blob], "capture.jpg", { type: "image/jpeg" });
      stopCamera();
      setDiagnosePhoto(file);
    }, "image/jpeg", 0.9);
  }

  function showDiagnoseSubState(name) {
    var map = { capture: "diagnose-capture", thinking: "diagnose-thinking", result: "diagnose-result", error: "diagnose-error" };
    Object.keys(map).forEach(function (k) { $(map[k]).hidden = k !== name; });
  }

  function resetDiagnoseScreen() {
    stopCamera();
    state.diagnosePhotoFile = null;
    state.lastDiagnoseCaseId = null;
    state.lastDiagnoseMessage = "";
    $("photo-input").value = "";
    $("photo-preview-wrap").hidden = true;
    $("capture-choices").hidden = false;
    $("submit-diagnose-btn").disabled = true;
    $("dispute-thanks").hidden = true;
    $("not-right-btn").hidden = false;
    showDiagnoseSubState("capture");
  }

  function submitDiagnose() {
    if (!state.diagnosePhotoFile) return;
    showDiagnoseSubState("thinking");
    window.KM_API.diagnose(state.farmerId, state.diagnosePhotoFile, null)
      .then(renderDiagnoseResult)
      .catch(function (err) {
        $("diagnose-error-msg").textContent = friendlyMessageFor(err, "diagnoseError");
        showDiagnoseSubState("error");
      });
  }

  function renderDiagnoseResult(data) {
    state.lastDiagnoseCaseId = data.case_id || null;
    state.lastDiagnoseMessage = data.message || "";
    var d = dict();
    var fusion = data.fusion;

    var pillText = $("result-status-text");
    var pillIcon = $("result-status-icon");
    var conditionHeading = $("result-condition");
    var confidenceBlock = $("confidence-block");

    if (fusion && fusion.top) {
      var isAdvise = fusion.decision === "advise";
      pillText.textContent = isAdvise ? t("statusAdvise") : t("statusEscalate");
      setIconUse(pillIcon, isAdvise ? "icon-check-circle" : "icon-alert-triangle");
      $("result-status-pill").className = "status-pill " + (isAdvise ? "status-ok" : "status-attention");

      conditionHeading.hidden = false;
      conditionHeading.textContent = prettyCondition(fusion.top);

      // The confidence indicator is driven by DECISION, not the raw posterior
      // number: "advise" always reads confident/high; "escalate_rsk" always reads
      // uncertain -- so a confident result is never shown as unsure (and never
      // paired with an officer message), per PROJECT_SPEC.md.
      confidenceBlock.hidden = false;
      var word = isAdvise ? t("confidenceHigh") : t("confidenceUncertain");
      var filled = isAdvise ? 5 : 2;
      renderSegments($("confidence-segments"), filled, 5);
      $("confidence-word").textContent = word;
      $("confidence-segments").setAttribute("aria-label", t("confidenceLabel") + ": " + word);
    } else {
      pillText.textContent = t("statusEscalate");
      setIconUse(pillIcon, "icon-alert-triangle");
      $("result-status-pill").className = "status-pill status-attention";
      conditionHeading.hidden = true;
      confidenceBlock.hidden = true;
    }

    renderExplanation(data);
    $("not-right-btn").hidden = !state.lastDiagnoseCaseId;
    $("dispute-thanks").hidden = true;

    showDiagnoseSubState("result");
    if (!state.demoActive) window.KM_SPEECH.speak(data.message, localeTag());
  }

  // Render the structured what / why / what-to-do in separate blocks; fall back
  // to a single paragraph if the backend only returned a combined message.
  function renderExplanation(data) {
    var expl = data.explanation;
    var structured = $("result-explanation");
    var fallback = $("advice-block-fallback");
    if (expl && (expl.what || expl.why || expl.what_to_do)) {
      setExplainBlock("explain-what-block", "explain-what", expl.what);
      setExplainBlock("explain-why-block", "explain-why", expl.why);
      setExplainBlock("explain-todo-block", "explain-todo", expl.what_to_do);
      structured.hidden = false;
      fallback.hidden = true;
    } else {
      structured.hidden = true;
      fallback.hidden = false;
      $("result-message").textContent = data.message || "";
    }
  }

  function setExplainBlock(blockId, textId, value) {
    var text = (value || "").trim();
    $(textId).textContent = text;
    $(blockId).hidden = !text;
  }

  function handleNotRight() {
    if (!state.lastDiagnoseCaseId) return;
    var confirmed = window.confirm(t("disputeConfirmQuestion"));
    if (!confirmed) return;
    // Dispute path: records status="disputed" for the officer's "Farmer disputed"
    // queue. It is NOT an officer verdict and fires no alert (backend-enforced).
    window.KM_API.dispute(state.lastDiagnoseCaseId).catch(function () {});
    $("dispute-thanks").hidden = false;
    $("dispute-thanks").textContent = t("disputeThanks");
    $("not-right-btn").hidden = true;
    window.KM_SPEECH.speak(t("disputeThanks"), localeTag());
  }

  // ---- Recommend (voice-first conversation) --------------------------------------

  // Kept as a no-op: the recommend screen no longer uses the old chip form, but
  // several callers (setLang, profile save, demo) still call this by name.
  function rebuildRecommendOptionGroups() {}

  function resetRecommendScreen() {
    state.recommendMicState = "idle";
    state.lastRecommendSpoken = "";
    setRecommendMicVisual("idle");
    $("recommend-transcript").hidden = true;
    $("recommend-transcript").textContent = "";
    $("recommend-mic-hint").textContent = "";
    $("recommend-text-input").value = "";
    if (!window.KM_SPEECH.sttAvailable) {
      // STT unavailable: point the farmer at the text box instead (logged).
      $("recommend-mic-btn").setAttribute("aria-disabled", "true");
      $("recommend-mic-hint").textContent = t("micUnavailableHint");
      window.KM_API.logTelemetry({ event: "stt_unavailable", layer: "recommend", detail: {}, fallback_used: true });
    }
    showRecommendSubState("ask");
  }

  function wireRecommend() {
    $("recommend-mic-btn").addEventListener("click", handleRecommendMic);
    $("recommend-text-form").addEventListener("submit", function (e) {
      e.preventDefault();
      askRecommend(($("recommend-text-input").value || "").trim());
    });
    $("recommend-start-over-btn").addEventListener("click", resetRecommendScreen);
    $("recommend-retry-btn").addEventListener("click", function () {
      if (state.lastRecommendQuestion) askRecommend(state.lastRecommendQuestion);
      else resetRecommendScreen();
    });
    $("recommend-play-btn").addEventListener("click", function () {
      if (state.lastRecommendSpoken) window.KM_SPEECH.speak(state.lastRecommendSpoken, localeTag());
    });
    if (!window.KM_SPEECH.ttsAvailable) $("recommend-play-btn").hidden = true;
  }

  function showRecommendSubState(name) {
    var map = { ask: "recommend-ask", thinking: "recommend-thinking", result: "recommend-result", error: "recommend-error" };
    Object.keys(map).forEach(function (k) { $(map[k]).hidden = k !== name; });
  }

  function setRecommendMicVisual(newState) {
    state.recommendMicState = newState;
    var btn = $("recommend-mic-btn");
    var status = $("recommend-mic-status");
    if (newState === "listening") {
      btn.classList.add("mic-listening");
      btn.setAttribute("aria-pressed", "true");
      status.textContent = t("listening");
    } else {
      btn.classList.remove("mic-listening");
      btn.setAttribute("aria-pressed", "false");
      status.textContent = t("askMeToGrow");
    }
  }

  function handleRecommendMic() {
    if (!window.KM_SPEECH.sttAvailable) {
      // Fallback: focus the text box so the farmer can type instead.
      $("recommend-mic-hint").textContent = t("micUnavailableHint");
      $("recommend-text-input").focus();
      return;
    }
    if (state.recommendMicState === "listening") {
      window.KM_SPEECH.stopListening();
      setRecommendMicVisual("idle");
      return;
    }
    setRecommendMicVisual("listening");
    window.KM_SPEECH.listenOnce(localeTag(), {
      onResult: function (transcript) {
        var q = (transcript || "").trim();
        if (q) askRecommend(q);
      },
      onError: function (kind) {
        if (kind === "not-allowed" || kind === "service-not-allowed") {
          $("recommend-mic-hint").textContent = t("micPermissionDenied");
        } else if (kind === "unsupported") {
          $("recommend-mic-hint").textContent = t("micUnavailableHint");
        } else {
          $("recommend-mic-hint").textContent = t("voiceNotUnderstood");
        }
      },
      onEnd: function () { setRecommendMicVisual("idle"); }
    });
  }

  function askRecommend(question) {
    if (!question) { $("recommend-mic-hint").textContent = t("recommendAskEmpty"); return; }
    state.lastRecommendQuestion = question;
    showRecommendSubState("thinking");
    window.KM_API.recommendAsk(state.farmerId, question)
      .then(renderRecommendResult)
      .catch(function (err) {
        $("recommend-error-msg").textContent = friendlyMessageFor(err, "recommendError");
        showRecommendSubState("error");
      });
  }

  function renderRecommendResult(data) {
    var recs = (data && data.recommendations) || [];
    var qEl = $("recommend-question");
    if (data && data.question) {
      qEl.textContent = t("recommendYouAsked")(data.question);
      qEl.hidden = false;
    } else {
      qEl.hidden = true;
    }

    var list = $("recommend-list");
    list.innerHTML = "";
    recs.forEach(function (rec, index) {
      var li = document.createElement("li");
      li.className = "reco-card" + (index === 0 ? " reco-card-best" : "");

      var head = document.createElement("div");
      head.className = "reco-head";
      var emoji = document.createElement("span");
      emoji.className = "crop-emoji";
      emoji.setAttribute("aria-hidden", "true");
      emoji.textContent = CROP_EMOJI[rec.crop_id] || "🌱";
      var name = document.createElement("span");
      name.className = "reco-name";
      name.textContent = rec.crop_name || rec.crop_id || "";
      head.appendChild(emoji);
      head.appendChild(name);
      li.appendChild(head);

      if (rec.why) {
        var why = document.createElement("p");
        why.className = "reco-why";
        why.textContent = rec.why;
        li.appendChild(why);
      }
      list.appendChild(li);
    });

    state.lastRecommendSpoken = (data && data.spoken) || "";
    showRecommendSubState("result");
    if (state.lastRecommendSpoken && !state.demoActive) {
      window.KM_SPEECH.speak(state.lastRecommendSpoken, localeTag());
    }
  }

  // ---- Alerts ------------------------------------------------------------------

  function showAlertsSubState(name) {
    var map = { loading: "alerts-loading", list: "alerts-list", empty: "alerts-empty", error: "alerts-error" };
    Object.keys(map).forEach(function (k) { $(map[k]).hidden = k !== name; });
  }

  function loadAlerts() {
    showAlertsSubState("loading");
    window.KM_API.getAlerts(state.farmerId)
      .then(renderAlerts)
      .catch(function (err) {
        $("alerts-error-msg").textContent = friendlyMessageFor(err, "alertsLoadError");
        showAlertsSubState("error");
      });
  }

  function formatRelativeDate(iso) {
    if (!iso) return null;
    var date = new Date(iso);
    if (isNaN(date.getTime())) return null;
    var diffDays = Math.floor((Date.now() - date.getTime()) / 86400000);
    if (diffDays <= 0) return t("today");
    if (diffDays === 1) return t("yesterday");
    return t("daysAgo")(diffDays);
  }

  function renderAlerts(data) {
    var d = dict();
    var alerts = data.alerts || [];
    if (!alerts.length) {
      showAlertsSubState("empty");
      return;
    }

    var list = $("alerts-list");
    list.innerHTML = "";
    alerts.forEach(function (alert) {
      var tier = alert.tier || "watch";
      var li = document.createElement("li");
      li.className = "alert-card alert-tier-" + tier;

      var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "icon alert-card-icon");
      svg.setAttribute("aria-hidden", "true");
      var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
      use.setAttribute("href", "#" + (TIER_ICON[tier] || "icon-info-circle"));
      svg.appendChild(use);
      li.appendChild(svg);

      var body = document.createElement("div");
      body.className = "alert-card-body";

      var badge = document.createElement("span");
      badge.className = "tier-badge tier-badge-" + tier;
      badge.textContent = d.tiers[tier] || tier;
      body.appendChild(badge);

      var title = document.createElement("p");
      title.className = "alert-card-title";
      title.textContent = prettyCondition(alert.condition) + " " + t("inYourArea");
      body.appendChild(title);

      var metaParts = [];
      var relDate = formatRelativeDate(alert.created_at);
      if (relDate) metaParts.push(relDate);
      if (typeof alert.radius_km === "number") metaParts.push(t("withinKm")(Math.round(alert.radius_km)));
      if (metaParts.length) {
        var meta = document.createElement("p");
        meta.className = "alert-card-meta";
        meta.textContent = metaParts.join(" • ");
        body.appendChild(meta);
      }

      li.appendChild(body);
      list.appendChild(li);
    });

    showAlertsSubState("list");
    if (!state.demoActive) window.KM_SPEECH.speak(t("alertsSummarySpoken")(alerts.length), localeTag());
  }

  function wireAlerts() {
    $("alerts-retry-btn").addEventListener("click", loadAlerts);
  }

  // ---- My reports (past diagnoses, incl. any RSK officer verdict) --------------

  // Reuses the alert-card visual tiers: escalated/disputed still need attention,
  // confirmed is the officer's verdict landing back on the farmer (PROJECT_SPEC.md:
  // "the verdict is authoritative and flows back to the farmer"), advised/pending
  // are calm/neutral.
  var REPORT_STATUS_TIER = {
    pending: "watch",
    advised: "watch",
    escalated: "warning",
    disputed: "warning",
    confirmed: "confirmed"
  };

  function showReportsSubState(name) {
    var map = { loading: "reports-loading", list: "reports-list", empty: "reports-empty", error: "reports-error" };
    Object.keys(map).forEach(function (k) { $(map[k]).hidden = k !== name; });
  }

  function loadMyCases() {
    showReportsSubState("loading");
    window.KM_API.getMyCases(state.farmerId)
      .then(renderMyCases)
      .catch(function (err) {
        $("reports-error-msg").textContent = friendlyMessageFor(err, "reportsLoadError");
        showReportsSubState("error");
      });
  }

  function renderMyCases(data) {
    var d = dict();
    var cases = data.cases || [];
    if (!cases.length) {
      showReportsSubState("empty");
      return;
    }

    var list = $("reports-list");
    list.innerHTML = "";
    cases.forEach(function (item) {
      var status = item.status || "pending";
      var tier = REPORT_STATUS_TIER[status] || "watch";

      var li = document.createElement("li");
      li.className = "alert-card alert-tier-" + tier;

      var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "icon alert-card-icon");
      svg.setAttribute("aria-hidden", "true");
      var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
      use.setAttribute("href", "#" + (TIER_ICON[tier] || "icon-info-circle"));
      svg.appendChild(use);
      li.appendChild(svg);

      var body = document.createElement("div");
      body.className = "alert-card-body";

      var badge = document.createElement("span");
      badge.className = "tier-badge tier-badge-" + tier;
      badge.textContent = (d.reportStatuses && d.reportStatuses[status]) || status;
      body.appendChild(badge);

      var title = document.createElement("p");
      title.className = "alert-card-title";
      title.textContent = item.condition ? prettyCondition(item.condition) : t("loading");
      body.appendChild(title);

      var metaParts = [];
      if (item.crop) metaParts.push(prettyCondition(item.crop));
      var relDate = formatRelativeDate(item.created_at);
      if (relDate) metaParts.push(relDate);
      if (metaParts.length) {
        var meta = document.createElement("p");
        meta.className = "alert-card-meta";
        meta.textContent = metaParts.join(" • ");
        body.appendChild(meta);
      }

      if (item.officer_reviewed) {
        var note = document.createElement("p");
        note.className = "alert-card-meta";
        note.textContent = "✓ " + t("officerReviewed");
        body.appendChild(note);
      }

      li.appendChild(body);
      list.appendChild(li);
    });

    showReportsSubState("list");
  }

  function wireReports() {
    $("reports-retry-btn").addEventListener("click", loadMyCases);
  }

  // ---- verdict popup (RSK officer reviewed the farmer's case) ------------------

  // Checked once per app open/reload: if an officer has confirmed/overridden any
  // of the farmer's cases since they last saw it, show a one-time popup. The
  // server tracks "seen" per case (verdict_seen), so it fires exactly once even
  // across devices; acknowledging marks them seen so it never repeats.
  function checkVerdictNotifications() {
    if (!isSignedIn() || state.demoActive) return;
    window.KM_API.getNotifications(state.farmerId)
      .then(function (data) {
        var items = (data && data.notifications) || [];
        if (items.length) showVerdictModal(items);
      })
      .catch(function () { /* silent: the verdict is still in My reports */ });
  }

  function showVerdictModal(items) {
    var modal = $("verdict-modal");
    var body = $("verdict-modal-body");
    if (items.length === 1) {
      var cond = items[0].condition ? prettyCondition(items[0].condition) : "";
      body.textContent = t("verdictModalBody")(cond);
    } else {
      body.textContent = t("verdictModalBodyMany")(items.length);
    }

    // Acknowledge every notified case so the popup never fires for them again.
    function acknowledge() {
      items.forEach(function (it) {
        if (it.case_id) window.KM_API.markVerdictSeen(it.case_id);
      });
    }

    function close() {
      modal.hidden = true;
      acknowledge();
    }

    $("verdict-modal-dismiss-btn").onclick = close;
    $("verdict-modal-view-btn").onclick = function () {
      modal.hidden = true;
      acknowledge();
      showScreen("reports");
    };

    modal.hidden = false;
    if (!state.demoActive) window.KM_SPEECH.speak(t("verdictModalTitle"), localeTag());
  }

  // ---- language picker + header + bottom nav -------------------------------------

  function wireLanguageCards() {
    document.querySelectorAll(".lang-card").forEach(function (btn) {
      // Picking a card only SELECTS the language (applies it live); a forward
      // arrow advances -- to phone sign-in during onboarding, or back home when
      // the picker was opened via the header to change language while signed in.
      btn.addEventListener("click", function () {
        setLang(btn.getAttribute("data-lang"));
        $("language-next-btn").disabled = false;
      });
    });
    $("language-next-btn").addEventListener("click", function () {
      if (!state.lang) return;
      if (isSignedIn()) { showScreen("home"); return; }
      // First-time flow: language -> cinematic intro -> login. On later visits
      // (intro already seen) go straight to login.
      if (introSeen()) { showScreen("phone"); return; }
      startIntro(function () { showScreen("phone"); });
    });
  }

  function wireWelcome() {
    $("welcome-start-btn").addEventListener("click", function () { showScreen("language"); });
  }

  // ---- phone + OTP sign-in --------------------------------------------------------

  function wireAuth() {
    $("phone-form").addEventListener("submit", function (e) {
      e.preventDefault();
      submitPhone();
    });
    $("phone-back-btn").addEventListener("click", function () { showScreen("language"); });

    $("otp-form").addEventListener("submit", function (e) {
      e.preventDefault();
      submitOtp();
    });
    $("otp-back-btn").addEventListener("click", function () {
      $("otp-input").value = "";
      showScreen("phone");
    });
    // "Watch intro" on the login screen replays the cinematic intro.
    $("watch-intro-btn").addEventListener("click", function () {
      startIntro(function () { showScreen("phone"); });
    });
  }

  function submitPhone() {
    var raw = ($("phone-input").value || "").replace(/\D/g, "");
    if (raw.length < 10) {
      $("phone-hint").textContent = t("phoneInvalid");
      return;
    }
    $("phone-hint").textContent = "";
    var btn = $("send-otp-btn");
    btn.disabled = true;
    window.KM_API.requestOtp(raw)
      .then(function (data) {
        state.pendingPhone = data.phone || raw;
        $("otp-sent-to").textContent = t("otpSentTo")(state.pendingPhone);
        // Demo mode: the code comes back in the response so anyone can proceed.
        $("demo-otp-code").textContent = data.code || "";
        $("otp-input").value = "";
        $("otp-hint").textContent = "";
        showScreen("otp");
        $("otp-input").focus();
      })
      .catch(function () {
        $("phone-hint").textContent = t("otpRequestError");
      })
      .finally(function () { btn.disabled = false; });
  }

  function submitOtp() {
    var code = ($("otp-input").value || "").replace(/\D/g, "");
    if (code.length < 4) {
      $("otp-hint").textContent = t("otpInvalid");
      return;
    }
    $("otp-hint").textContent = "";
    var btn = $("verify-otp-btn");
    btn.disabled = true;
    window.KM_API.verifyOtp(state.pendingPhone, code, state.lang || "en")
      .then(function (data) {
        saveSession(data.farmer);
        applyStrings();
        rebuildRecommendOptionGroups();
        if (data.is_new) {
          openSetup();
        } else {
          showScreen("home");
          checkVerdictNotifications();  // one-time officer-verdict popup on first login
        }
      })
      .catch(function () {
        $("otp-hint").textContent = t("otpInvalid");
      })
      .finally(function () { btn.disabled = false; });
  }

  // ---- profile: shared building blocks (used by setup wizard + profile page) -----

  // Location fallback default (PROJECT_SPEC.md: district picker defaults to Guntur).
  var DEFAULT_DISTRICT_NAME = "Guntur";
  var DEFAULT_COORDS = { lat: 16.3067, lng: 80.4365 };

  // Live place-search type-ahead: as the farmer types a place name, query
  // /api/places (OSM geocoder) and show matches; selecting one sets the precise
  // coordinates as their location. `getLocation` returns the location object to
  // mutate (it is re-created on each screen open, so we read it lazily).
  function attachPlaceSearch(inputEl, suggestionsEl, getLocation, statusEl) {
    var timer = null;
    var results = [];
    var activeIndex = -1;

    function close() {
      suggestionsEl.hidden = true;
      suggestionsEl.innerHTML = "";
      inputEl.setAttribute("aria-expanded", "false");
      inputEl.removeAttribute("aria-activedescendant");
      results = [];
      activeIndex = -1;
    }

    function showHint(text) {
      suggestionsEl.innerHTML = "";
      var li = document.createElement("li");
      li.className = "place-hint";
      li.textContent = text;
      suggestionsEl.appendChild(li);
      suggestionsEl.hidden = false;
    }

    function choose(place) {
      var loc = getLocation();
      loc.mandal = place.name;
      loc.lat = place.lat;
      loc.lng = place.lng;
      inputEl.value = place.name;
      if (statusEl) statusEl.textContent = t("locationDistrictSet")(place.name);
      close();
    }

    function highlight(idx) {
      var items = suggestionsEl.querySelectorAll(".place-option");
      items.forEach(function (el, i) { el.classList.toggle("place-option-active", i === idx); });
      if (idx >= 0 && items[idx]) inputEl.setAttribute("aria-activedescendant", items[idx].id);
    }

    function render(places) {
      results = places;
      activeIndex = -1;
      suggestionsEl.innerHTML = "";
      places.forEach(function (p, i) {
        var li = document.createElement("li");
        li.className = "place-option";
        li.id = suggestionsEl.id + "-opt-" + i;
        li.setAttribute("role", "option");
        li.textContent = p.name;
        // mousedown (not click) so the selection lands before the input blurs.
        li.addEventListener("mousedown", function (e) { e.preventDefault(); choose(p); });
        suggestionsEl.appendChild(li);
      });
      suggestionsEl.hidden = false;
      inputEl.setAttribute("aria-expanded", "true");
    }

    function search() {
      var q = inputEl.value.trim();
      getLocation().mandal = q; // keep the typed name even if nothing is selected
      if (q.length < 3) { close(); return; }
      showHint(t("searchingPlaces"));
      window.KM_API.searchPlaces(q).then(function (data) {
        if (inputEl.value.trim() !== q) return; // a newer keystroke superseded this
        var places = (data && data.places) || [];
        if (!places.length) { showHint(t("noPlaceMatches")); return; }
        render(places);
      }).catch(function () { close(); });
    }

    inputEl.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(search, 350);
    });
    inputEl.addEventListener("keydown", function (e) {
      var items = suggestionsEl.querySelectorAll(".place-option");
      if (e.key === "ArrowDown" && items.length) {
        e.preventDefault(); activeIndex = Math.min(activeIndex + 1, items.length - 1); highlight(activeIndex);
      } else if (e.key === "ArrowUp" && items.length) {
        e.preventDefault(); activeIndex = Math.max(activeIndex - 1, 0); highlight(activeIndex);
      } else if (e.key === "Enter") {
        if (activeIndex >= 0 && results[activeIndex]) { e.preventDefault(); choose(results[activeIndex]); }
      } else if (e.key === "Escape") {
        close();
      }
    });
    inputEl.addEventListener("blur", function () { setTimeout(close, 150); });
  }

  var SOIL_TYPES = ["black", "red", "alluvial", "loam", "sandy", "clay"];

  // Language code -> native label, from the strings module's language list.
  var LANG_LABELS = {};
  (window.KM_LANGUAGES || []).forEach(function (l) { LANG_LABELS[l.code] = l.label; });

  // Cached /api/crops catalog (crop id + names), loaded once per session.
  var cropsCatalog = null;

  function ensureCropsCatalog() {
    if (cropsCatalog) return Promise.resolve(cropsCatalog);
    return window.KM_API.getCrops().then(function (data) {
      cropsCatalog = (data && data.crops) || [];
      return cropsCatalog;
    });
  }

  function cropName(crop) {
    if (!crop) return "";
    return (crop.names && (crop.names[state.lang] || crop.names.en)) || crop.id;
  }

  // Generic single-select chip group (soil, language). Calls onChange(code).
  function buildChips(containerId, groupName, codes, labelDict, selected, onChange) {
    var container = $(containerId);
    container.innerHTML = "";
    codes.forEach(function (code) {
      var id = groupName + "-" + code;
      var wrap = document.createElement("div");
      wrap.className = "chip";

      var input = document.createElement("input");
      input.type = "radio";
      input.name = groupName;
      input.id = id;
      input.value = code;
      input.className = "chip-input visually-hidden-input";
      if (selected === code) input.checked = true;
      input.addEventListener("change", function () { onChange(code); });

      var label = document.createElement("label");
      label.setAttribute("for", id);
      label.className = "chip-label";
      label.textContent = (labelDict && labelDict[code]) || code;

      wrap.appendChild(input);
      wrap.appendChild(label);
      container.appendChild(wrap);
    });
  }

  // One editable crop row: crop <select> + planting-date input + remove button.
  function addCropRow(listEl, prefill) {
    var row = document.createElement("div");
    row.className = "crop-row";

    var select = document.createElement("select");
    select.className = "text-input crop-row-select";
    select.setAttribute("aria-label", t("selectCropPlaceholder"));
    var placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = t("selectCropPlaceholder");
    select.appendChild(placeholder);
    (cropsCatalog || []).forEach(function (crop) {
      var opt = document.createElement("option");
      opt.value = crop.id;
      opt.textContent = cropName(crop);
      select.appendChild(opt);
    });
    if (prefill && prefill.crop_id) {
      // Defensive: if the catalog didn't include this crop (not yet loaded /
      // unavailable), still add it so the prefilled value sticks and isn't lost.
      if (!select.querySelector('option[value="' + prefill.crop_id + '"]')) {
        var fallback = document.createElement("option");
        fallback.value = prefill.crop_id;
        fallback.textContent = prefill.crop_id;
        select.appendChild(fallback);
      }
      select.value = prefill.crop_id;
    }

    var date = document.createElement("input");
    date.type = "date";
    date.className = "text-input crop-row-date";
    date.setAttribute("aria-label", t("plantingDateLabel"));
    if (prefill && prefill.planting_date) date.value = prefill.planting_date;

    var remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-btn crop-row-remove";
    remove.setAttribute("aria-label", t("cropRemove"));
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#icon-x");
    svg.appendChild(use);
    remove.appendChild(svg);
    remove.addEventListener("click", function () { row.parentNode.removeChild(row); });

    row.appendChild(select);
    row.appendChild(date);
    row.appendChild(remove);
    listEl.appendChild(row);
    return row;
  }

  // Read crop rows -> { crops: [{crop_id, planting_date}], missingDate: bool }.
  function collectCrops(listEl) {
    var crops = [];
    var missingDate = false;
    listEl.querySelectorAll(".crop-row").forEach(function (row) {
      var cropId = row.querySelector(".crop-row-select").value;
      var date = row.querySelector(".crop-row-date").value;
      if (!cropId) return; // an empty row is simply ignored
      if (!date) { missingDate = true; return; }
      crops.push({ crop_id: cropId, planting_date: date });
    });
    return { crops: crops, missingDate: missingDate };
  }

  // Device geolocation with the spec's silent, logged district fallback.
  // `inputEl` is the visible place text field; it must be updated too, otherwise
  // it keeps showing whatever district name (e.g. the "Guntur" default) was
  // there before the farmer's real coordinates were picked up.
  function detectGeolocation(statusEl, location, inputEl, onDone) {
    statusEl.textContent = t("locationDetecting");
    function fallback(reason) {
      window.KM_API.logTelemetry({
        event: "geolocation_fallback",
        layer: "location",
        detail: { reason: reason },
        fallback_used: true
      });
      statusEl.textContent = t("locationDenied");
      onDone(false);
    }
    if (!navigator.geolocation) { fallback("unsupported"); return; }
    navigator.geolocation.getCurrentPosition(
      function (pos) {
        location.lat = pos.coords.latitude;
        location.lng = pos.coords.longitude;
        statusEl.textContent = t("locationDeviceSet");
        window.KM_API.reversePlace(location.lat, location.lng)
          .then(function (data) {
            var name = data && data.name;
            if (name) {
              location.mandal = name;
              inputEl.value = name;
              statusEl.textContent = t("locationDistrictSet")(name);
            }
          })
          .catch(function () { /* keep the coords; the name is best-effort */ })
          .finally(function () { onDone(true); });
      },
      function (err) { fallback((err && err.message) || "denied"); },
      { timeout: 8000, maximumAge: 60000 }
    );
  }

  // ---- profile setup wizard (new farmers) ----------------------------------------

  var SETUP_STEPS = ["location", "soil", "crops"];
  var setupState = { index: 0, soil: null, location: null };

  function openSetup() {
    setupState.index = 0;
    setupState.soil = null;
    // Default location is Guntur until geolocation resolves or the farmer picks
    // or types a district.
    setupState.location = {
      lat: DEFAULT_COORDS.lat, lng: DEFAULT_COORDS.lng, mandal: DEFAULT_DISTRICT_NAME
    };

    $("setup-place-input").value = setupState.location.mandal;
    $("setup-place-suggestions").hidden = true;
    buildChips("setup-soil-options", "setup-soil", SOIL_TYPES, dict().soils, null, function (code) {
      setupState.soil = code;
      $("setup-soil-hint").textContent = "";
    });
    $("setup-crops-list").innerHTML = "";
    $("setup-crops-hint").textContent = "";
    $("setup-location-status").textContent = "";

    ensureCropsCatalog().then(function () {
      addCropRow($("setup-crops-list"), null); // start with one empty row
    }).catch(function () { /* crops step still usable once network returns */ });

    showSetupStep(0);
    showScreen("profile-setup");
    // Try the device location immediately; silently falls back to the picker.
    detectGeolocation($("setup-location-status"), setupState.location, $("setup-place-input"), function () {});
  }

  function showSetupStep(index) {
    setupState.index = index;
    SETUP_STEPS.forEach(function (name, i) {
      $("setup-step-" + name).hidden = i !== index;
    });
    $("setup-step-label").textContent = t("setupStepOf")(index + 1, SETUP_STEPS.length);
    var pct = Math.round(((index + 1) / SETUP_STEPS.length) * 100);
    $("setup-progress-fill").style.width = pct + "%";
  }

  function wireSetup() {
    $("setup-use-location-btn").addEventListener("click", function () {
      detectGeolocation($("setup-location-status"), setupState.location, $("setup-place-input"), function () {});
    });
    attachPlaceSearch(
      $("setup-place-input"), $("setup-place-suggestions"),
      function () { return setupState.location; }, $("setup-location-status")
    );
    $("setup-location-next").addEventListener("click", function () {
      // Keep whatever place text is in the box (coords were set on selection).
      var typed = $("setup-place-input").value.trim();
      if (typed) setupState.location.mandal = typed;
      showSetupStep(1);
    });

    $("setup-soil-back").addEventListener("click", function () { showSetupStep(0); });
    $("setup-soil-next").addEventListener("click", function () {
      if (!setupState.soil) { $("setup-soil-hint").textContent = t("soilRequired"); return; }
      showSetupStep(2);
    });

    $("setup-add-crop").addEventListener("click", function () {
      ensureCropsCatalog().then(function () { addCropRow($("setup-crops-list"), null); });
    });
    $("setup-crops-back").addEventListener("click", function () { showSetupStep(1); });
    $("setup-finish-btn").addEventListener("click", finishSetup);
  }

  function finishSetup() {
    var result = collectCrops($("setup-crops-list"));
    if (result.missingDate) { $("setup-crops-hint").textContent = t("cropDateRequired"); return; }
    if (result.crops.length === 0) { $("setup-crops-hint").textContent = t("cropsRequired"); return; }
    $("setup-crops-hint").textContent = "";

    var payload = {
      location: setupState.location,
      soil_type: setupState.soil,
      current_crops: result.crops
    };
    var btn = $("setup-finish-btn");
    btn.disabled = true;
    window.KM_API.updateFarmer(state.farmerId, payload)
      .then(function (farmer) {
        saveSession(farmer);
        applyStrings();
        rebuildRecommendOptionGroups();
        toast(t("profileSaved"));
        showScreen("home");
      })
      .catch(function (err) {
        if (isStaleSession(err)) { handleStaleSession(); return; }
        $("setup-crops-hint").textContent = t("profileSaveError");
      })
      .finally(function () { btn.disabled = false; });
  }

  // ---- profile page (editable, reachable any time) --------------------------------

  var profileEdit = { location: null, soil: null };

  // A 404 from a farmer endpoint means the signed-in session points to a document
  // that no longer exists (e.g. it was removed). Treat the session as stale.
  function isStaleSession(err) {
    return !!(err && err.status === 404);
  }

  function handleStaleSession() {
    clearSession();
    updateHeaderButtons();
    toast(t("sessionExpired"));
    showScreen("welcome");
  }

  function openProfilePage() {
    // Load the crops catalog BEFORE rendering, so prefilled crop rows can select
    // their crop (otherwise the <select> has no matching option and the crop is
    // silently dropped on save).
    var cached = state.session && state.session.farmer ? state.session.farmer : {};
    ensureCropsCatalog().catch(function () {}).then(function () {
      return window.KM_API.getFarmer(state.farmerId)
        .then(function (farmer) { renderProfilePage(farmer); showScreen("profile"); })
        .catch(function (err) {
          if (isStaleSession(err)) { handleStaleSession(); return; }
          renderProfilePage(cached);
          showScreen("profile");
        });
    });
  }

  function renderProfilePage(farmer) {
    farmer = farmer || {};
    var loc = farmer.location || {};
    profileEdit.location = {
      lat: typeof loc.lat === "number" ? loc.lat : DEFAULT_COORDS.lat,
      lng: typeof loc.lng === "number" ? loc.lng : DEFAULT_COORDS.lng,
      mandal: loc.mandal || DEFAULT_DISTRICT_NAME
    };

    buildChips("profile-lang-options", "profile-lang", ["en", "hi", "te"], LANG_LABELS,
      farmer.lang || state.lang, function (code) {
        setLang(code); // apply the UI language live; persisted on save
      });

    $("profile-place-input").value = profileEdit.location.mandal;
    $("profile-place-suggestions").hidden = true;
    $("profile-location-status").textContent = "";

    buildChips("profile-soil-options", "profile-soil", SOIL_TYPES, dict().soils,
      farmer.soil_type || null, function (code) { profileEdit.soil = code; });
    profileEdit.soil = farmer.soil_type || null;

    var listEl = $("profile-crops-list");
    listEl.innerHTML = "";
    (farmer.current_crops || []).forEach(function (c) { addCropRow(listEl, c); });

    $("profile-hint").textContent = "";
  }

  function wireProfilePage() {
    $("open-profile-btn").addEventListener("click", openProfilePage);

    $("profile-use-location-btn").addEventListener("click", function () {
      detectGeolocation($("profile-location-status"), profileEdit.location, $("profile-place-input"), function () {});
    });
    attachPlaceSearch(
      $("profile-place-input"), $("profile-place-suggestions"),
      function () { return profileEdit.location; }, $("profile-location-status")
    );
    $("profile-add-crop").addEventListener("click", function () {
      ensureCropsCatalog().then(function () { addCropRow($("profile-crops-list"), null); });
    });

    $("profile-form").addEventListener("submit", function (e) {
      e.preventDefault();
      saveProfilePage();
    });
    $("sign-out-btn").addEventListener("click", signOut);
  }

  function saveProfilePage() {
    // Keep any place text still in the box (coords were set on selection).
    var typed = $("profile-place-input").value.trim();
    if (typed) profileEdit.location.mandal = typed;

    var result = collectCrops($("profile-crops-list"));
    if (result.missingDate) { $("profile-hint").textContent = t("cropDateRequired"); return; }
    $("profile-hint").textContent = "";

    var payload = {
      lang: state.lang,
      location: profileEdit.location,
      soil_type: profileEdit.soil,
      current_crops: result.crops
    };
    var btn = $("profile-save-btn");
    btn.disabled = true;
    window.KM_API.updateFarmer(state.farmerId, payload)
      .then(function (farmer) {
        saveSession(farmer);
        applyStrings();
        rebuildRecommendOptionGroups();
        toast(t("profileSaved"));
        showScreen("home");
      })
      .catch(function (err) {
        if (isStaleSession(err)) { handleStaleSession(); return; }
        $("profile-hint").textContent = t("profileSaveError");
      })
      .finally(function () { btn.disabled = false; });
  }

  function signOut() {
    window.KM_SPEECH.stopSpeaking();
    clearSession();
    updateHeaderButtons();
    toast(t("signedOut"));
    showScreen("welcome");
  }

  function wireHeader() {
    $("change-language-btn").addEventListener("click", function () { showScreen("language"); });
  }

  function wireBottomNav() {
    document.querySelectorAll(".nav-btn").forEach(function (btn) {
      btn.addEventListener("click", function () { showScreen(btn.getAttribute("data-target")); });
    });
  }

  // ---- guided demo (the "Run demo scenario" walkthrough) --------------------------

  var DEMO_STEP_MS = 4500; // minimum beat time; a scene also waits for its voice-over
  var demoState = { narratorLang: "en", steps: [], index: -1, timer: null, maxTimer: null, gen: 0, data: null };

  function demoDict() {
    return window.KM_STRINGS[demoState.narratorLang] || window.KM_STRINGS.en;
  }
  function demoStr(key) {
    return demoDict()[key];
  }

  // Switch the app UI language during the demo WITHOUT persisting it (so the
  // judge's real choice is untouched when the demo exits).
  function applyDemoLang(code) {
    state.lang = code;
    applyStrings();
    rebuildRecommendOptionGroups();
  }

  function startDemo() {
    demoState.narratorLang = state.lang || "en";
    state.demoActive = true;
    window.KM_SPEECH.stopSpeaking();

    var overlay = $("demo-overlay");
    overlay.hidden = false;
    document.body.classList.add("demo-running");
    $("demo-replay-btn").hidden = true;
    $("demo-next-btn").disabled = true;
    $("bottom-nav").hidden = true;
    setDemoCaption("", demoStr("demoLoading"));
    $("demo-caption").focus();

    window.KM_API.demoRun(demoState.narratorLang)
      .then(function (data) {
        demoState.data = data;
        demoState.steps = buildDemoSteps(data);
        demoState.index = -1;
        $("demo-next-btn").disabled = false;
        advanceDemo();
      })
      .catch(function () {
        setDemoCaption("", demoStr("demoError"));
        showDemoEndControls();
      });
  }

  function buildDemoSteps(data) {
    return [
      {
        caption: demoStr("demoStep1"),
        run: function () {
          applyDemoLang(demoState.narratorLang);
          showScreen("recommend", true);
          // Map the deterministic demo data into the conversational result shape.
          var lang = demoState.narratorLang;
          var recs = (data.recommend.recommendations || []).slice(0, 2).map(function (r) {
            var nm = (r.names && (r.names[lang] || r.names.en)) || r.crop;
            return { crop_id: r.crop, crop_name: nm, why: r.reason || "" };
          });
          renderRecommendResult({ recommendations: recs });
        }
      },
      {
        caption: demoStr("demoStep2"),
        run: function () {
          showScreen("diagnose", true);
          renderDiagnoseResult(data.diagnose);
        },
        speakLang: demoState.narratorLang,
        speakText: data.diagnose.message
      },
      {
        caption: demoStr("demoStep3"),
        run: function () {
          // Show the officer portal: the case under "AI needs review", then the
          // officer confirming late blight. The real confirm + alert already
          // happened server-side in /api/demo/run; this stages the portal view.
          showStagedOfficerDemo();
        },
        speakText: demoStr("demoStep3")
      },
      {
        caption: demoStr("demoStep4"),
        run: function () {
          // Show Lakshmi's alert in Telugu (the story point: she's warned in HER
          // language) -- but the narration/voice-over stays in the viewer's chosen
          // language, so no speakLang/speakText override here (advanceDemo speaks
          // the caption in narratorLang).
          applyDemoLang("te");
          showScreen("alerts", true);
          renderAlerts({ alerts: data.alerts.lakshmi.alerts });
        }
      }
    ];
  }

  // Staged officer-portal beat for the guided demo (reuses the intro's portal
  // markup): hide the farmer screens and show the /admin-style view in place.
  function showStagedOfficerDemo() {
    Object.keys(SECTION_IDS).forEach(function (k) {
      var e = $(SECTION_IDS[k]);
      if (e) e.hidden = true;
    });
    var host = $("demo-officer");
    host.innerHTML = '<div class="intro-stage intro-enter">' + stagedOfficerHTML(demoDict(), false) + "</div>";
    host.hidden = false;
    var portal = host.querySelector(".intro-portal");
    var delay = reducedMotion() ? 0 : 1500;
    setTimeout(function () { if (portal) portal.classList.add("confirmed"); }, delay);
  }

  function advanceDemo() {
    clearTimeout(demoState.timer);
    clearTimeout(demoState.maxTimer);
    var myGen = ++demoState.gen;
    $("demo-officer").hidden = true; // each step starts clean; only the officer step shows it

    demoState.index += 1;
    var step = demoState.steps[demoState.index];
    if (!step) return;

    setDemoCaption(demoStr("demoStepOf")(demoState.index + 1, demoState.steps.length), step.caption);
    try {
      step.run();
    } catch (err) {
      /* a rendering hiccup must not halt the walkthrough */
    }
    updateDemoProgress();

    window.KM_SPEECH.stopSpeaking();
    var tag = window.KM_LOCALE_TAGS[step.speakLang || demoState.narratorLang] || "en-IN";

    // Advance only after BOTH the minimum beat time AND the voice-over finish --
    // the screen never changes mid-sentence. TTS is optional (speak() calls back
    // immediately when unavailable), so captions still pace the walkthrough.
    var isLast = demoState.index === demoState.steps.length - 1;
    var minDone = false, speechDone = false;
    function proceed() {
      if (myGen !== demoState.gen) return;
      if (!(minDone && speechDone)) return;
      if (isLast) { showDemoEndControls(); }
      else { $("demo-next-btn").focus(); advanceDemo(); }
    }
    demoState.timer = setTimeout(function () { minDone = true; proceed(); }, DEMO_STEP_MS);
    // Safety cap so a stuck speech engine can't freeze the walkthrough.
    demoState.maxTimer = setTimeout(function () {
      if (myGen === demoState.gen) { isLast ? showDemoEndControls() : advanceDemo(); }
    }, DEMO_STEP_MS + 15000);
    window.KM_SPEECH.speak(step.speakText || step.caption, tag, function () { speechDone = true; proceed(); });
  }

  function setDemoCaption(stepLabel, caption) {
    $("demo-step").textContent = stepLabel || "";
    $("demo-caption").textContent = caption || "";
  }

  function updateDemoProgress() {
    var pct = Math.round(((demoState.index + 1) / demoState.steps.length) * 100);
    $("demo-progress-fill").style.width = pct + "%";
  }

  function showDemoEndControls() {
    clearTimeout(demoState.timer);
    clearTimeout(demoState.maxTimer);
    demoState.gen++; // stop any pending scene callback
    $("demo-next-btn").disabled = true;
    $("demo-replay-btn").hidden = false;
    $("demo-replay-btn").focus();
  }

  function replayDemo() {
    if (!demoState.data) {
      // The initial run failed to load -- treat "Replay" as a retry.
      startDemo();
      return;
    }
    window.KM_SPEECH.stopSpeaking();
    $("demo-replay-btn").hidden = true;
    $("demo-next-btn").disabled = false;
    demoState.steps = buildDemoSteps(demoState.data);
    demoState.index = -1;
    advanceDemo();
  }

  function exitDemo() {
    clearTimeout(demoState.timer);
    clearTimeout(demoState.maxTimer);
    demoState.gen++;
    window.KM_SPEECH.stopSpeaking();
    state.demoActive = false;
    $("demo-overlay").hidden = true;
    $("demo-officer").hidden = true;
    document.body.classList.remove("demo-running");
    applyDemoLang(demoState.narratorLang); // restore the judge's language
    // Return to wherever the demo makes sense to exit to: the app home if signed
    // in, otherwise back to the welcome landing (the demo can run signed-out).
    showScreen(isSignedIn() ? "home" : "welcome");
  }

  function resetDemoData() {
    var btn = $("reset-demo-btn");
    btn.disabled = true;
    window.KM_API.demoReset()
      .then(function () { toast(t("resetDemoDone")); })
      .catch(function () { toast(t("resetDemoError")); })
      .finally(function () { btn.disabled = false; });
  }

  function wireDemo() {
    $("run-demo-btn").addEventListener("click", startDemo);
    $("run-demo-btn-welcome").addEventListener("click", startDemo);
    $("reset-demo-btn").addEventListener("click", resetDemoData);
    $("demo-next-btn").addEventListener("click", function () {
      clearTimeout(demoState.timer);
      advanceDemo();
    });
    $("demo-replay-btn").addEventListener("click", replayDemo);
    $("demo-exit-btn").addEventListener("click", exitDemo);
  }

  // ---- cinematic intro (scripted, pre-staged; no live AI) -------------------------

  var INTRO_SEEN_KEY = "kisanmate_intro_seen";
  var introState = { index: -1, timer: null, maxTimer: null, gen: 0, onDone: null };

  function introSeen() { return localStorage.getItem(INTRO_SEEN_KEY) === "1"; }
  function reducedMotion() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  // A sick tomato leaf, drawn inline so no image asset / network is needed.
  var LEAF_SVG =
    '<svg class="intro-leaf" viewBox="0 0 100 100" role="img" aria-label="sick tomato leaf">' +
    '<path d="M50 92 C18 76 14 34 50 10 C86 34 82 76 50 92 Z" fill="#5b8c3e"/>' +
    '<path d="M50 90 L50 18" stroke="#3d6b28" stroke-width="2"/>' +
    '<circle cx="40" cy="44" r="6.5" fill="#6b4a2b"/><circle cx="59" cy="54" r="5" fill="#6b4a2b"/>' +
    '<circle cx="46" cy="66" r="4.5" fill="#7a5230"/><circle cx="63" cy="38" r="3.5" fill="#7a5230"/></svg>';

  function segmentsHTML(filled, total) {
    var out = "";
    for (var i = 0; i < total; i++) out += '<span class="segment' + (i < filled ? " segment-filled" : "") + '"></span>';
    return out;
  }

  // Reusable staged officer-portal view (also used by the guided demo), so the
  // intro shows the REAL portal look driven by fixed content.
  function stagedOfficerHTML(d, confirmed) {
    return '' +
      '<div class="intro-portal' + (confirmed ? " confirmed" : "") + '">' +
      '  <div class="intro-portal-head">🛡️ RSK Officer Portal</div>' +
      '  <div class="intro-portal-tabs">' +
      '    <span class="intro-tab intro-tab-active">AI needs review · 1</span>' +
      '    <span class="intro-tab">Farmer disputed</span>' +
      '  </div>' +
      '  <div class="intro-portal-case">' +
      '    <div class="intro-portal-row"><strong>Ramesh</strong> · ' + (d.crops.tomato) + ' · Guntur</div>' +
      '    <div class="intro-portal-ai">AI: ' + d.conditions.late_blight + " / " + d.conditions.early_blight + ' ?</div>' +
      '    <button class="intro-confirm-btn" type="button" tabindex="-1">✓ Confirm: ' + d.conditions.late_blight + '</button>' +
      '    <div class="intro-confirmed-stamp">✓ ' + d.conditions.late_blight + '</div>' +
      '  </div>' +
      '</div>';
  }

  function introScenes() {
    var d = dict();
    return [
      {
        ms: 3000, caption: t("introScene1"),
        html:
          '<div class="intro-card">' +
          '  <div class="intro-profile"><span class="intro-avatar" aria-hidden="true">🧑‍🌾</span>' +
          '    <div><div class="intro-name">Ramesh</div><div class="intro-sub">Guntur</div></div></div>' +
          '  <div class="intro-chips">' +
          '    <span class="intro-chip">🍅 ' + d.crops.tomato + '</span>' +
          '    <span class="intro-chip">🪨 ' + d.soils.black + '</span>' +
          '    <span class="intro-chip">📍 Guntur</span></div>' +
          '</div>'
      },
      {
        ms: 3000, caption: t("introScene2"),
        html:
          '<div class="intro-card">' +
          '  <div class="intro-photo">' + LEAF_SVG + '</div>' +
          '  <div class="thinking-state intro-thinking">' +
          '    <svg class="icon icon-lg spinner-icon" aria-hidden="true"><use href="#icon-spinner"></use></svg>' +
          '    <p>' + t("thinking") + '</p></div>' +
          '</div>'
      },
      {
        ms: 3500, caption: t("introScene3"),
        html:
          '<div class="intro-card result-card">' +
          '  <div class="status-pill status-attention">' +
          '    <svg class="icon" aria-hidden="true"><use href="#icon-alert-triangle"></use></svg>' +
          '    <span>' + t("statusEscalate") + '</span></div>' +
          '  <h2 class="result-condition">' + d.conditions.late_blight + " / " + d.conditions.early_blight + ' ?</h2>' +
          '  <div class="confidence-block"><span class="confidence-label">' + t("confidenceLabel") + '</span>' +
          '    <div class="segment-row" role="img" aria-label="' + t("confidenceUncertain") + '">' + segmentsHTML(2, 5) + '</div>' +
          '    <span class="confidence-word">' + t("confidenceUncertain") + '</span></div>' +
          '  <div class="explain-block"><span class="explain-label">' + t("explainWhatToDo") + '</span>' +
          '    <p>' + t("introDxNote") + '</p></div>' +
          '</div>'
      },
      {
        ms: 3500, caption: t("introScene4"),
        html: stagedOfficerHTML(d, false),
        after: function (stage) {
          // The officer confirms partway through the scene (or immediately if
          // motion is reduced), so the "confirmed" stamp lands during the beat.
          var portal = stage.querySelector(".intro-portal");
          var delay = reducedMotion() ? 0 : 1500;
          var tmr = setTimeout(function () { if (portal) portal.classList.add("confirmed"); }, delay);
          introState.subTimers.push(tmr);
        }
      },
      {
        ms: 4000, caption: t("introScene5"),
        html:
          '<div class="intro-map" role="img" aria-label="' + t("introScene5") + '">' +
          '  <span class="intro-radius" aria-hidden="true"></span>' +
          '  <span class="map-dot map-dot-source" style="left:50%;top:58%"><span class="map-label">Ramesh</span></span>' +
          '  <span class="map-dot map-dot-alert" style="left:50%;top:34%">' +
          '    <span class="map-label">Lakshmi · ' + d.crops.tomato + '</span>' +
          '    <span class="map-badge alert">' + t("introWarned") + '</span></span>' +
          '  <span class="map-dot map-dot-safe" style="left:78%;top:62%">' +
          '    <span class="map-label">Venkat · ' + d.crops.rice + '</span>' +
          '    <span class="map-badge safe">' + t("introSafe") + '</span></span>' +
          '  <span class="map-dot map-dot-safe" style="left:48%;top:94%">' +
          '    <span class="map-label">Sita</span>' +
          '    <span class="map-badge safe">' + t("introSafe") + '</span></span>' +
          '</div>'
      },
      {
        ms: 2600, caption: t("introScene6"),
        html:
          '<div class="intro-finale"><span class="intro-logo" aria-hidden="true">🌾</span>' +
          '<div class="intro-finale-title">KisanMate</div></div>'
      }
    ];
  }

  function startIntro(onDone) {
    introState.onDone = onDone || function () {};
    introState.index = -1;
    introState.subTimers = [];
    window.KM_SPEECH.stopSpeaking();
    var overlay = $("intro-overlay");
    overlay.hidden = false;
    overlay.classList.toggle("intro-reduced", reducedMotion());
    document.body.classList.add("intro-running");
    advanceIntro();
  }

  function advanceIntro() {
    clearTimeout(introState.timer);
    clearTimeout(introState.maxTimer);
    (introState.subTimers || []).forEach(clearTimeout);
    introState.subTimers = [];
    var myGen = ++introState.gen;

    introState.index += 1;
    var scenes = introScenes();
    if (introState.index >= scenes.length) { finishIntro(); return; }
    var scene = scenes[introState.index];

    var stage = $("intro-stage");
    stage.classList.remove("intro-enter");
    stage.innerHTML = scene.html;
    void stage.offsetWidth; // restart the enter transition
    stage.classList.add("intro-enter");
    if (scene.after) scene.after(stage);

    $("intro-caption").textContent = scene.caption;
    var scenesLen = scenes.length;
    $("intro-progress-fill").style.width = Math.round(((introState.index + 1) / scenesLen) * 100) + "%";

    // Advance only after BOTH the minimum beat time has passed AND the optional
    // voice-over has finished -- so the screen never changes mid-sentence. TTS is
    // optional: if unavailable, speak() calls back immediately and captions carry it.
    var isLast = introState.index === scenesLen - 1;
    var minDone = false, speechDone = false;
    function maybeNext() {
      if (myGen !== introState.gen) return;
      if (minDone && speechDone) { isLast ? finishIntro() : advanceIntro(); }
    }
    introState.timer = setTimeout(function () { minDone = true; maybeNext(); }, scene.ms);
    // Safety cap so a stuck speech engine can never freeze the intro.
    introState.maxTimer = setTimeout(function () {
      if (myGen === introState.gen) { isLast ? finishIntro() : advanceIntro(); }
    }, scene.ms + 12000);
    window.KM_SPEECH.stopSpeaking();
    window.KM_SPEECH.speak(scene.caption, localeTag(), function () { speechDone = true; maybeNext(); });
  }

  function finishIntro() {
    introState.gen++; // invalidate any pending scene callbacks
    clearTimeout(introState.timer);
    clearTimeout(introState.maxTimer);
    (introState.subTimers || []).forEach(clearTimeout);
    introState.subTimers = [];
    window.KM_SPEECH.stopSpeaking();
    localStorage.setItem(INTRO_SEEN_KEY, "1");
    $("intro-overlay").hidden = true;
    document.body.classList.remove("intro-running");
    var done = introState.onDone;
    introState.onDone = null;
    if (done) done();
  }

  function wireIntro() {
    // Skip is available at all times; skipping and finishing both go to login.
    $("intro-skip-btn").addEventListener("click", finishIntro);
  }

  // ---- boot -----------------------------------------------------------------------

  function init() {
    wireWelcome();
    wireLanguageCards();
    wireAuth();
    wireSetup();
    wireProfilePage();
    wireHeader();
    wireBottomNav();
    wireHome();
    wireDiagnose();
    wireRecommend();
    wireAlerts();
    wireReports();
    wireDemo();
    wireIntro();

    // Language is applied whenever it is known so onboarding screens are localized.
    if (state.lang) {
      applyStrings();
      rebuildRecommendOptionGroups();
      $("language-next-btn").disabled = false;
    }

    // localStorage session is the source of truth: signed in -> straight home;
    // otherwise start at the welcome landing and walk the onboarding flow.
    if (isSignedIn()) {
      showScreen("home");
      checkVerdictNotifications();  // one-time officer-verdict popup on reload
    } else {
      showScreen("welcome");
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
