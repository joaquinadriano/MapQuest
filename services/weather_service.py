"""OpenWeatherMap current weather and 5-day / 3-hour forecast for trip planning."""

import os
from threading import Lock
from time import monotonic

import requests
from dotenv import load_dotenv

from services.graphhopper_service import ServiceError

load_dotenv()

OPENWEATHER_KEY = os.getenv("OPENWEATHER_API_KEY", "").strip()
OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5"
TIMEOUT = (4, 12)
# Weather changes slowly and Metro Manila is small, so nearby requests share one
# cached answer for 10 minutes. This keeps us far below the free plan's limits.
CACHE_SECONDS = 600
CACHE_GRID_DEGREES = 0.05  # about 5 km
session = requests.Session()
_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = Lock()


def require_weather_key() -> None:
    if not OPENWEATHER_KEY:
        raise ServiceError(
            "Weather is not configured. Add OPENWEATHER_API_KEY to your .env file and restart Maply.",
            503,
        )


def _get(path: str, lat: float, lng: float) -> dict:
    try:
        response = session.get(
            f"{OPENWEATHER_URL}/{path}",
            params={"lat": lat, "lon": lng, "units": "metric", "appid": OPENWEATHER_KEY},
            timeout=TIMEOUT,
        )
    except requests.Timeout as exc:
        raise ServiceError("The weather service timed out. Please try again.", 504) from exc
    except requests.RequestException as exc:
        raise ServiceError("Could not reach the weather service. Check your connection and try again.", 502) from exc

    if response.status_code == 401:
        raise ServiceError("OpenWeatherMap rejected the API key. Check OPENWEATHER_API_KEY in .env (new keys can take up to 2 hours to activate).", 503)
    if response.status_code == 429:
        raise ServiceError("Weather request limit reached. Please try again later.", 429)
    if not response.ok:
        raise ServiceError("Weather is not available right now.", 502)
    try:
        return response.json()
    except ValueError as exc:
        raise ServiceError("The weather service sent an unexpected response.", 502) from exc


def _condition(entry: dict) -> dict:
    weather = (entry.get("weather") or [{}])[0]
    code = int(weather.get("id") or 800)
    icon = str(weather.get("icon") or "01d")
    return {
        "code": code,
        "condition": weather.get("main") or "Clear",
        "description": (weather.get("description") or "").capitalize(),
        "is_day": icon.endswith("d"),
    }


def _reading(entry: dict, rain_key: str) -> dict:
    main = entry.get("main") or {}
    rain = entry.get("rain") or {}
    return {
        "time": int(entry.get("dt", 0)) * 1000,
        "temp_c": round(float(main.get("temp", 0)), 1),
        "feels_like_c": round(float(main.get("feels_like", main.get("temp", 0))), 1),
        "humidity": main.get("humidity"),
        "wind_kph": round(float((entry.get("wind") or {}).get("speed", 0)) * 3.6, 1),
        "rain_mm": round(float(rain.get(rain_key, 0)), 1),
        # Probability of precipitation (0 to 1). Current weather has none; use 1 if it is raining now.
        "pop": round(float(entry.get("pop", 1 if rain else 0)), 2),
        **_condition(entry),
    }


def weather_for(lat: float, lng: float) -> dict:
    """Current conditions plus the 3-hourly forecast for the next five days."""
    require_weather_key()
    key = (round(lat / CACHE_GRID_DEGREES), round(lng / CACHE_GRID_DEGREES))
    now = monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]

    current = _get("weather", lat, lng)
    forecast = _get("forecast", lat, lng)
    result = {
        "current": _reading(current, "1h"),
        "forecast": [_reading(entry, "3h") for entry in forecast.get("list", [])],
        "place": (forecast.get("city") or {}).get("name") or current.get("name") or "",
    }
    with _cache_lock:
        _cache[key] = (now, result)
    return result


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
