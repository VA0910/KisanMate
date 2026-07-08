/**
 * RSK officer portal logic (served at /admin).
 * Separate officer login + session from the farmer app; talks to
 * GET /api/cases, GET /api/alerts, POST /api/confirm, POST /api/admin/login.
 * English-only desk app: labels, keyboard operability, visible focus.
 */
(function () {
  "use strict";

  var $ = document.getElementById.bind(document);
  var ADMIN_KEY = "kisanmate_admin"; // officer session, distinct from the farmer's

  var CONDITIONS = ["late_blight", "early_blight", "nitrogen_deficiency", "healthy", "other"];
  var CONDITION_LABELS = {
    late_blight: "Late Blight", early_blight: "Early Blight",
    nitrogen_deficiency: "Nitrogen Deficiency", healthy: "Healthy", other: "Other"
  };
  var DECISION_LABELS = {
    advise: "AI advised the farmer directly",
    escalate_rsk: "AI escalated this for your review"
  };
  // Which case statuses belong under each tab.
  var CATEGORY_STATUSES = { review: ["escalated", "pending"], disputed: ["disputed"] };

  var state = { cases: [], category: "review" };

  function conditionLabel(code) {
    if (!code) return "—";
    return CONDITION_LABELS[code] || code.replace(/_/g, " ");
  }
  function tmpl(id) { return $(id).content.firstElementChild; }
  function el(root, name) { return root.querySelector('[data-el="' + name + '"]'); }

  // ---- network -------------------------------------------------------------

  function fetchJson(url, options, timeoutMs) {
    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var opts = options || {};
    if (controller) opts.signal = controller.signal;
    var timer = setTimeout(function () { if (controller) controller.abort(); }, timeoutMs || 15000);
    return fetch(url, opts)
      .then(function (r) {
        if (!r.ok) { var e = new Error("HTTP " + r.status); e.status = r.status; throw e; }
        return r.json();
      })
      .finally(function () { clearTimeout(timer); });
  }

  // ---- toast ---------------------------------------------------------------

  var toastTimer = null;
  function toast(msg, isError) {
    var t = $("toast");
    t.textContent = msg;
    t.classList.toggle("toast-error", !!isError);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, 6000);
  }

  function relativeTime(iso) {
    if (!iso) return "";
    var date = new Date(iso);
    if (isNaN(date.getTime())) return "";
    var secs = Math.round((Date.now() - date.getTime()) / 1000);
    if (secs < 60) return "just now";
    var mins = Math.round(secs / 60);
    if (mins < 60) return mins + (mins === 1 ? " minute ago" : " minutes ago");
    var hrs = Math.round(mins / 60);
    if (hrs < 24) return hrs + (hrs === 1 ? " hour ago" : " hours ago");
    var days = Math.round(hrs / 24);
    return days + (days === 1 ? " day ago" : " days ago");
  }

  function showOnly(prefix, which) {
    ["loading", "empty", "error", "list"].forEach(function (name) {
      var node = $(prefix + "-" + name);
      if (node) node.hidden = name !== which;
    });
  }

  // ---- auth (officer login, separate session) ------------------------------

  function isSignedIn() { return !!localStorage.getItem(ADMIN_KEY); }

  function showLogin() {
    $("admin-login").hidden = false;
    $("admin-portal").hidden = true;
    $("admin-username").focus();
  }

  function showPortal() {
    $("admin-login").hidden = true;
    $("admin-portal").hidden = false;
    loadCases();
    loadAlerts(false);
  }

  function loadDemoCredentials() {
    fetchJson("/api/admin/demo-credentials")
      .then(function (d) {
        $("demo-user").textContent = d.username;
        $("demo-pass").textContent = d.password;
      })
      .catch(function () { /* the login still works if this display fails */ });
  }

  function wireLogin() {
    $("admin-login-form").addEventListener("submit", function (e) {
      e.preventDefault();
      var btn = $("admin-login-btn");
      $("admin-login-error").hidden = true;
      btn.disabled = true;
      fetchJson("/api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: $("admin-username").value.trim(),
          password: $("admin-password").value
        })
      })
        .then(function (data) {
          localStorage.setItem(ADMIN_KEY, data.token || "officer");
          showPortal();
        })
        .catch(function (err) {
          var msg = err && err.status === 401
            ? "Incorrect username or password."
            : "Couldn't sign in. Please try again.";
          $("admin-login-error").textContent = msg;
          $("admin-login-error").hidden = false;
        })
        .finally(function () { btn.disabled = false; });
    });
  }

  function signOut() {
    localStorage.removeItem(ADMIN_KEY);
    $("admin-password").value = "";
    showLogin();
  }

  // ---- cases (two categories) ----------------------------------------------

  function loadCases() {
    showOnly("cases", "loading");
    fetchJson("/api/cases")
      .then(function (data) {
        state.cases = (data && data.cases) || [];
        updateCounts();
        renderCases();
      })
      .catch(function () {
        $("cases-error-msg").textContent = "Couldn't load cases. Check the connection and try again.";
        showOnly("cases", "error");
      });
  }

  function casesFor(category) {
    var allowed = CATEGORY_STATUSES[category] || [];
    return state.cases.filter(function (c) { return allowed.indexOf(c.status) !== -1; });
  }

  function updateCounts() {
    $("count-review").textContent = casesFor("review").length || "";
    $("count-disputed").textContent = casesFor("disputed").length || "";
  }

  function renderCases() {
    var rows = casesFor(state.category);
    if (!rows.length) {
      $("cases-empty-msg").textContent = state.category === "disputed"
        ? "No farmer-disputed cases right now."
        : "No cases awaiting review right now.";
      showOnly("cases", "empty");
      return;
    }
    var list = $("cases-list");
    list.innerHTML = "";
    rows.forEach(function (row) { list.appendChild(buildCaseCard(row)); });
    showOnly("cases", "list");
  }

  function selectCategory(category) {
    state.category = category;
    ["review", "disputed"].forEach(function (c) {
      $("tab-" + c).setAttribute("aria-selected", String(c === category));
    });
    renderCases();
  }

  function buildCaseCard(row) {
    var card = tmpl("case-card-template").cloneNode(true);

    var status = el(card, "status");
    status.textContent = row.status;
    status.classList.add("status-" + row.status);

    el(card, "farmerName").textContent = row.farmer_name || row.farmer_id || "Unknown farmer";
    el(card, "cropMeta").textContent = row.crop ? " · " + row.crop : "";

    el(card, "disputeNote").hidden = row.status !== "disputed";

    var photo = el(card, "photo");
    if (row.photo) { photo.src = row.photo; photo.hidden = false; }

    var locBits = [];
    if (row.mandal) locBits.push(row.mandal);
    if (row.location) locBits.push(row.location.lat.toFixed(4) + ", " + row.location.lng.toFixed(4));
    el(card, "location").textContent = locBits.join(" · ") || "Unknown";

    if (row.image_note) el(card, "note").textContent = row.image_note;
    else el(card, "noteRow").hidden = true;

    // When the AI never read the photo (vision unavailable), the fusion top is a
    // prior-only ENVIRONMENT estimate, not a diagnosis of the image. Don't present
    // it as a confident AI verdict -- say so plainly and have the officer diagnose.
    var analyzed = row.photo_analyzed !== false;
    var confRow = card.querySelector(".conf-row");

    if (analyzed) {
      el(card, "aiCondition").textContent = conditionLabel(row.ai_top_condition);
      var conf = typeof row.ai_confidence === "number" ? row.ai_confidence : 0;
      var pct = Math.round(conf * 100);
      el(card, "confFill").style.width = pct + "%";
      el(card, "confNum").textContent = pct + "%";
      el(card, "confBar").setAttribute("aria-label", "AI confidence: " + pct + " percent");

      var decision = el(card, "decision");
      if (row.ai_decision) {
        decision.textContent = DECISION_LABELS[row.ai_decision] || row.ai_decision;
        decision.classList.add(row.ai_decision === "advise" ? "decision-advise" : "decision-escalate");
      } else {
        decision.hidden = true;
      }

      var candidates = row.candidates || [];
      if (candidates.length) {
        var cl = el(card, "candidates");
        candidates.slice().sort(function (a, b) { return (b.confidence || 0) - (a.confidence || 0); })
          .forEach(function (c) {
            var li = document.createElement("li");
            li.className = "candidate";
            li.textContent = conditionLabel(c.condition) + " — " + Math.round((c.confidence || 0) * 100) + "%";
            cl.appendChild(li);
          });
      } else {
        el(card, "candidatesWrap").hidden = true;
      }

      var symptoms = row.visible_symptoms || [];
      if (symptoms.length) {
        var chips = el(card, "symptoms");
        symptoms.forEach(function (s) {
          var chip = document.createElement("span");
          chip.className = "chip";
          chip.textContent = s;
          chips.appendChild(chip);
        });
      } else {
        el(card, "symptomsWrap").hidden = true;
      }
    } else {
      el(card, "aiCondition").textContent = "Photo not analysed";
      if (confRow) confRow.hidden = true;
      var decisionEl = el(card, "decision");
      decisionEl.textContent = "The AI couldn't read this photo, so it was escalated — please set the diagnosis.";
      decisionEl.classList.add("decision-escalate");
      el(card, "candidatesWrap").hidden = true;
      el(card, "symptomsWrap").hidden = true;
    }

    wireVerdict(card, row, analyzed);
    return card;
  }

  function wireVerdict(card, row, analyzed) {
    var form = el(card, "verdictForm");
    var confirmBtn = el(card, "confirmBtn");
    var overrideToggle = el(card, "overrideToggle");
    var overridePanel = el(card, "overridePanel");
    var overrideSelect = el(card, "overrideSelect");
    var overrideLabel = el(card, "overrideLabel");
    var aiTop = row.ai_top_condition;

    // Only offer "Confirm the AI's pick" when the AI actually read the photo.
    if (analyzed && aiTop) {
      el(card, "confirmLabel").textContent = "Confirm as " + conditionLabel(aiTop);
      confirmBtn.addEventListener("click", function () { submitVerdict(card, row.case_id, aiTop); });
    } else {
      confirmBtn.hidden = true;
      overrideToggle.textContent = "";
      var oi = document.createElement("span");
      oi.textContent = "Set diagnosis";
      overrideToggle.appendChild(oi);
    }

    // Override options include this crop's candidate diseases (from the case's
    // ranked candidates) plus the base conditions -- so the officer can pick a
    // crop-specific disease, not just the hardcoded tomato set.
    var allConds = [];
    (row.candidates || []).map(function (c) { return c.condition; })
      .concat(CONDITIONS)
      .forEach(function (c) { if (c && allConds.indexOf(c) === -1) allConds.push(c); });
    // Exclude the AI's top only when it actually analysed the photo (Confirm covers it).
    var choices = (analyzed && aiTop) ? allConds.filter(function (c) { return c !== aiTop; }) : allConds;
    choices.forEach(function (c) {
      var opt = document.createElement("option");
      opt.value = c;
      opt.textContent = conditionLabel(c);
      overrideSelect.appendChild(opt);
    });
    overrideLabel.setAttribute("for", "override-" + row.case_id);
    overrideSelect.id = "override-" + row.case_id;

    overrideToggle.addEventListener("click", function () {
      var open = !overridePanel.hidden;
      overridePanel.hidden = open;
      overrideToggle.setAttribute("aria-expanded", String(!open));
      if (!open) overrideSelect.focus();
    });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      submitVerdict(card, row.case_id, overrideSelect.value);
    });
  }

  function submitVerdict(card, caseId, verdict) {
    card.querySelectorAll("button, select").forEach(function (n) { n.disabled = true; });
    fetchJson("/api/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case_id: caseId, officer_verdict: verdict })
    })
      .then(function (data) {
        toast(verdictToastMessage(data));
        loadCases();
        loadAlerts(!!(data && data.alert));
      })
      .catch(function () {
        card.querySelectorAll("button, select").forEach(function (n) { n.disabled = false; });
        toast("Couldn't save the verdict. Please try again.", true);
      });
  }

  function verdictToastMessage(data) {
    var cond = conditionLabel(data.condition);
    if (data.alert) {
      var names = (data.alert.recipients || []).map(function (r) { return r.name; }).join(", ");
      var count = data.alert.recipient_count;
      var who = names ? " (" + names + ")" : "";
      return "Confirmed as " + cond + ". " + data.alert.tier.toUpperCase() +
        " alert fired to " + count + (count === 1 ? " farmer" : " farmers") + who + ".";
    }
    if (data.contagious) return "Confirmed as " + cond + ". No nearby farmers matched — no alert fired.";
    return "Confirmed as " + cond + ". Not contagious — no alert needed.";
  }

  // ---- alerts --------------------------------------------------------------

  function loadAlerts(focusAfter) {
    showOnly("alerts", "loading");
    fetchJson("/api/alerts")
      .then(function (data) {
        var alerts = (data && data.alerts) || [];
        $("alerts-count").textContent = alerts.length ? String(alerts.length) : "";
        if (!alerts.length) { showOnly("alerts", "empty"); return; }
        var list = $("alerts-list");
        list.innerHTML = "";
        alerts.forEach(function (a) { list.appendChild(buildAlertCard(a)); });
        showOnly("alerts", "list");
        if (focusAfter) {
          var heading = $("alerts-heading");
          heading.setAttribute("tabindex", "-1");
          heading.focus();
        }
      })
      .catch(function () {
        $("alerts-error-msg").textContent = "Couldn't load alerts. Check the connection and try again.";
        showOnly("alerts", "error");
      });
  }

  function buildAlertCard(a) {
    var card = tmpl("alert-card-template").cloneNode(true);
    var tier = a.tier || "watch";
    card.classList.add("tier-" + tier);
    var tierChip = el(card, "tier");
    tierChip.textContent = tier;
    tierChip.classList.add("tier-" + tier);
    el(card, "condition").textContent = conditionLabel(a.condition) + " in the area";

    var names = (a.recipients || []).map(function (r) { return r.name; });
    var count = a.recipient_count != null ? a.recipient_count : names.length;
    var recipientsText = count + (count === 1 ? " farmer notified" : " farmers notified");
    if (names.length) recipientsText += ": " + names.join(", ");
    el(card, "recipients").textContent = recipientsText;

    var metaBits = [];
    if (typeof a.radius_km === "number") metaBits.push("within " + Math.round(a.radius_km) + " km");
    var rel = relativeTime(a.created_at);
    if (rel) metaBits.push(rel);
    el(card, "meta").textContent = metaBits.join(" · ");
    return card;
  }

  // ---- boot ----------------------------------------------------------------

  function init() {
    wireLogin();
    loadDemoCredentials();
    $("admin-signout-btn").addEventListener("click", signOut);
    $("refresh-btn").addEventListener("click", function () { loadCases(); loadAlerts(false); });
    document.querySelectorAll(".tab").forEach(function (tab) {
      tab.addEventListener("click", function () { selectCategory(tab.getAttribute("data-category")); });
    });
    document.querySelectorAll("[data-retry]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (btn.getAttribute("data-retry") === "cases") loadCases();
        else loadAlerts(false);
      });
    });

    if (isSignedIn()) showPortal();
    else showLogin();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
