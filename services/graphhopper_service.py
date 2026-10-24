"""GraphHopper geocoding and routing integration."""

import os

import requests
from dotenv import load_dotenv

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
        raise ServiceError("GraphHopper is not configured. Add GRAPHHOPPER_API_KEY to your .env file and restart MapQuest.", 503)


def api_error(response: requests.Response, fallback: str) -> ServiceError:
    try:
        payload = response.json()
        hints = payload.get("hints") or []
        message = payload.get("message") or (hints[0].get("message") if hints else None)
    except (ValueError, AttributeError, IndexError):
        message = None
    if response.status_code == 401:
        message = "GraphHopper rejected the API key. Check the key in .env."
    elif response.status_code == 429:
        message = "GraphHopper request limit reached. Please try again later."
    return ServiceError(str(message or fallback), 429 if response.status_code == 429 else 502)


def geocode(query: str, limit: int = 5) -> list[dict]:
    require_api_key()
    try:
        response = session.get(f"{GRAPHHOPPER_URL}/geocode", params={"q": query, "limit": limit, "key": GRAPHHOPPER_KEY}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise ServiceError("Could not reach GraphHopper. Check your connection and try again.") from exc
    if not response.ok:
        raise api_error(response, "Location search failed.")
    hits = response.json().get("hits", [])
    return [{"name": hit.get("name", query), "label": ", ".join(filter(None, [hit.get("name"), hit.get("city"), hit.get("state"), hit.get("country")])),
             "lat": hit["point"]["lat"], "lng": hit["point"]["lng"], "country": hit.get("country", "")}
            for hit in hits if hit.get("point")]


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


def route_for(origin: dict, destination: dict, mode: str) -> dict:
    require_api_key()
    if mode not in PROFILES:
        raise ServiceError("Choose driving, cycling, or walking.", 400)
    params = [("profile", PROFILES[mode]), ("key", GRAPHHOPPER_KEY), ("instructions", "true"),
              ("points_encoded", "false"), ("calc_points", "true"), ("locale", "en")]
    params.extend([("point", f'{origin["lat"]},{origin["lng"]}'), ("point", f'{destination["lat"]},{destination["lng"]}')])
    try:
        response = session.get(f"{GRAPHHOPPER_URL}/route", params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise ServiceError("Could not reach GraphHopper. Check your connection and try again.") from exc
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
    instructions = [{"text": step.get("text", "Continue"), "distance": step.get("distance", 0),
                     "time": step.get("time", 0), "sign": step.get("sign", 0)} for step in path.get("instructions", [])]
    return {"mode": mode, "distance_m": path.get("distance", 0), "duration_ms": path.get("time", 0),
            "geometry": [[point[1], point[0]] for point in points], "instructions": instructions,
            "points": path.get("points", {}).get("type", "LineString")}
