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
  var APP_SCREENS = { home: true, diagnose: true, recommend: true, alerts: true };

  var SOIL_OPTIONS = ["alluvial", "black", "loamy", "red", "sandy"];
  var ZONE_OPTIONS = ["delta", "coastal", "upland", "semi_arid"];
  var RAINFALL_OPTIONS = ["low", "medium", "high"];
  var GROUNDWATER_OPTIONS = ["shallow", "medium", "deep"];
  var RAINFALL_MM = { low: 300, medium: 650, high: 1000 };
  var GROUNDWATER_M = { shallow: 3, medium: 10, deep: 20 };
  var CROP_EMOJI = { rice: "🌾", tomato: "🍅", chili: "🌶️", cotton: "🌱", groundnut: "🥜", maize: "🌽" };
  var TIER_ICON = { watch: "icon-info-circle", warning: "icon-alert-triangle", alert: "icon-alert-octagon" };

  var SECTION_IDS = {
    welcome: "screen-welcome",
    language: "screen-language",
    phone: "screen-phone",
    otp: "screen-otp",
    profile: "screen-profile",
    home: "screen-home",
    diagnose: "screen-diagnose",
    recommend: "screen-recommend",
    alerts: "screen-alerts"
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
    var signOutBtn = $("sign-out-btn");
    if (signOutBtn) signOutBtn.hidden = !isSignedIn();
  }

  function markCurrentLanguageCard() {
    document.querySelectorAll(".lang-card").forEach(function (btn) {
      if (btn.getAttribute("data-lang") === state.lang) btn.setAttribute("aria-current", "true");
      else btn.removeAttribute("aria-current");
    });
  }

  // ---- routing ---------------------------------------------------------------

  var SCREEN_ENTER_HOOKS = {
    diagnose: resetDiagnoseScreen,
    recommend: function () { showRecommendSubState("form"); },
    alerts: loadAlerts
  };

  function showScreen(name, skipHook) {
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

  function setIconUse(useEl, symbolId) {
    useEl.setAttribute("href", "#" + symbolId);
  }

  // ---- Home ---------------------------------------------------------------------

  function wireHome() {
    $("mic-btn").addEventListener("click", handleMicTap);
    $("go-diagnose-btn").addEventListener("click", function () { showScreen("diagnose"); });
    $("go-recommend-btn").addEventListener("click", function () { showScreen("recommend"); });

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
      if (!file) return;
      state.diagnosePhotoFile = file;
      $("photo-preview").src = URL.createObjectURL(file);
      $("photo-preview-wrap").hidden = false;
      $("submit-diagnose-btn").disabled = false;
    });
    $("change-photo-btn").addEventListener("click", function () { $("photo-input").click(); });
    $("submit-diagnose-btn").addEventListener("click", submitDiagnose);
    $("diagnose-retry-btn").addEventListener("click", submitDiagnose);
    $("new-photo-btn").addEventListener("click", resetDiagnoseScreen);
    $("not-right-btn").addEventListener("click", handleNotRight);
    $("play-audio-btn").addEventListener("click", function () {
      if (state.lastDiagnoseMessage) window.KM_SPEECH.speak(state.lastDiagnoseMessage, localeTag());
    });
    if (!window.KM_SPEECH.ttsAvailable) $("play-audio-btn").hidden = true;
  }

  function showDiagnoseSubState(name) {
    var map = { capture: "diagnose-capture", thinking: "diagnose-thinking", result: "diagnose-result", error: "diagnose-error" };
    Object.keys(map).forEach(function (k) { $(map[k]).hidden = k !== name; });
  }

  function resetDiagnoseScreen() {
    state.diagnosePhotoFile = null;
    state.lastDiagnoseCaseId = null;
    state.lastDiagnoseMessage = "";
    $("photo-input").value = "";
    $("photo-preview-wrap").hidden = true;
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
      conditionHeading.textContent = d.conditions[fusion.top] || fusion.top;

      confidenceBlock.hidden = false;
      var filled = Math.round((fusion.confidence || 0) * 5);
      renderSegments($("confidence-segments"), filled, 5);
      $("confidence-word").textContent = confidenceWord(fusion.confidence || 0);
      $("confidence-segments").setAttribute(
        "aria-label",
        t("confidenceLabel") + ": " + confidenceWord(fusion.confidence || 0) + ", " + Math.round((fusion.confidence || 0) * 100) + "%"
      );
    } else {
      pillText.textContent = t("statusEscalate");
      setIconUse(pillIcon, "icon-alert-triangle");
      $("result-status-pill").className = "status-pill status-attention";
      conditionHeading.hidden = true;
      confidenceBlock.hidden = true;
    }

    $("result-message").textContent = data.message || "";
    $("not-right-btn").hidden = !state.lastDiagnoseCaseId;
    $("dispute-thanks").hidden = true;

    showDiagnoseSubState("result");
    if (!state.demoActive) window.KM_SPEECH.speak(data.message, localeTag());
  }

  function handleNotRight() {
    if (!state.lastDiagnoseCaseId) return;
    var confirmed = window.confirm(t("disputeConfirmQuestion"));
    if (!confirmed) return;
    window.KM_API.confirm(state.lastDiagnoseCaseId, "farmer_disputed").catch(function () {});
    $("dispute-thanks").hidden = false;
    $("dispute-thanks").textContent = t("disputeThanks");
    $("not-right-btn").hidden = true;
    window.KM_SPEECH.speak(t("disputeThanks"), localeTag());
  }

  // ---- Recommend ----------------------------------------------------------------

  function buildOptionGroup(containerId, groupName, codes, labelDict) {
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
      if (state.recommendSelections[groupName] === code) input.checked = true;
      input.addEventListener("change", function () {
        state.recommendSelections[groupName] = code;
        $("recommend-hint").textContent = "";
      });

      var label = document.createElement("label");
      label.setAttribute("for", id);
      label.className = "chip-label";
      label.textContent = labelDict[code] || code;

      wrap.appendChild(input);
      wrap.appendChild(label);
      container.appendChild(wrap);
    });
  }

  function rebuildRecommendOptionGroups() {
    var d = dict();
    buildOptionGroup("soil-options", "soil", SOIL_OPTIONS, d.soils);
    buildOptionGroup("zone-options", "zone", ZONE_OPTIONS, d.zones);
    buildOptionGroup("rainfall-options", "rainfall", RAINFALL_OPTIONS, d.rainfall);
    buildOptionGroup("groundwater-options", "groundwater", GROUNDWATER_OPTIONS, d.groundwater);
  }

  function wireRecommend() {
    rebuildRecommendOptionGroups();
    $("recommend-form").addEventListener("submit", function (e) {
      e.preventDefault();
      var sel = state.recommendSelections;
      if (!sel.soil || !sel.zone || !sel.rainfall || !sel.groundwater) {
        $("recommend-hint").textContent = t("chooseAllHint");
        return;
      }
      submitRecommend();
    });
    $("recommend-start-over-btn").addEventListener("click", function () { showRecommendSubState("form"); });
    $("recommend-retry-btn").addEventListener("click", submitRecommend);
  }

  function showRecommendSubState(name) {
    var map = { form: "recommend-form", thinking: "recommend-thinking", result: "recommend-result", error: "recommend-error" };
    Object.keys(map).forEach(function (k) { $(map[k]).hidden = k !== name; });
  }

  function submitRecommend() {
    var sel = state.recommendSelections;
    var payload = {
      farmer_id: state.farmerId,
      soil: sel.soil,
      groundwater_depth_m: GROUNDWATER_M[sel.groundwater],
      agro_zone: sel.zone,
      seasonal_rainfall_mm: RAINFALL_MM[sel.rainfall]
    };
    showRecommendSubState("thinking");
    window.KM_API.recommend(payload)
      .then(renderRecommendResult)
      .catch(function (err) {
        $("recommend-error-msg").textContent = friendlyMessageFor(err, "recommendError");
        showRecommendSubState("error");
      });
  }

  function renderRecommendResult(data) {
    var d = dict();
    var list = $("recommend-list");
    list.innerHTML = "";
    var recs = data.recommendations || [];

    recs.forEach(function (rec, index) {
      var li = document.createElement("li");
      li.className = "crop-card" + (index === 0 ? " crop-card-best" : "");

      if (index === 0) {
        var badge = document.createElement("span");
        badge.className = "best-badge";
        badge.textContent = t("bestMatch");
        li.appendChild(badge);
      }

      var head = document.createElement("div");
      head.className = "crop-card-head";
      var emoji = document.createElement("span");
      emoji.className = "crop-emoji";
      emoji.setAttribute("aria-hidden", "true");
      emoji.textContent = CROP_EMOJI[rec.crop] || "🌱";
      var name = document.createElement("span");
      name.className = "crop-name";
      name.textContent = d.crops[rec.crop] || rec.crop;
      head.appendChild(emoji);
      head.appendChild(name);
      li.appendChild(head);

      var segWrap = document.createElement("div");
      segWrap.className = "segment-row";
      segWrap.setAttribute("role", "img");
      segWrap.setAttribute("aria-label", t("confidenceLabel") + ": " + rec.score + "/4");
      renderSegments(segWrap, rec.score, 4);
      li.appendChild(segWrap);

      var reasonText = d.reasons[rec.reason_code];
      if (reasonText) {
        var reason = document.createElement("p");
        reason.className = "crop-reason";
        reason.textContent = reasonText;
        li.appendChild(reason);
      }

      list.appendChild(li);
    });

    showRecommendSubState("result");
    if (recs.length > 0 && !state.demoActive) {
      var topName = d.crops[recs[0].crop] || recs[0].crop;
      window.KM_SPEECH.speak(t("recommendVoiceSummary")(topName), localeTag());
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
      title.textContent = (d.conditions[alert.condition] || alert.condition || "") + " " + t("inYourArea");
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
      showScreen(isSignedIn() ? "home" : "phone");
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

    $("profile-form").addEventListener("submit", function (e) {
      e.preventDefault();
      finishProfileSetup();
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
          $("profile-name-input").value = data.farmer.name || "";
          showScreen("profile");
          $("profile-name-input").focus();
        } else {
          showScreen("home");
        }
      })
      .catch(function () {
        $("otp-hint").textContent = t("otpInvalid");
      })
      .finally(function () { btn.disabled = false; });
  }

  function finishProfileSetup() {
    // Minimal setup for this step: capture the name locally so home greets them.
    // Full profile (location, soil, crops) is the dedicated profile-page step;
    // this persists to the session and re-feeds the greeting immediately.
    var name = ($("profile-name-input").value || "").trim();
    if (state.session && state.session.farmer) {
      state.session.farmer.name = name;
      saveSession(state.session.farmer);
    }
    applyStrings();
    showScreen("home");
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
    $("sign-out-btn").addEventListener("click", signOut);
  }

  function wireBottomNav() {
    document.querySelectorAll(".nav-btn").forEach(function (btn) {
      btn.addEventListener("click", function () { showScreen(btn.getAttribute("data-target")); });
    });
  }

  // ---- guided demo (the "Run demo scenario" walkthrough) --------------------------

  var DEMO_STEP_MS = 7500; // auto-advance pace; "Next" skips ahead, "Exit" stops
  var demoState = { narratorLang: "en", steps: [], index: -1, timer: null, data: null };

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
          renderRecommendResult(data.recommend);
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
          // The RSK confirmation is narrated over the diagnosis result; the real
          // confirm + alert already happened server-side in /api/demo/run.
        }
      },
      {
        caption: demoStr("demoStep4"),
        run: function () {
          applyDemoLang("te"); // Lakshmi's phone is in Telugu
          showScreen("alerts", true);
          renderAlerts({ alerts: data.alerts.lakshmi.alerts });
        },
        speakLang: "te",
        speakText: window.KM_STRINGS.te.alertsSummarySpoken(data.alerts.lakshmi.alerts.length)
      }
    ];
  }

  function advanceDemo() {
    clearTimeout(demoState.timer);
    demoState.index += 1;
    var step = demoState.steps[demoState.index];
    if (!step) return;

    setDemoCaption(demoStr("demoStepOf")(demoState.index + 1, demoState.steps.length), step.caption);
    try {
      step.run();
    } catch (err) {
      /* a rendering hiccup must not halt the walkthrough */
    }

    window.KM_SPEECH.stopSpeaking();
    var tag = window.KM_LOCALE_TAGS[step.speakLang || demoState.narratorLang] || "en-IN";
    window.KM_SPEECH.speak(step.speakText || step.caption, tag);

    updateDemoProgress();

    var isLast = demoState.index === demoState.steps.length - 1;
    if (isLast) {
      showDemoEndControls();
    } else {
      $("demo-next-btn").focus();
      demoState.timer = setTimeout(advanceDemo, DEMO_STEP_MS);
    }
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
    window.KM_SPEECH.stopSpeaking();
    state.demoActive = false;
    $("demo-overlay").hidden = true;
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

  // ---- boot -----------------------------------------------------------------------

  function init() {
    wireWelcome();
    wireLanguageCards();
    wireAuth();
    wireHeader();
    wireBottomNav();
    wireHome();
    wireDiagnose();
    wireRecommend();
    wireAlerts();
    wireDemo();

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
    } else {
      showScreen("welcome");
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
