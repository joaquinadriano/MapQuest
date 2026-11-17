"""Public transport routes from the Google Maps Routes API (travel mode TRANSIT).

GraphHopper's hosted plan only routes cars, bikes and walking, so transit uses
Google, which covers Metro Manila's rail lines (LRT-1, LRT-2, MRT-3, PNR) and
many bus routes. The key stays on the server; the browser never sees it.
"""

import os
from datetime import datetime, timezone
from threading import Lock
from time import monotonic

import requests
from dotenv import load_dotenv

from services.graphhopper_service import ServiceError

load_dotenv()

GOOGLE_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
TIMEOUT = (4, 16)
MAX_ROUTES = 3
# Google bills per request, so identical searches within 5 minutes reuse the answer.
CACHE_SECONDS = 300
FIELD_MASK = ",".join([
    "routes.distanceMeters",
    "routes.duration",
    "routes.polyline.encodedPolyline",
    "routes.travelAdvisory.transitFare",
    "routes.legs.steps.travelMode",
    "routes.legs.steps.distanceMeters",
    "routes.legs.steps.staticDuration",
    "routes.legs.steps.polyline.encodedPolyline",
    "routes.legs.steps.navigationInstruction.instructions",
    "routes.legs.steps.transitDetails",
])
VEHICLES = {
    "SUBWAY": "train", "METRO_RAIL": "train", "HEAVY_RAIL": "train", "RAIL": "train", "COMMUTER_TRAIN": "train",
    "HIGH_SPEED_TRAIN": "train", "LONG_DISTANCE_TRAIN": "train", "TRAM": "train", "MONORAIL": "train",
    "BUS": "bus", "INTERCITY_BUS": "bus", "TROLLEYBUS": "bus", "SHARE_TAXI": "jeepney",
    "FERRY": "ferry",
}
session = requests.Session()
_cache: dict[tuple, tuple[float, list]] = {}
_cache_lock = Lock()


def require_google_key() -> None:
    if not GOOGLE_KEY:
        raise ServiceError(
            "Transit is not configured. Add GOOGLE_MAPS_API_KEY to your .env file and restart Maply.",
            503,
        )


def decode_polyline(encoded: str) -> list[list[float]]:
    """Decode a Google encoded polyline into [lat, lng] pairs."""
    points, index, lat, lng = [], 0, 0, 0
    while index < len(encoded):
        for axis in range(2):
            result, shift = 0, 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lng += delta
        points.append([round(lat / 1e5, 5), round(lng / 1e5, 5)])
    return points


def _seconds(value) -> int:
    """Google durations look like "754s"."""
    try:
        return int(float(str(value or "0s").rstrip("s")))
    except ValueError:
        return 0


