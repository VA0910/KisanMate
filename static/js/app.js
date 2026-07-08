/**
 * App state machine: screen routing, i18n application, and all the
 * screen-specific wiring (Home voice router, Diagnose, Recommend, Alerts).
 * Talks to the backend only through window.KM_API; talks to voice only
 * through window.KM_SPEECH; never touches strings except through KM_STRINGS.
 */
(function () {
  "use strict";

  var $ = document.getElementById.bind(document);

  var DEFAULT_FARMER_ID = "ramesh";

  var SOIL_OPTIONS = ["alluvial", "black", "loamy", "red", "sandy"];
  var ZONE_OPTIONS = ["delta", "coastal", "upland", "semi_arid"];
  var RAINFALL_OPTIONS = ["low", "medium", "high"];
  var GROUNDWATER_OPTIONS = ["shallow", "medium", "deep"];
  var RAINFALL_MM = { low: 300, medium: 650, high: 1000 };
  var GROUNDWATER_M = { shallow: 3, medium: 10, deep: 20 };
  var CROP_EMOJI = { rice: "🌾", tomato: "🍅", chili: "🌶️", cotton: "🌱", groundnut: "🥜", maize: "🌽" };
  var TIER_ICON = { watch: "icon-info-circle", warning: "icon-alert-triangle", alert: "icon-alert-octagon" };

  var SECTION_IDS = {
    language: "screen-language",
    home: "screen-home",
    diagnose: "screen-diagnose",
    recommend: "screen-recommend",
    alerts: "screen-alerts"
  };

  var state = {
    lang: localStorage.getItem("kisanmate_lang"),
    farmerId: resolveFarmerId(),
    screen: null,
    micState: "idle",
    diagnosePhotoFile: null,
    lastDiagnoseCaseId: null,
    lastDiagnoseMessage: "",
    recommendSelections: { soil: null, zone: null, rainfall: null, groundwater: null }
  };

  if (state.lang && !window.KM_STRINGS[state.lang]) state.lang = null;

  function resolveFarmerId() {
    var params = new URLSearchParams(window.location.search);
    var fromQuery = params.get("farmer");
    if (fromQuery) {
      localStorage.setItem("kisanmate_farmer_id", fromQuery);
      return fromQuery;
    }
    return localStorage.getItem("kisanmate_farmer_id") || DEFAULT_FARMER_ID;
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
    document.title = d.appName + " – " + d.tagline;
    if (!window.KM_SPEECH.sttAvailable) {
      var micHint = $("mic-hint");
      if (micHint) micHint.textContent = t("micUnavailableHint");
    }
    markCurrentLanguageCard();
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

  function showScreen(name) {
    Object.keys(SECTION_IDS).forEach(function (key) {
      var el = $(SECTION_IDS[key]);
      if (el) el.hidden = key !== name;
    });
    state.screen = name;

    var bottomNav = $("bottom-nav");
    if (bottomNav) bottomNav.hidden = name === "language";
    document.querySelectorAll(".nav-btn").forEach(function (btn) {
      if (btn.getAttribute("data-target") === name) btn.setAttribute("aria-current", "page");
      else btn.removeAttribute("aria-current");
    });

    var hook = SCREEN_ENTER_HOOKS[name];
    if (hook) hook();

    var section = $(SECTION_IDS[name]);
    var heading = section && section.querySelector("h1, h2");
    if (heading) {
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
    window.KM_SPEECH.speak(data.message, localeTag());
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
    if (recs.length > 0) {
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
    window.KM_SPEECH.speak(t("alertsSummarySpoken")(alerts.length), localeTag());
  }

  function wireAlerts() {
    $("alerts-retry-btn").addEventListener("click", loadAlerts);
  }

  // ---- language picker + header + bottom nav -------------------------------------

  function wireLanguageCards() {
    document.querySelectorAll(".lang-card").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setLang(btn.getAttribute("data-lang"));
        showScreen("home");
      });
    });
  }

  function wireHeader() {
    $("change-language-btn").addEventListener("click", function () { showScreen("language"); });
  }

  function wireBottomNav() {
    document.querySelectorAll(".nav-btn").forEach(function (btn) {
      btn.addEventListener("click", function () { showScreen(btn.getAttribute("data-target")); });
    });
  }

  // ---- boot -----------------------------------------------------------------------

  function init() {
    wireLanguageCards();
    wireHeader();
    wireBottomNav();
    wireHome();
    wireDiagnose();
    wireRecommend();
    wireAlerts();

    if (state.lang) {
      applyStrings();
      rebuildRecommendOptionGroups();
      showScreen("home");
    } else {
      showScreen("language");
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
