"""JSON endpoints for location search and route planning."""

from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import monotonic, time

from flask import Blueprint, jsonify, request

from services.commute_service import commute_routes
from services.graphhopper_service import (
    PROFILES,
    ServiceError,
    clean_place,
    geocode,
    require_metro_manila,
    route_alternatives,
    route_for,
)
from services.mapquest_service import route_for as mapquest_route_for
from services.weather_service import trip_weather, weather_for
from services.webex_service import send_summary as send_webex_summary

api = Blueprint("api", __name__)
_request_buckets: dict[str, deque] = defaultdict(deque)
_rate_limit_lock = Lock()
_RATE_WINDOW_SECONDS = 60
_GRAPHOPPER_CREDIT_BUDGET = 20


@api.before_request
def limit_graphhopper_requests():
    """Limit accidental bursts of billable geocode and route requests per client."""
    # GraphHopper charges about 2 credits for an alternative-routes request. A commute search
    # counts as 2: most are answered from the day-long cache (for example when only the
    # departure time changes), and COMMUTE_DAILY_LIMIT caps the paid OpenAI searches.
    costs = {"api.geocode_endpoint": 1, "api.route_endpoint": 1, "api.compare_endpoint": 3, "api.alternatives_endpoint": 2, "api.commute_endpoint": 2, "api.mapquest_endpoint": 1}
    cost = costs.get(request.endpoint)
    if cost is None:
        return None
    now = monotonic()
    client = request.remote_addr or "unknown"
    with _rate_limit_lock:
        # Prune inactive buckets to prevent memory leaks over time
        stale_clients = [
            key
            for key, timestamps in _request_buckets.items()
            if not timestamps or now - timestamps[-1][0] >= _RATE_WINDOW_SECONDS
        ]
        for key in stale_clients:
            del _request_buckets[key]

        bucket = _request_buckets[client]
        while bucket and now - bucket[0][0] >= _RATE_WINDOW_SECONDS:
            bucket.popleft()
        used = sum(weight for _timestamp, weight in bucket)
        if used + cost > _GRAPHOPPER_CREDIT_BUDGET:
            raise ServiceError("Maply is limiting route searches for a moment. Please wait a minute and try again.", 429)
        bucket.append((now, cost))
    return None


@api.get("/api/geocode")
def geocode_endpoint():
    query = request.args.get("q", "").strip()
    if not query or len(query) > 160:
        raise ServiceError("Enter a location up to 160 characters.", 400)
    return jsonify({"suggestions": geocode(query)})


@api.post("/api/route")
def route_endpoint():
    body = request.get_json(silent=True) or {}
    origin = require_metro_manila(clean_place(body.get("origin"), "Origin"), "Starting point")
    destination = require_metro_manila(clean_place(body.get("destination"), "Destination"), "Destination")

    if abs(origin["lat"] - destination["lat"]) < 1e-6 and abs(origin["lng"] - destination["lng"]) < 1e-6:
        raise ServiceError("Origin and destination cannot be the same place.", 400)

    kwargs = {}
    if "fuel_efficiency" in body:
        try:
            kwargs["fuel_efficiency"] = float(body["fuel_efficiency"])
        except (TypeError, ValueError):
            pass
    if "fuel_price" in body:
        try:
            kwargs["fuel_price"] = float(body["fuel_price"])
        except (TypeError, ValueError):
            pass

    route = route_for(
        origin,
        destination,
        str(body.get("mode", "car")),
        **kwargs,
    )
    return jsonify({"route": route, "origin": origin, "destination": destination})


@api.post("/api/compare")
def compare_endpoint():
    body = request.get_json(silent=True) or {}
    origin = require_metro_manila(clean_place(body.get("origin"), "Origin"), "Starting point")
    destination = require_metro_manila(clean_place(body.get("destination"), "Destination"), "Destination")

    if abs(origin["lat"] - destination["lat"]) < 1e-6 and abs(origin["lng"] - destination["lng"]) < 1e-6:
        raise ServiceError("Origin and destination cannot be the same place.", 400)

    modes = list(PROFILES.keys())
    results_map: dict[str, dict] = {}

    def fetch_profile_route(target_mode: str) -> tuple[str, dict]:
        try:
            res = route_for(origin, destination, target_mode)
            return target_mode, {
                "mode": target_mode,
                "available": True,
                "distance_m": res["distance_m"],
                "duration_ms": res["duration_ms"],
                "summary": res.get("summary"),
                # Full route lets the browser switch modes without another billable request.
                "route": res,
            }
        except ServiceError as exc:
            return target_mode, {
                "mode": target_mode,
                "available": False,
                "error": str(exc),
            }

    # Fetch comparison profiles concurrently to minimize latency
    with ThreadPoolExecutor(max_workers=len(modes)) as executor:
        futures = [executor.submit(fetch_profile_route, mode) for mode in modes]
        for future in futures:
            mode_name, data = future.result()
            results_map[mode_name] = data

    results = [results_map[mode] for mode in modes]
    return jsonify({"comparison": results, "origin": origin, "destination": destination})


