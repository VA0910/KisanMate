/**
 * RSK officer dashboard logic.
 * Talks to GET /api/cases, GET /api/alerts, POST /api/confirm.
 * English-only (officers are literate desk users), but keeps labels, keyboard
 * operability and visible focus. No framework, no build step.
 */
(function () {
  "use strict";

  var $ = document.getElementById.bind(document);

  // The five conditions from the Vision/Fusion contract (models.Condition).
  var CONDITIONS = ["late_blight", "early_blight", "nitrogen_deficiency", "healthy", "other"];
  var CONDITION_LABELS = {
    late_blight: "Late Blight",
    early_blight: "Early Blight",
    nitrogen_deficiency: "Nitrogen Deficiency",
    healthy: "Healthy",
    other: "Other"
  };
  var DECISION_LABELS = {
    advise: "AI advised the farmer directly",
    escalate_rsk: "AI escalated this for your review"
  };

  function conditionLabel(code) {
    if (!code) return "—";
    return CONDITION_LABELS[code] || code.replace(/_/g, " ");
  }

  function tmpl(id) {
    return $(id).content.firstElementChild;
  }
  function el(root, name) {
    return root.querySelector('[data-el="' + name + '"]');
  }

  // ---- network -------------------------------------------------------------

  function fetchJson(url, options, timeoutMs) {
    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var opts = options || {};
    if (controller) opts.signal = controller.signal;
    var timer = setTimeout(function () { if (controller) controller.abort(); }, timeoutMs || 15000);
    return fetch(url, opts)
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
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

  // ---- date ----------------------------------------------------------------

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

  // ---- panel state helpers -------------------------------------------------

  function showOnly(prefix, which) {
    ["loading", "empty", "error", "list"].forEach(function (name) {
      var node = $(prefix + "-" + name);
      if (node) node.hidden = name !== which;
    });
  }

  // ---- cases ---------------------------------------------------------------

  function loadCases() {
    showOnly("cases", "loading");
    fetchJson("/api/cases")
      .then(function (data) {
        var cases = (data && data.cases) || [];
        $("cases-count").textContent = cases.length ? String(cases.length) : "";
        if (!cases.length) {
          showOnly("cases", "empty");
          return;
        }
        var list = $("cases-list");
        list.innerHTML = "";
        cases.forEach(function (row) { list.appendChild(buildCaseCard(row)); });
        showOnly("cases", "list");
      })
      .catch(function () {
        $("cases-error-msg").textContent = "Couldn't load cases. Check the connection and try again.";
        showOnly("cases", "error");
      });
  }

  function buildCaseCard(row) {
    var card = tmpl("case-card-template").cloneNode(true);

    var status = el(card, "status");
    status.textContent = row.status;
    status.classList.add("status-" + row.status);

    el(card, "farmerName").textContent = row.farmer_name || row.farmer_id || "Unknown farmer";
    var cropBits = [];
    if (row.crop) cropBits.push(row.crop);
    el(card, "cropMeta").textContent = cropBits.length ? " · " + cropBits.join(" · ") : "";

    var locBits = [];
    if (row.mandal) locBits.push(row.mandal);
    if (row.location) locBits.push(row.location.lat.toFixed(4) + ", " + row.location.lng.toFixed(4));
    el(card, "location").textContent = locBits.join(" · ") || "Unknown";

    if (row.image_note) {
      el(card, "note").textContent = row.image_note;
    } else {
      el(card, "noteRow").hidden = true;
    }

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

    wireVerdict(card, row);
    return card;
  }

  function wireVerdict(card, row) {
    var form = el(card, "verdictForm");
    var confirmBtn = el(card, "confirmBtn");
    var overrideToggle = el(card, "overrideToggle");
    var overridePanel = el(card, "overridePanel");
    var overrideSelect = el(card, "overrideSelect");
    var overrideLabel = el(card, "overrideLabel");

    var aiTop = row.ai_top_condition;

    // "Confirm diagnosis" accepts the AI's top pick verbatim.
    if (aiTop) {
      el(card, "confirmLabel").textContent = "Confirm as " + conditionLabel(aiTop);
      confirmBtn.addEventListener("click", function () { submitVerdict(card, row.case_id, aiTop); });
    } else {
      confirmBtn.disabled = true;
    }

    // Override = pick a DIFFERENT condition than the AI's top pick.
    CONDITIONS.filter(function (c) { return c !== aiTop; }).forEach(function (c) {
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
    // lock the card's controls while the request is in flight
    card.querySelectorAll("button, select").forEach(function (n) { n.disabled = true; });

    fetchJson("/api/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case_id: caseId, officer_verdict: verdict })
    })
      .then(function (data) {
        toast(verdictToastMessage(data));
        var firedAlert = !!(data && data.alert);
        loadCases();
        loadAlerts(firedAlert);
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
    if (data.contagious) {
      return "Confirmed as " + cond + ". No nearby farmers matched — no alert fired.";
    }
    return "Confirmed as " + cond + ". Not contagious — no alert needed.";
  }

  // ---- alerts --------------------------------------------------------------

  function loadAlerts(focusAfter) {
    showOnly("alerts", "loading");
    fetchJson("/api/alerts")
      .then(function (data) {
        var alerts = (data && data.alerts) || [];
        $("alerts-count").textContent = alerts.length ? String(alerts.length) : "";
        if (!alerts.length) {
          showOnly("alerts", "empty");
          return;
        }
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
    $("refresh-btn").addEventListener("click", function () { loadCases(); loadAlerts(false); });
    document.querySelectorAll("[data-retry]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (btn.getAttribute("data-retry") === "cases") loadCases();
        else loadAlerts(false);
      });
    });
    loadCases();
    loadAlerts(false);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
