"""JSON endpoints for location search and route planning."""

from collections import defaultdict, deque
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
    origin, destination = clean_place(body.get("origin"), "Origin"), clean_place(body.get("destination"), "Destination")
    route = route_for(origin, destination, str(body.get("mode", "car")))
    return jsonify({"route": route, "origin": origin, "destination": destination})


@api.post("/api/compare")
def compare_endpoint():
    body = request.get_json(silent=True) or {}
    origin, destination = clean_place(body.get("origin"), "Origin"), clean_place(body.get("destination"), "Destination")
    results = []
    for mode in PROFILES:
        try:
            result = route_for(origin, destination, mode)
            results.append({"mode": mode, "available": True, "distance_m": result["distance_m"], "duration_ms": result["duration_ms"]})
        except ServiceError as exc:
            results.append({"mode": mode, "available": False, "error": str(exc)})
    return jsonify({"comparison": results})


@api.errorhandler(ServiceError)
def handle_service_error(error):
    return jsonify({"error": str(error)}), error.status