@api.post("/api/alternatives")
def alternatives_endpoint():
    """Up to three different routes for one travel mode, fastest first."""
    body = request.get_json(silent=True) or {}
    origin = require_metro_manila(clean_place(body.get("origin"), "Origin"), "Starting point")
    destination = require_metro_manila(clean_place(body.get("destination"), "Destination"), "Destination")

    if abs(origin["lat"] - destination["lat"]) < 1e-6 and abs(origin["lng"] - destination["lng"]) < 1e-6:
        raise ServiceError("Origin and destination cannot be the same place.", 400)

    mode = str(body.get("mode", "car"))
    return jsonify({"mode": mode, "routes": route_alternatives(origin, destination, mode)})


@api.post("/api/commute")
def commute_endpoint():
    """Commute options by jeepney, bus, UV Express and LRT/MRT, found on the web by OpenAI."""
    body = request.get_json(silent=True) or {}
    origin = require_metro_manila(clean_place(body.get("origin"), "Origin"), "Starting point")
    destination = require_metro_manila(clean_place(body.get("destination"), "Destination"), "Destination")

    if abs(origin["lat"] - destination["lat"]) < 1e-6 and abs(origin["lng"] - destination["lng"]) < 1e-6:
        raise ServiceError("Origin and destination cannot be the same place.", 400)

    depart_at = _departure(body.get("depart_at"), int(time() * 1000))
    return jsonify({"routes": commute_routes(origin, destination, depart_at), "origin": origin, "destination": destination})


def _departure(value, now_ms: int) -> int | None:
    """A validated future departure time in ms, or None for "now"."""
    if value is None:
        return None
    try:
        depart_at = int(value)
    except (TypeError, ValueError):
        raise ServiceError("Choose a valid departure time.", 400)
    if depart_at < now_ms - 5 * 60_000 or depart_at > now_ms + 30 * 24 * 3_600_000:
        raise ServiceError("Choose a departure time between now and 30 days from now.", 400)
    return depart_at if depart_at > now_ms else None  # Allow for small clock differences.


@api.post("/api/mapquest")
def mapquest_endpoint():
    """MapQuest's directions for the trip, plus an automatic trip summary posted to Webex."""
    body = request.get_json(silent=True) or {}
    origin = require_metro_manila(clean_place(body.get("origin"), "Origin"), "Starting point")
    destination = require_metro_manila(clean_place(body.get("destination"), "Destination"), "Destination")

    if abs(origin["lat"] - destination["lat"]) < 1e-6 and abs(origin["lng"] - destination["lng"]) < 1e-6:
        raise ServiceError("Origin and destination cannot be the same place.", 400)

    unit = "imperial" if body.get("unit") in ("imperial", "english") else "metric"
    now_ms = int(time() * 1000)
    depart_ms = _departure(body.get("depart_at"), now_ms) or now_ms
    trip = mapquest_route_for(origin, destination, str(body.get("mode", "car")))
    # Weather is optional: without an OpenWeatherMap key (or if it is down) the summary is sent without it.
    weather = trip_weather(origin, destination, depart_ms, trip.seconds, trip.mode, now_ms)
    return jsonify({
        "trip": trip.to_dict(),
        "unit": unit,
        "weather": weather,
        "webex": send_webex_summary(trip, unit, weather),
    })


@api.get("/api/weather")
def weather_endpoint():
    """Weather now and for the next five days at a place in Metro Manila (OpenWeatherMap)."""
    place = require_metro_manila(
        clean_place({"lat": request.args.get("lat"), "lng": request.args.get("lng")}, "Location"),
        "Location",
    )
    return jsonify(weather_for(place["lat"], place["lng"]))


@api.errorhandler(ServiceError)
def handle_service_error(error):
    return jsonify({"error": str(error)}), error.status

