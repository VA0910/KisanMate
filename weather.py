"""Weather sourcing (PROJECT_SPEC.md context layer + layer-3 fallback).

Live current weather comes from Open-Meteo -- a free, keyless API -- so the
diagnosis context uses REAL temperature/humidity/recent-rain for the farmer's
location instead of hardcoded values. If the fetch fails or times out we raise
WeatherError and the caller degrades to zone_normal_weather(), logging the
fallback -- the same graceful path a judge sees when "weather is removed".

get_forecast() is the same idea for a 7-day rain outlook, used only to power
the irrigation dry-spell reminder (engine.reminders) -- it also raises
WeatherError on failure, and the caller there simply keeps the seeded,
cadence-only reminder unchanged.
"""
import json
import urllib.parse
import urllib.request

from models import Forecast, Weather

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# A 7-day outlook under this much rain, with a low peak rain chance, reads as a
# dry spell worth an unscheduled irrigation nudge (engine.reminders).
DRY_SPELL_MM = 5.0
DRY_SPELL_RAIN_PROB_MAX = 40


class WeatherError(Exception):
    """Raised when live weather cannot be sourced for a location."""


def zone_normal_weather() -> Weather:
    """Deterministic zone-normal defaults for the Guntur tomato belt.

    Warm, moderately humid, dry -- a neutral baseline that lets the prior table
    degrade predictably (it leans slightly to early blight in the warmth) instead
    of inventing precise live readings we don't have. Used ONLY when the live
    fetch fails.
    """
    return Weather(temp_c=28.0, humidity_pct=70.0, rain_48h_mm=0.0, source="zone_normal")


def get_weather(lat: float, lng: float) -> Weather:
    """Return live weather for a location, or raise WeatherError if unavailable.

    Uses Open-Meteo (no API key): current temperature + relative humidity, and
    rain over the last 48h summed from the hourly precipitation series.
    """
    params = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lng,
        "current": "temperature_2m,relative_humidity_2m",
        "hourly": "precipitation",
        "past_days": 2,
        "forecast_days": 1,
        "timezone": "auto",
    })
    request = urllib.request.Request(
        f"{OPEN_METEO_URL}?{params}", headers={"User-Agent": "KisanMate/1.0 (agriculture assistant)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=6) as resp:
            data = json.load(resp)
    except Exception as exc:
        raise WeatherError(f"live weather fetch failed: {exc}") from exc

    current = data.get("current") or {}
    temp = current.get("temperature_2m")
    humidity = current.get("relative_humidity_2m")
    if temp is None or humidity is None:
        raise WeatherError("live weather response missing temperature/humidity")

    hourly_precip = (data.get("hourly") or {}).get("precipitation") or []
    rain_48h = sum(float(p) for p in hourly_precip[-48:] if p is not None)

    return Weather(
        temp_c=float(temp),
        humidity_pct=float(humidity),
        rain_48h_mm=round(rain_48h, 1),
        source="live",
    )


def get_forecast(lat: float, lng: float) -> Forecast:
    """Return the 7-day rain outlook for a location, or raise WeatherError.

    Feeds only the irrigation dry-spell reminder (engine.reminders); the
    diagnosis prior keeps using get_weather() above, unchanged.
    """
    params = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lng,
        "daily": "precipitation_sum,precipitation_probability_max",
        "forecast_days": 7,
        "timezone": "auto",
    })
    request = urllib.request.Request(
        f"{OPEN_METEO_URL}?{params}", headers={"User-Agent": "KisanMate/1.0 (agriculture assistant)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=6) as resp:
            data = json.load(resp)
    except Exception as exc:
        raise WeatherError(f"live forecast fetch failed: {exc}") from exc

    daily = data.get("daily") or {}
    precip_days = daily.get("precipitation_sum") or []
    prob_days = daily.get("precipitation_probability_max") or []
    if not precip_days:
        raise WeatherError("live forecast response missing precipitation_sum")

    precip_next7 = sum(float(p) for p in precip_days if p is not None)
    rain_prob_max = max((float(p) for p in prob_days if p is not None), default=0.0)

    return Forecast(
        precip_next7_mm=round(precip_next7, 1),
        rain_prob_max_pct=rain_prob_max,
        dry_spell=precip_next7 < DRY_SPELL_MM and rain_prob_max < DRY_SPELL_RAIN_PROB_MAX,
        source="live",
    )
