"""JSON endpoints for location search and route planning."""

from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import monotonic

from flask import Blueprint, jsonify, request

from services.graphhopper_service import PROFILES, ServiceError, clean_place, geocode, route_for

api = Blueprint("api", __name__)
_request_buckets: dict[str, deque] = defaultdict(deque)
_rate_limit_lock = Lock()
_RATE_WINDOW_SECONDS = 60
_GRAPHOPPER_CREDIT_BUDGET = 20


@api.before_request
def limit_graphhopper_requests():
    """Limit accidental bursts of billable geocode and route requests per client."""
    costs = {"api.geocode_endpoint": 1, "api.route_endpoint": 1, "api.compare_endpoint": 3}
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
            raise ServiceError("MapQuest is limiting route searches for a moment. Please wait a minute and try again.", 429)
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
    origin = clean_place(body.get("origin"), "Origin")
    destination = clean_place(body.get("destination"), "Destination")

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
    origin = clean_place(body.get("origin"), "Origin")
    destination = clean_place(body.get("destination"), "Destination")

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
    return jsonify({"comparison": results})


@api.errorhandler(ServiceError)
def handle_service_error(error):
    return jsonify({"error": str(error)}), error.status

