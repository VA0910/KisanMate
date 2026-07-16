/**
 * Speech I/O: speech-to-text stays on the browser's Web Speech API, but
 * text-to-speech now prefers the backend's Google Cloud TTS (POST /tts, see
 * tts.py) for higher-quality Hindi/Telugu/Indian-English voices, and falls
 * back to the browser's own speechSynthesis whenever the cloud call fails
 * for any reason (offline, slow, API not enabled, etc).
 *
 * Feature-detection happens once at load. Every entry point is safe to call
 * even when the underlying API is missing or throws at runtime -- callers
 * always get a callback, never an exception, so the rest of the app never
 * has to special-case "voice broke." This is what lets the UI silently fall
 * back to buttons/text instead of blocking the farmer.
 */
(function (global) {
  "use strict";

  var SpeechRecognitionCtor = global.SpeechRecognition || global.webkitSpeechRecognition || null;
  var sttAvailable = !!SpeechRecognitionCtor;
  var browserTtsAvailable = "speechSynthesis" in global && typeof SpeechSynthesisUtterance !== "undefined";
  var audioPlaybackAvailable = typeof global.Audio !== "undefined";
  // Whether *some* form of speaking-aloud is possible -- cloud TTS plays back
  // through a plain <audio> element, so this stays true even on the rare
  // browser that lacks speechSynthesis but can still play audio.
  var ttsAvailable = browserTtsAvailable || audioPlaybackAvailable;

  var CLOUD_TTS_TIMEOUT_MS = 8000;

  var activeRecognition = null;
  var activeAudio = null;

  // Farmer-controlled mute for text-to-speech only (speech-to-text/mic input is
  // unaffected). Persisted so it survives reload; read once at load so a farmer
  // who muted it doesn't get talked at again on the next visit.
  var MUTE_KEY = "kisanmate_tts_muted";
  var ttsMuted = false;
  try {
    ttsMuted = global.localStorage && global.localStorage.getItem(MUTE_KEY) === "1";
  } catch (err) {
    ttsMuted = false;
  }

  function isMuted() { return ttsMuted; }

  function setMuted(value) {
    ttsMuted = !!value;
    try {
      global.localStorage.setItem(MUTE_KEY, ttsMuted ? "1" : "0");
    } catch (err) {
      /* localStorage unavailable -- mute still applies for this session */
    }
    if (ttsMuted) stopSpeaking();
  }

  /**
   * Listens for a single utterance and reports back through callbacks.
   * onResult(transcript), onError(kind), onEnd() -- onEnd always fires last,
   * exactly once, whether recognition succeeded, failed, or was never
   * supported in the first place.
   */
  function listenOnce(localeTag, callbacks) {
    var onResult = (callbacks && callbacks.onResult) || function () {};
    var onError = (callbacks && callbacks.onError) || function () {};
    var onEnd = (callbacks && callbacks.onEnd) || function () {};

    if (!sttAvailable) {
      onError("unsupported");
      onEnd();
      return;
    }

    try {
      var recognition = new SpeechRecognitionCtor();
      activeRecognition = recognition;
      recognition.lang = localeTag;
      recognition.interimResults = false;
      recognition.maxAlternatives = 1;

      var ended = false;
      var finish = function () {
        if (ended) return;
        ended = true;
        activeRecognition = null;
        onEnd();
      };

      recognition.onresult = function (event) {
        try {
          var transcript = event.results[0][0].transcript || "";
          onResult(transcript.trim());
        } catch (err) {
          onError("no-match");
        }
      };

      recognition.onerror = function (event) {
        // event.error is one of: no-speech, audio-capture, not-allowed,
        // network, language-not-supported, aborted, service-not-allowed...
        onError(event && event.error ? event.error : "unknown");
      };

      recognition.onend = finish;

      recognition.start();
    } catch (err) {
      onError("unsupported");
      onEnd();
    }
  }

  function stopListening() {
    if (activeRecognition) {
      try {
        activeRecognition.abort();
      } catch (err) {
        /* already stopped -- nothing to do */
      }
      activeRecognition = null;
    }
  }

  function speakWithBrowserTts(text, localeTag, done) {
    if (!browserTtsAvailable) { done(); return; }
    try {
      global.speechSynthesis.cancel(); // never overlap two utterances
      var utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = localeTag;
      var fired = false;
      var finish = function () { if (fired) return; fired = true; done(); };
      utterance.onend = finish;
      utterance.onerror = finish;
      global.speechSynthesis.speak(utterance);
    } catch (err) {
      /* speaking is a nicety, never let it break the flow */
      done();
    }
  }

  function stopCloudAudio() {
    if (activeAudio) {
      try {
        activeAudio.pause();
        if (activeAudio.src) URL.revokeObjectURL(activeAudio.src);
      } catch (err) {
        /* ignore */
      }
      activeAudio = null;
    }
  }

  // Asks the backend to synthesize `text` with Google Cloud TTS and plays the
  // returned audio. Calls onFallback() -- never onDone directly -- on any
  // failure (network, timeout, non-2xx, playback error) so the caller always
  // retries with the browser's own TTS rather than leaving the farmer silent.
  function speakWithCloudTts(text, localeTag, lang, done, onFallback) {
    if (!audioPlaybackAvailable || typeof global.fetch !== "function") { onFallback(); return; }

    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timer = setTimeout(function () { if (controller) controller.abort(); }, CLOUD_TTS_TIMEOUT_MS);

    global.fetch("/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text, lang: lang }),
      signal: controller ? controller.signal : undefined
    }).then(function (response) {
      clearTimeout(timer);
      if (!response.ok) throw new Error("tts http " + response.status);
      return response.blob();
    }).then(function (blob) {
      stopCloudAudio();
      var audio = new global.Audio(URL.createObjectURL(blob));
      activeAudio = audio;
      var fired = false;
      var finish = function () {
        if (fired) return;
        fired = true;
        if (activeAudio === audio) activeAudio = null;
        done();
      };
      audio.onended = finish;
      audio.onerror = finish;
      audio.play().catch(function () { onFallback(); });
    }).catch(function () {
      clearTimeout(timer);
      onFallback();
    });
  }

  /** Speaks text aloud: Google Cloud TTS first, the browser's own TTS as the
   * fallback whenever the cloud call fails for any reason. A no-op (not an
   * error) when neither is available.
   *
   * The optional onEnd callback ALWAYS fires exactly once -- when speech ends
   * or errors, or immediately when TTS is unavailable / text is empty / muted.
   * Callers can use it to wait for the voice-over before advancing, without
   * ever blocking: captions must stand on their own if TTS never speaks.
   */
  function speak(text, localeTag, onEnd) {
    var done = typeof onEnd === "function" ? onEnd : function () {};
    if (!ttsAvailable || !text || ttsMuted) { done(); return; }

    var lang = (localeTag || "en-IN").split("-")[0];
    speakWithCloudTts(text, localeTag, lang, done, function fallback() {
      speakWithBrowserTts(text, localeTag, done);
    });
  }

  function stopSpeaking() {
    stopCloudAudio();
    if (browserTtsAvailable) {
      try {
        global.speechSynthesis.cancel();
      } catch (err) {
        /* ignore */
      }
    }
  }

  global.KM_SPEECH = {
    sttAvailable: sttAvailable,
    ttsAvailable: ttsAvailable,
    listenOnce: listenOnce,
    stopListening: stopListening,
    speak: speak,
    stopSpeaking: stopSpeaking,
    isMuted: isMuted,
    setMuted: setMuted
  };
})(window);
