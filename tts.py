"""Google Cloud Text-to-Speech (PROJECT_SPEC.md AI content layer helper).

Synthesizes a farmer-facing answer to speech using Google Cloud's TTS voices,
which read Hindi/Telugu/Indian-English far more naturally than most devices'
built-in TTS engine. This is a nicety, never load-bearing: if Cloud TTS is
unreachable, misconfigured, or the API isn't enabled, synthesize_speech raises
TtsError and the frontend (speech.js) silently falls back to the browser's own
Web Speech API -- a farmer must never be left without an answer just because
this optional upgrade failed.
"""
import logging

_log = logging.getLogger("kisanmate.tts")

# Only language_code + gender are set (no specific voice name): this auto-selects
# whatever voice Google Cloud currently offers for that locale, so the app never
# breaks if a named voice is renamed or deprecated upstream.
_LANGUAGE_CODES = {"en": "en-IN", "hi": "hi-IN", "te": "te-IN"}


class TtsError(Exception):
    """Raised when Cloud TTS synthesis fails for any reason. Callers (main.py)
    turn this into a 503 so the frontend falls back to browser TTS."""


def synthesize_speech(text: str, lang: str) -> bytes:
    """Return MP3 audio bytes for `text`, spoken in `lang` ("en"/"hi"/"te").

    Speaks the exact text passed in -- never rephrases or invents anything.
    Raises TtsError on any failure (missing package/credentials, API not
    enabled, network, empty text).
    """
    text = (text or "").strip()
    if not text:
        raise TtsError("empty text")

    try:
        from google.cloud import texttospeech
    except Exception as exc:  # package not installed
        raise TtsError(f"google-cloud-texttospeech not available: {exc}") from exc

    language_code = _LANGUAGE_CODES.get(lang, _LANGUAGE_CODES["en"])
    try:
        client = texttospeech.TextToSpeechClient()
        response = client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=text),
            voice=texttospeech.VoiceSelectionParams(
                language_code=language_code,
                ssml_gender=texttospeech.SsmlVoiceGender.FEMALE,
            ),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=0.95,
            ),
        )
        return response.audio_content
    except Exception as exc:
        raise TtsError(f"Cloud TTS synthesis failed: {exc}") from exc
