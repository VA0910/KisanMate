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

  function KMApiError(kind, detail, status) {
    this.name = "KMApiError";
    this.kind = kind; // "timeout" | "offline" | "server"
    this.status = status || null; // HTTP status when kind === "server"
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
          throw new KMApiError("server", "HTTP " + response.status, response.status);
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

  function requestOtp(phone) {
    return requestJson(
      "/api/auth/request-otp",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone: phone })
      },
      15000
    );
  }

  function verifyOtp(phone, code, lang) {
    return requestJson(
      "/api/auth/verify-otp",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone: phone, code: code, lang: lang || "en" })
      },
      15000
    );
  }

  function getFarmer(farmerId) {
    return requestJson("/api/farmers/" + encodeURIComponent(farmerId), { method: "GET" }, 15000);
  }

  function updateFarmer(farmerId, payload) {
    return requestJson(
      "/api/farmers/" + encodeURIComponent(farmerId),
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      },
      15000
    );
  }

  function getCrops() {
    return requestJson("/api/crops", { method: "GET" }, 15000);
  }

  function searchPlaces(query) {
    return requestJson(
      "/api/places?q=" + encodeURIComponent(query),
      { method: "GET" },
      10000
    );
  }

  // Best-effort telemetry (e.g. the silent geolocation fallback). Never rejects
  // to the caller -- a logging failure must not disrupt the farmer's flow.
  function logTelemetry(payload) {
    return requestJson(
      "/api/telemetry",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      },
      8000
    ).catch(function () { return null; });
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

  function demoReset() {
    return requestJson("/api/demo/reset", { method: "POST" }, 30000);
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
    requestOtp: requestOtp,
    verifyOtp: verifyOtp,
    getFarmer: getFarmer,
    updateFarmer: updateFarmer,
    getCrops: getCrops,
    searchPlaces: searchPlaces,
    logTelemetry: logTelemetry,
    diagnose: diagnose,
    recommend: recommend,
    getAlerts: getAlerts,
    confirm: confirm,
    demoRun: demoRun,
    demoReset: demoReset
  };
})(window);
