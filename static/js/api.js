/**
 * Fetch wrappers for the existing /api endpoints.
 *
 * Every function here resolves with parsed JSON or rejects with a
 * KMApiError carrying a `kind` ("timeout" | "offline" | "server") that the
 * UI maps to one friendly, localized sentence -- callers never see a raw
 * network error, HTTP status, or stack trace (PROJECT_SPEC.md's "never show
 * the farmer an error or a stack trace," applied to the frontend too).
 */
(function (global) {
  "use strict";

  function KMApiError(kind, detail) {
    this.name = "KMApiError";
    this.kind = kind; // "timeout" | "offline" | "server"
    this.message = detail || kind;
  }
  KMApiError.prototype = Object.create(Error.prototype);

  function requestJson(url, options, timeoutMs) {
    if (global.navigator && global.navigator.onLine === false) {
      return Promise.reject(new KMApiError("offline"));
    }

    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timer = null;
    var opts = options || {};
    if (controller) opts.signal = controller.signal;

    var timeoutPromise = new Promise(function (resolve, reject) {
      timer = setTimeout(function () {
        if (controller) controller.abort();
        reject(new KMApiError("timeout"));
      }, timeoutMs || 20000);
    });

    var fetchPromise = fetch(url, opts).then(
      function (response) {
        clearTimeout(timer);
        if (!response.ok) {
          throw new KMApiError("server", "HTTP " + response.status);
        }
        return response.json().catch(function () {
          throw new KMApiError("server", "invalid JSON");
        });
      },
      function (err) {
        clearTimeout(timer);
        if (err instanceof KMApiError) throw err;
        throw new KMApiError("offline", err && err.message);
      }
    );

    return Promise.race([fetchPromise, timeoutPromise]).finally(function () {
      clearTimeout(timer);
    });
  }

  function diagnose(farmerId, imageFile, imageNote) {
    var form = new FormData();
    form.append("farmer_id", farmerId);
    form.append("image", imageFile);
    if (imageNote) form.append("image_note", imageNote);
    return requestJson("/api/diagnose", { method: "POST", body: form }, 30000);
  }

  function recommend(payload) {
    return requestJson(
      "/api/recommend",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      },
      15000
    );
  }

  function getAlerts(farmerId) {
    return requestJson("/api/alerts/" + encodeURIComponent(farmerId), { method: "GET" }, 15000);
  }

  function demoRun(lang) {
    return requestJson(
      "/api/demo/run",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lang: lang || "en" })
      },
      30000
    );
  }

  function confirm(caseId, officerVerdict) {
    return requestJson(
      "/api/confirm",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ case_id: caseId, officer_verdict: officerVerdict })
      },
      15000
    );
  }

  global.KM_API = {
    KMApiError: KMApiError,
    diagnose: diagnose,
    recommend: recommend,
    getAlerts: getAlerts,
    confirm: confirm,
    demoRun: demoRun
  };
})(window);
