"""GraphHopper geocoding and routing integration."""

import os

import requests
from dotenv import load_dotenv

from services.converters import (
    build_route_summary,
    format_step_distance,
    meters_to_km,
    meters_to_miles,
)

load_dotenv()

GRAPHHOPPER_KEY = os.getenv("GRAPHHOPPER_API_KEY", "").strip()
GRAPHHOPPER_URL = "https://graphhopper.com/api/1"
TIMEOUT = (4, 16)
PROFILES = {"car": "car", "bike": "bike", "foot": "foot"}
session = requests.Session()


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def require_api_key() -> None:
    if not GRAPHHOPPER_KEY:
        raise ServiceError(
            "GraphHopper is not configured. Add GRAPHHOPPER_API_KEY to your .env file and restart MapQuest.",
            503,
        )


def api_error(response: requests.Response, fallback: str) -> ServiceError:
    try:
        payload = response.json()
        hints = payload.get("hints") or []
        message = payload.get("message") or (hints[0].get("message") if hints else None)
    except (ValueError, AttributeError, IndexError):
        message = None

    status_code = response.status_code
    if status_code == 401:
        message = "GraphHopper rejected the API key. Check the key in .env."
        return ServiceError(str(message), 503)
    if status_code == 429:
        message = "GraphHopper request limit reached. Please try again later."
        return ServiceError(str(message), 429)
    if status_code == 400:
        return ServiceError(str(message or fallback), 400)
    if status_code == 404:
        return ServiceError(str(message or fallback), 404)

    return ServiceError(str(message or fallback), 502)


def geocode(query: str, limit: int = 5) -> list[dict]:
    require_api_key()
    try:
        response = session.get(
            f"{GRAPHHOPPER_URL}/geocode",
            params={"q": query, "limit": limit, "key": GRAPHHOPPER_KEY},
            timeout=TIMEOUT,
        )
    except requests.Timeout as exc:
        raise ServiceError("GraphHopper geocoding request timed out. Please try again.", 504) from exc
    except requests.RequestException as exc:
        raise ServiceError("Could not reach GraphHopper. Check your connection and try again.", 502) from exc

    if not response.ok:
        raise api_error(response, "Location search failed.")

    hits = response.json().get("hits", [])
    return [
        {
            "name": hit.get("name", query),
            "label": ", ".join(
                filter(None, [hit.get("name"), hit.get("city"), hit.get("state"), hit.get("country")])
            ),
            "lat": hit["point"]["lat"],
            "lng": hit["point"]["lng"],
            "country": hit.get("country", ""),
        }
        for hit in hits
        if hit.get("point")
    ]


def clean_place(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ServiceError(f"Select a valid {label.lower()} from the suggestions.", 400)
    try:
        lat, lng = float(value["lat"]), float(value["lng"])
    except (KeyError, TypeError, ValueError):
        raise ServiceError(f"Select a valid {label.lower()} from the suggestions.", 400)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ServiceError(f"The {label.lower()} coordinates are invalid.", 400)
    name = str(value.get("label") or value.get("name") or "").strip()[:200]
    return {"name": name, "lat": lat, "lng": lng}


def route_for(
    origin: dict,
    destination: dict,
    mode: str,
    fuel_efficiency: float = 12.0,
    fuel_price: float = 65.0,
) -> dict:
    require_api_key()
    if mode not in PROFILES:
        raise ServiceError("Choose driving, cycling, or walking.", 400)

    params = [
        ("profile", PROFILES[mode]),
        ("key", GRAPHHOPPER_KEY),
        ("instructions", "true"),
        ("points_encoded", "false"),
        ("calc_points", "true"),
        ("locale", "en"),
    ]
    params.extend([
        ("point", f'{origin["lat"]},{origin["lng"]}'),
        ("point", f'{destination["lat"]},{destination["lng"]}'),
    ])

    try:
        response = session.get(f"{GRAPHHOPPER_URL}/route", params=params, timeout=TIMEOUT)
    except requests.Timeout as exc:
        raise ServiceError(f"GraphHopper route request for {mode} timed out. Please try again.", 504) from exc
    except requests.RequestException as exc:
        raise ServiceError("Could not reach GraphHopper. Check your connection and try again.", 502) from exc

    if not response.ok:
        raise api_error(response, f"GraphHopper could not calculate a {mode} route.")

    payload = response.json()
    paths = payload.get("paths") or []
    if not paths:
        raise ServiceError(f"No {mode} route was found for these locations.")

    path = paths[0]
    points = path.get("points", {}).get("coordinates", [])
    if not points:
        raise ServiceError("GraphHopper returned a route without map geometry.")

    instructions = [
        {
            "text": step.get("text", "Continue"),
            "distance": step.get("distance", 0),
            "distance_km": meters_to_km(step.get("distance", 0), 2),
            "distance_mi": meters_to_miles(step.get("distance", 0), 2),
            "formatted_metric": format_step_distance(step.get("distance", 0), "metric"),
            "formatted_imperial": format_step_distance(step.get("distance", 0), "imperial"),
            "time": step.get("time", 0),
            "sign": step.get("sign", 0),
        }
        for step in path.get("instructions", [])
    ]

    distance_m = path.get("distance", 0)
    duration_ms = path.get("time", 0)
    summary = build_route_summary(
        distance_m,
        duration_ms,
        fuel_efficiency=fuel_efficiency,
        fuel_price=fuel_price,
    )

    return {
        "mode": mode,
        "distance_m": distance_m,
        "duration_ms": duration_ms,
        "summary": summary,
        "geometry": [[point[1], point[0]] for point in points],
        "instructions": instructions,
        "points": path.get("points", {}).get("type", "LineString"),
    }

