/**
 * /log telemetry view. Polls GET /api/telemetry and renders a table.
 * Fallback rows are flagged with text + colour (never colour alone).
 */
(function () {
  "use strict";

  var $ = document.getElementById.bind(document);
  var POLL_MS = 5000;
  var pollTimer = null;

  function show(which) {
    ["loading", "empty", "error", "table-wrap"].forEach(function (id) {
      $(id).hidden = id !== which;
    });
  }

  function formatTime(iso) {
    if (!iso) return "—";
    var date = new Date(iso);
    if (isNaN(date.getTime())) return String(iso);
    return date.toLocaleString();
  }

  function detailText(detail) {
    if (!detail || typeof detail !== "object") return "";
    if (typeof detail.error === "string") return detail.error;
    try {
      return JSON.stringify(detail);
    } catch (e) {
      return "";
    }
  }

  function render(entries) {
    $("count").textContent = entries.length ? String(entries.length) : "";
    if (!entries.length) {
      show("empty");
      return;
    }

    var body = $("log-body");
    body.innerHTML = "";
    entries.forEach(function (e) {
      var tr = document.createElement("tr");
      if (e.fallback_used) tr.className = "is-fallback";

      var time = document.createElement("td");
      time.className = "time";
      time.textContent = formatTime(e.created_at);

      var evt = document.createElement("td");
      evt.className = "evt";
      evt.textContent = e.event || "—";

      var layer = document.createElement("td");
      var layerTag = document.createElement("span");
      layerTag.className = "layer-tag";
      layerTag.textContent = e.layer || "—";
      layer.appendChild(layerTag);

      var flagCell = document.createElement("td");
      var flag = document.createElement("span");
      if (e.fallback_used) {
        flag.className = "flag flag-fallback";
        flag.textContent = "⚠ Fallback";
      } else {
        flag.className = "flag flag-normal";
        flag.textContent = "✓ Normal";
      }
      flagCell.appendChild(flag);

      var detail = document.createElement("td");
      detail.className = "detail";
      detail.textContent = detailText(e.detail);

      tr.appendChild(time);
      tr.appendChild(evt);
      tr.appendChild(layer);
      tr.appendChild(flagCell);
      tr.appendChild(detail);
      body.appendChild(tr);
    });
    show("table-wrap");
  }

  function load(showSpinner) {
    if (showSpinner) show("loading");
    fetch("/api/telemetry")
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        render((data && data.entries) || []);
      })
      .catch(function () {
        $("error-msg").textContent = "Couldn't load telemetry. Check the connection and try again.";
        show("error");
      });
  }

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(function () {
      // don't fight the tab when it's backgrounded
      if (!document.hidden) load(false);
    }, POLL_MS);
  }
  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  function init() {
    $("refresh-btn").addEventListener("click", function () { load(true); });
    $("error-retry").addEventListener("click", function () { load(true); });
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) load(false);
    });
    load(true);
    startPolling();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
