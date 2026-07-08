/**
 * Thin wrapper around the Web Speech API (speech-to-text + text-to-speech).
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
  var ttsAvailable = "speechSynthesis" in global && typeof SpeechSynthesisUtterance !== "undefined";

  var activeRecognition = null;

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

  /** Speaks text aloud. A no-op (not an error) when TTS isn't available. */
  function speak(text, localeTag) {
    if (!ttsAvailable || !text) return;
    try {
      global.speechSynthesis.cancel(); // never overlap two utterances
      var utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = localeTag;
      global.speechSynthesis.speak(utterance);
    } catch (err) {
      /* speaking is a nicety, never let it break the flow */
    }
  }

  function stopSpeaking() {
    if (ttsAvailable) {
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
    stopSpeaking: stopSpeaking
  };
})(window);