def _epoch_ms(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _fare(route: dict) -> dict | None:
    fare = (route.get("travelAdvisory") or {}).get("transitFare")
    if not fare:
        return None
    amount = int(fare.get("units", 0) or 0) + int(fare.get("nanos", 0) or 0) / 1e9
    return {"amount": round(amount, 2), "currency": fare.get("currencyCode", "PHP")}


def _parse_step(step: dict) -> dict:
    geometry = decode_polyline((step.get("polyline") or {}).get("encodedPolyline", ""))
    base = {"distance_m": step.get("distanceMeters", 0), "duration_ms": _seconds(step.get("staticDuration")) * 1000, "geometry": geometry}
    details = step.get("transitDetails")
    if step.get("travelMode") != "TRANSIT" or not details:
        text = ((step.get("navigationInstruction") or {}).get("instructions") or "Walk").strip()
        return {"type": "walk", "text": text, **base}
    line = details.get("transitLine") or {}
    vehicle = line.get("vehicle") or {}
    stops = details.get("stopDetails") or {}
    return {
        "type": "transit",
        "vehicle": VEHICLES.get(vehicle.get("type", ""), "bus"),
        "vehicle_name": ((vehicle.get("name") or {}).get("text") or "").strip(),
        "line": line.get("nameShort") or line.get("name") or "Transit",
        "line_name": line.get("name") or "",
        "color": line.get("color") or "",
        "text_color": line.get("textColor") or "",
        "headsign": details.get("headsign") or "",
        "from_stop": (stops.get("departureStop") or {}).get("name", ""),
        "to_stop": (stops.get("arrivalStop") or {}).get("name", ""),
        "depart_time": _epoch_ms(stops.get("departureTime")),
        "arrive_time": _epoch_ms(stops.get("arrivalTime")),
        "stops": details.get("stopCount") or 0,
        **base,
    }


def _merge_walks(legs: list[dict]) -> list[dict]:
    """Google splits a walk into many short steps; show each walk as one leg."""
    merged: list[dict] = []
    for leg in legs:
        if leg["type"] == "walk" and merged and merged[-1]["type"] == "walk":
            previous = merged[-1]
            previous["distance_m"] += leg["distance_m"]
            previous["duration_ms"] += leg["duration_ms"]
            previous["geometry"] += leg["geometry"]
        else:
            merged.append(dict(leg))
    for leg in merged:
        if leg["type"] == "walk":
            leg["text"] = "Walk"
    return merged


def _parse_route(route: dict) -> dict | None:
    steps = [step for leg in route.get("legs", []) for step in leg.get("steps", [])]
    legs = _merge_walks([_parse_step(step) for step in steps])
    rides = [leg for leg in legs if leg["type"] == "transit"]
    if not rides:
        return None  # A walking-only answer is not a transit route.
    geometry = decode_polyline((route.get("polyline") or {}).get("encodedPolyline", "")) or [point for leg in legs for point in leg["geometry"]]
    if not geometry:
        return None
    return {
        "mode": "transit",
        "distance_m": route.get("distanceMeters", 0),
        "duration_ms": _seconds(route.get("duration")) * 1000,
        "geometry": geometry,
        "legs": legs,
        "instructions": [],
        "fare": _fare(route),
        "transfers": max(0, len(rides) - 1),
        "walk_m": sum(leg["distance_m"] for leg in legs if leg["type"] == "walk"),
        "depart_time": rides[0]["depart_time"],
        "arrive_time": rides[-1]["arrive_time"],
        "label": " + ".join(dict.fromkeys(ride["line"] for ride in rides)),
    }


def transit_routes(origin: dict, destination: dict, depart_at_ms: int | None = None) -> list[dict]:
    """Up to three public transport routes, fastest first."""
    require_google_key()
    # Round the departure to 5 minutes so repeated searches share the cache.
    depart_key = None if depart_at_ms is None else depart_at_ms // 300_000
    key = (round(origin["lat"], 4), round(origin["lng"], 4), round(destination["lat"], 4), round(destination["lng"], 4), depart_key)
    now = monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]

    body = {
        "origin": {"location": {"latLng": {"latitude": origin["lat"], "longitude": origin["lng"]}}},
        "destination": {"location": {"latLng": {"latitude": destination["lat"], "longitude": destination["lng"]}}},
        "travelMode": "TRANSIT",
        "computeAlternativeRoutes": True,
        "languageCode": "en",
        "units": "METRIC",
    }
    if depart_at_ms is not None:
        body["departureTime"] = datetime.fromtimestamp(depart_at_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        response = session.post(ROUTES_URL, json=body, headers={"X-Goog-Api-Key": GOOGLE_KEY, "X-Goog-FieldMask": FIELD_MASK}, timeout=TIMEOUT)
    except requests.Timeout as exc:
        raise ServiceError("The transit request timed out. Please try again.", 504) from exc
    except requests.RequestException as exc:
        raise ServiceError("Could not reach Google Maps. Check your connection and try again.", 502) from exc

    if response.status_code in (401, 403):
        raise ServiceError("Google rejected the transit request. Check GOOGLE_MAPS_API_KEY and that the Routes API is enabled with billing.", 503)
    if response.status_code == 429:
        raise ServiceError("Transit request limit reached. Please try again later.", 429)
    if response.status_code == 400:
        raise ServiceError("Transit times are only available from now on. Choose a later departure.", 400)
    if not response.ok:
        raise ServiceError("Transit routes are not available right now.", 502)

    routes = [parsed for parsed in (_parse_route(route) for route in (response.json().get("routes") or [])) if parsed]
    if not routes:
        raise ServiceError("No public transport route was found for this trip at that time.", 404)
    routes.sort(key=lambda route: route["duration_ms"])
    routes = routes[:MAX_ROUTES]
    with _cache_lock:
        _cache[key] = (now, routes)
    return routes


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
