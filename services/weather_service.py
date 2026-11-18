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


# ---------- Weather for one trip (used by the MapQuest panel and the Webex summary) ----------

HOUR_MS = 3_600_000


def reading_at(weather: dict | None, time_ms: int, now_ms: int) -> dict | None:
    """Current conditions for the next 90 minutes, otherwise the nearest 3-hour forecast slot.

    Same rule as readingAt() in static/js/weather.js. None when the time is past the forecast.
    """
    if not weather:
        return None
    if weather.get("current") and abs(time_ms - now_ms) <= 1.5 * HOUR_MS:
        return weather["current"]
    slots = weather.get("forecast") or []
    if not slots:
        return None
    best = min(slots, key=lambda slot: abs(slot["time"] - time_ms))
    return best if abs(best["time"] - time_ms) <= 3 * HOUR_MS else None


def rain_risk(reading: dict | None) -> str:
    """'heavy', 'likely', 'possible' or 'low' (same bands as rainRisk() in weather.js)."""
    if not reading:
        return "low"
    code = int(reading.get("code") or 800)
    group = code // 100
    if group == 2 or reading.get("rain_mm", 0) >= 7.5 or 502 <= code <= 531:
        return "heavy"
    if reading.get("pop", 0) >= 0.5 or group in (3, 5):
        return "likely"
    if reading.get("pop", 0) >= 0.25:
        return "possible"
    return "low"


def condition_emoji(reading: dict) -> str:
    code = int(reading.get("code") or 800)
    group = code // 100
    if group == 2:
        return "⛈️"
    if group == 3:
        return "🌦️"
    if group == 5:
        return "🌧️"
    if group == 7:
        return "🌫️"
    if code == 800:
        return "☀️" if reading.get("is_day", True) else "🌙"
    return "⛅" if code in (801, 802) else "☁️"


def trip_advice(start: dict | None, end: dict | None, mode: str) -> list[str]:
    """Short weather advice for the trip, most important first (no numbers, so it suits any units)."""
    readings = [reading for reading in (start, end) if reading]
    if not readings:
        return []
    order = {"low": 0, "possible": 1, "likely": 2, "heavy": 3}
    rain = max((rain_risk(reading) for reading in readings), key=order.get)
    feels = max(reading.get("feels_like_c", reading.get("temp_c", 0)) for reading in readings)
    exposed = mode in ("bike", "foot")
    advice = []
    if rain == "heavy":
        advice.append("⛈️ Heavy rain expected. Allow extra time and watch for flooded roads.")
    elif rain == "likely":
        advice.append("🌧️ Rain is likely. " + ("Bring rain gear, or consider driving or commuting." if exposed else "Bring an umbrella."))
    elif rain == "possible":
        advice.append("🌦️ There is a chance of rain. An umbrella is a good idea.")
    if feels >= 42:
        advice.append("🥵 Dangerous heat. Stay hydrated" + (" and avoid the midday sun." if exposed else "."))
    elif feels >= 33:
        advice.append("☀️ Hot weather. Bring water" + (" and take shade breaks." if exposed else "."))
    if not advice:
        advice.append("✅ Good weather for this trip.")
    return advice


def _brief(reading: dict | None) -> dict | None:
    if not reading:
        return None
    return {
        "time": reading["time"],
        "temp_c": reading["temp_c"],
        "feels_like_c": reading["feels_like_c"],
        "description": reading.get("description") or reading.get("condition") or "",
        "rain_chance": round(float(reading.get("pop", 0)) * 100),
        "wind_kph": reading.get("wind_kph", 0),
        "emoji": condition_emoji(reading),
    }


def trip_weather(origin: dict, destination: dict, depart_ms: int, seconds: int, mode: str, now_ms: int) -> dict | None:
    """Weather when leaving the start and when arriving at the destination, plus advice.

    Returns None (never raises) when weather is not configured or not reachable, so a trip
    summary can always be sent without it.
    """
    try:
        at_start = weather_for(origin["lat"], origin["lng"])
        at_end = weather_for(destination["lat"], destination["lng"])
    except ServiceError:
        return None
    arrive_ms = depart_ms + seconds * 1000
    start = reading_at(at_start, depart_ms, now_ms)
    end = reading_at(at_end, arrive_ms, now_ms)
    if not start and not end:
        return None
    return {
        "depart": depart_ms,
        "arrive": arrive_ms,
        "start": _brief(start),
        "end": _brief(end),
        "advice": trip_advice(start, end, mode),
    }
