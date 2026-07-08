"""Shared runtime configuration read from environment variables (and, optionally,
Google Secret Manager for the Gemini key)."""
import logging
import os

from dotenv import load_dotenv
from google import genai

# Load a local .env for development. On Cloud Run there is no .env file and this
# is a no-op; override=False (the default) means real platform env vars always
# win, so this can never clobber the deployed configuration.
load_dotenv()

_log = logging.getLogger("kisanmate.config")


def _resolve_secret_version(ref: str, project: str | None) -> str | None:
    """Turn a secret reference into a full Secret Manager version resource name.

    Accepts either a full resource name
    (`projects/<p>/secrets/<name>/versions/latest`) or a short secret id
    (`gemini-api-key`), in which case it's resolved against `project`.
    """
    if "/secrets/" in ref:  # already a resource name
        return ref if "/versions/" in ref else ref.rstrip("/") + "/versions/latest"
    if not project:
        _log.warning("GEMINI_API_KEY_SECRET is a short id but GOOGLE_CLOUD_PROJECT is unset; cannot resolve it.")
        return None
    return f"projects/{project}/secrets/{ref}/versions/latest"


def _load_secret_value(ref: str, project: str | None) -> str | None:
    """Read a secret value from Google Secret Manager. Returns None (with a logged
    warning) on any failure, so a misconfigured secret never crashes startup --
    the app just degrades to its deterministic fallbacks."""
    name = _resolve_secret_version(ref, project)
    if not name:
        return None
    try:
        from google.cloud import secretmanager  # lazy: only needed when configured

        client = secretmanager.SecretManagerServiceClient()
        response = client.access_secret_version(name=name)
        return response.payload.data.decode("utf-8").strip()
    except Exception as exc:  # missing package, permissions, network, no such secret...
        _log.warning("Could not load Gemini key from Secret Manager (%s): %s", name, exc)
        return None


def _gemini_api_key() -> str | None:
    """Resolve the Gemini API key.

    Precedence:
      1. GEMINI_API_KEY env var (local dev / explicit override), else
      2. Google Secret Manager, when GEMINI_API_KEY_SECRET names a secret.
    """
    direct = os.environ.get("GEMINI_API_KEY")
    if direct:
        return direct

    secret_ref = os.environ.get("GEMINI_API_KEY_SECRET")
    if secret_ref:
        project = os.environ.get("GEMINI_API_KEY_SECRET_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        return _load_secret_value(secret_ref, project)

    return None


GEMINI_API_KEY = _gemini_api_key()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# --- Gemini client backend ----------------------------------------------------
# The Developer API (AI Studio, used via GEMINI_API_KEY) is explicitly excluded
# from Google Cloud Free Trial credit coverage and bills straight to the payment
# method. Vertex AI is a native Google Cloud service, so the same Gemini models
# called through it draw from Free Trial/billing account credits instead. Default
# to Vertex AI whenever a GCP project is configured; fall back to the API key
# otherwise (e.g. local dev with no GCP project set up).
GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
GEMINI_VERTEX_LOCATION = os.environ.get("GEMINI_VERTEX_LOCATION", "asia-south1")
GEMINI_USE_VERTEXAI = os.environ.get("GEMINI_USE_VERTEXAI", "true").lower() in ("1", "true", "yes")


def get_genai_client() -> genai.Client:
    """Build the Gemini client for the configured backend (Vertex AI or AI Studio)."""
    if GEMINI_USE_VERTEXAI and GOOGLE_CLOUD_PROJECT:
        return genai.Client(vertexai=True, project=GOOGLE_CLOUD_PROJECT, location=GEMINI_VERTEX_LOCATION)
    return genai.Client(api_key=GEMINI_API_KEY)
