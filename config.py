"""Shared runtime configuration read from environment variables."""
import os

from dotenv import load_dotenv

# Load a local .env for development. On Cloud Run there is no .env file and this
# is a no-op; override=False (the default) means real platform env vars always
# win, so this can never clobber the deployed configuration.
load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
