"""Unit and endpoint tests for MapQuest.

All GraphHopper calls are mocked, so these tests need no API key, no internet
connection, and consume no API credits. Run with:

    python -m unittest discover -s tests -v
"""

import unittest
from unittest.mock import MagicMock, patch

import requests

import app as mapquest
from routes import api as routes_api
from services import graphhopper_service as gh
from services.graphhopper_service import ServiceError


# Helper: instead of calling the real GraphHopper API, the tests hand the app
# this fake response. That way we control exactly what "GraphHopper" says back
# (success, error 401, error 429, etc.) without using real API credits.
def fake_response(status_code=200, payload=None):
    """Build a stand-in for requests.Response with the given status and JSON body."""
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 400
    response.json.return_value = payload if payload is not None else {}
    return response


# Helper: the app only allows 20 searches per minute. Without clearing the
# counter, tests that run one after another would hit the limit and fail.
def reset_rate_limiter():
    """Clear per-client request history so one test's calls never throttle another's."""
    with routes_api._rate_limit_lock:
        routes_api._request_buckets.clear()


# ---------------------------------------------------------------------------
# Endpoint tests: send requests to the Flask app like a browser would and
# check the status code (200 = OK, 400 = bad input, 503 = not configured...).
# ---------------------------------------------------------------------------
class MapQuestEndpointTests(unittest.TestCase):
    # setUp runs before every single test so each one starts fresh.
    def setUp(self):
        reset_rate_limiter()
        self.client = mapquest.app.test_client()
        self.origin = {"name": "Manila", "lat": 14.5995, "lng": 120.9842}
        self.destination = {"name": "Makati", "lat": 14.5547, "lng": 121.0244}

    def test_home_page_loads_the_planner(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"MapQuest", response.data)

    @patch.object(routes_api, "geocode", return_value=[{"name": "Manila", "label": "Manila, Philippines", "lat": 14.5995, "lng": 120.9842, "country": "Philippines"}])
    def test_geocode_returns_suggestions(self, _geocode):
        response = self.client.get("/api/geocode?q=Manila")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["suggestions"][0]["name"], "Manila")

    def test_geocode_rejects_empty_query(self):
        response = self.client.get("/api/geocode?q=")
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json)

    def test_geocode_rejects_whitespace_only_query(self):
        response = self.client.get("/api/geocode?q=%20%20%20")
        self.assertEqual(response.status_code, 400)

    # Boundary testing: 161 characters should fail, 160 should pass.
    def test_geocode_rejects_overly_long_query(self):
        response = self.client.get("/api/geocode?q=" + "a" * 161)
        self.assertEqual(response.status_code, 400)
        self.assertIn("160 characters", response.json["error"])

    @patch.object(routes_api, "geocode", return_value=[])
    def test_geocode_accepts_query_at_length_limit(self, geocode):
        response = self.client.get("/api/geocode?q=" + "a" * 160)
        self.assertEqual(response.status_code, 200)
        geocode.assert_called_once_with("a" * 160)

    @patch.object(routes_api, "route_for", return_value={"mode": "car", "distance_m": 5200, "duration_ms": 780000, "geometry": [[14.59, 120.98], [14.55, 121.02]], "instructions": []})
    def test_route_uses_selected_mode_and_returns_route(self, route_for):
        response = self.client.post("/api/route", json={"origin": self.origin, "destination": self.destination, "mode": "car"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["route"]["distance_m"], 5200)
        route_for.assert_called_once_with(self.origin, self.destination, "car")

    @patch.object(routes_api, "route_for", return_value={"mode": "car", "distance_m": 1, "duration_ms": 1, "geometry": [], "instructions": []})
    def test_route_defaults_to_car_when_mode_missing(self, route_for):
        response = self.client.post("/api/route", json={"origin": self.origin, "destination": self.destination})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(route_for.call_args.args[2], "car")

    def test_route_rejects_invalid_coordinates(self):
        response = self.client.post("/api/route", json={"origin": {"lat": 110, "lng": 1}, "destination": self.destination, "mode": "car"})
        self.assertEqual(response.status_code, 400)

    def test_route_rejects_missing_body(self):
        response = self.client.post("/api/route", data="not json", content_type="text/plain")
        self.assertEqual(response.status_code, 400)
        self.assertIn("origin", response.json["error"])

    def test_route_rejects_missing_destination(self):
        response = self.client.post("/api/route", json={"origin": self.origin})
        self.assertEqual(response.status_code, 400)
        self.assertIn("destination", response.json["error"])

    @patch.object(gh, "GRAPHHOPPER_KEY", "test-key")
    def test_route_rejects_unknown_mode(self):
        response = self.client.post("/api/route", json={"origin": self.origin, "destination": self.destination, "mode": "plane"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("driving, cycling, or walking", response.json["error"])

    @patch.object(routes_api, "route_for")
    def test_compare_reports_available_and_unavailable_profiles(self, route_for):
        route_for.side_effect = [
            {"distance_m": 1000, "duration_ms": 60000},
            ServiceError("Bike profile unavailable"),
            {"distance_m": 1200, "duration_ms": 900000},
        ]
        response = self.client.post("/api/compare", json={"origin": self.origin, "destination": self.destination})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["available"] for item in response.json["comparison"]], [True, False, True])
        self.assertEqual(response.json["comparison"][1]["error"], "Bike profile unavailable")

    @patch.object(routes_api, "route_for", return_value={"distance_m": 1, "duration_ms": 1})
    def test_compare_covers_every_profile(self, _route_for):
        response = self.client.post("/api/compare", json={"origin": self.origin, "destination": self.destination})
        self.assertEqual([item["mode"] for item in response.json["comparison"]], ["car", "bike", "foot"])

    # The app refuses requests bigger than 32 KB so nobody can flood the server.
    def test_oversized_request_is_rejected(self):
        response = self.client.post("/api/route", data="x" * (33 * 1024), content_type="application/json")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json["error"], "Request is too large.")

    def test_unknown_page_returns_404(self):
        self.assertEqual(self.client.get("/does-not-exist").status_code, 404)


class MissingApiKeyTests(unittest.TestCase):
    """Objective: handle a missing API key gracefully instead of crashing."""

    def setUp(self):
        reset_rate_limiter()
        self.client = mapquest.app.test_client()

    @patch.object(gh, "GRAPHHOPPER_KEY", "")
    def test_geocode_without_key_returns_503_with_setup_hint(self):
        response = self.client.get("/api/geocode?q=Manila")
        self.assertEqual(response.status_code, 503)
        self.assertIn("GRAPHHOPPER_API_KEY", response.json["error"])

    @patch.object(gh, "GRAPHHOPPER_KEY", "")
    def test_route_without_key_returns_503(self):
        place = {"lat": 14.6, "lng": 121.0}
        response = self.client.post("/api/route", json={"origin": place, "destination": place})
        self.assertEqual(response.status_code, 503)

    @patch.object(gh, "GRAPHHOPPER_KEY", "")
    @patch.object(gh, "session")
    def test_no_network_call_is_made_without_key(self, session):
        with self.assertRaises(ServiceError):
            gh.geocode("Manila")
        session.get.assert_not_called()


# Latitude must be -90 to 90 and longitude -180 to 180. These tests try
# values just inside and just outside those limits.
class CleanPlaceTests(unittest.TestCase):
    """Objective: reject invalid locations before calling the API."""

    def test_accepts_valid_place_and_prefers_label(self):
        place = gh.clean_place({"name": "Manila", "label": "Manila, Philippines", "lat": "14.5", "lng": "121"}, "Origin")
        self.assertEqual(place, {"name": "Manila, Philippines", "lat": 14.5, "lng": 121.0})

    def test_accepts_boundary_coordinates(self):
        place = gh.clean_place({"lat": -90, "lng": 180}, "Origin")
        self.assertEqual((place["lat"], place["lng"]), (-90.0, 180.0))

    def test_truncates_long_names(self):
        place = gh.clean_place({"name": "x" * 500, "lat": 0, "lng": 0}, "Origin")
        self.assertEqual(len(place["name"]), 200)

    def test_rejects_non_dict(self):
        for value in (None, "Manila", [14.5, 121.0], 42):
            with self.subTest(value=value), self.assertRaises(ServiceError) as ctx:
                gh.clean_place(value, "Origin")
            self.assertEqual(ctx.exception.status, 400)

    def test_rejects_missing_or_non_numeric_coordinates(self):
        for value in ({"lat": 1}, {"lng": 1}, {"lat": "abc", "lng": 1}, {"lat": None, "lng": 1}):
            with self.subTest(value=value), self.assertRaises(ServiceError):
                gh.clean_place(value, "Destination")

    def test_rejects_out_of_range_coordinates(self):
        for lat, lng in ((90.1, 0), (-90.1, 0), (0, 180.1), (0, -180.1)):
            with self.subTest(lat=lat, lng=lng), self.assertRaises(ServiceError) as ctx:
                gh.clean_place({"lat": lat, "lng": lng}, "Origin")
            self.assertIn("coordinates are invalid", str(ctx.exception))


# Location search tests. @patch gives the app a pretend API key so the code
# runs normally, and the fake session stands in for the internet.
@patch.object(gh, "GRAPHHOPPER_KEY", "test-key")
class GeocodeServiceTests(unittest.TestCase):
    @patch.object(gh, "session")
    def test_builds_readable_label_and_skips_hits_without_point(self, session):
        session.get.return_value = fake_response(payload={"hits": [
            {"name": "Manila", "city": "Manila", "country": "Philippines", "point": {"lat": 14.6, "lng": 121.0}},
            {"name": "No point"},
        ]})
        results = gh.geocode("Manila")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["label"], "Manila, Manila, Philippines")
        self.assertEqual((results[0]["lat"], results[0]["lng"]), (14.6, 121.0))

    @patch.object(gh, "session")
    def test_unknown_location_returns_no_suggestions(self, session):
        session.get.return_value = fake_response(payload={"hits": []})
        self.assertEqual(gh.geocode("qwertyuiopasdf"), [])

    # side_effect makes the fake call raise an error, as if Wi-Fi were off.
    @patch.object(gh, "session")
    def test_no_internet_connection_gives_friendly_error(self, session):
        session.get.side_effect = requests.ConnectionError("offline")
        with self.assertRaises(ServiceError) as ctx:
            gh.geocode("Manila")
        self.assertIn("Check your connection", str(ctx.exception))
        self.assertEqual(ctx.exception.status, 502)

    @patch.object(gh, "session")
    def test_timeout_gives_friendly_error(self, session):
        session.get.side_effect = requests.Timeout()
        with self.assertRaises(ServiceError):
            gh.geocode("Manila")


# Route tests: check that distance, time, map line and turn-by-turn steps
# are read correctly, and that every failure gives a clear message.
@patch.object(gh, "GRAPHHOPPER_KEY", "test-key")
class RouteServiceTests(unittest.TestCase):
    origin = {"lat": 14.5995, "lng": 120.9842}
    destination = {"lat": 14.5547, "lng": 121.0244}

    @patch.object(gh, "session")
    def test_parses_distance_time_geometry_and_instructions(self, session):
        session.get.return_value = fake_response(payload={"paths": [{
            "distance": 5234.5, "time": 780000,
            "points": {"type": "LineString", "coordinates": [[120.98, 14.59], [121.02, 14.55]]},
            "instructions": [{"text": "Turn left", "distance": 100, "time": 6000, "sign": -2}, {}],
        }]})
        route = gh.route_for(self.origin, self.destination, "car")
        self.assertEqual(route["distance_m"], 5234.5)
        self.assertEqual(route["duration_ms"], 780000)
        # GraphHopper returns [lng, lat]; the map expects [lat, lng].
        self.assertEqual(route["geometry"], [[14.59, 120.98], [14.55, 121.02]])
        self.assertEqual(route["instructions"][0], {"text": "Turn left", "distance": 100, "time": 6000, "sign": -2})
        self.assertEqual(route["instructions"][1]["text"], "Continue")

    @patch.object(gh, "session")
    def test_sends_profile_and_both_points(self, session):
        session.get.return_value = fake_response(payload={"paths": [{"points": {"coordinates": [[0, 0]]}}]})
        gh.route_for(self.origin, self.destination, "bike")
        params = session.get.call_args.kwargs["params"]
        self.assertIn(("profile", "bike"), params)
        self.assertIn(("point", "14.5995,120.9842"), params)
        self.assertIn(("point", "14.5547,121.0244"), params)

    @patch.object(gh, "session")
    def test_invalid_mode_never_calls_api(self, session):
        with self.assertRaises(ServiceError) as ctx:
            gh.route_for(self.origin, self.destination, "boat")
        self.assertEqual(ctx.exception.status, 400)
        session.get.assert_not_called()

    @patch.object(gh, "session")
    def test_no_route_found(self, session):
        session.get.return_value = fake_response(payload={"paths": []})
        with self.assertRaises(ServiceError) as ctx:
            gh.route_for(self.origin, self.destination, "foot")
        self.assertIn("No foot route", str(ctx.exception))

    @patch.object(gh, "session")
    def test_route_without_geometry(self, session):
        session.get.return_value = fake_response(payload={"paths": [{"distance": 10, "points": {"coordinates": []}}]})
        with self.assertRaises(ServiceError) as ctx:
            gh.route_for(self.origin, self.destination, "car")
        self.assertIn("without map geometry", str(ctx.exception))

    @patch.object(gh, "session")
    def test_no_internet_connection(self, session):
        session.get.side_effect = requests.ConnectionError()
        with self.assertRaises(ServiceError) as ctx:
            gh.route_for(self.origin, self.destination, "car")
        self.assertIn("Could not reach GraphHopper", str(ctx.exception))

    @patch.object(gh, "session")
    def test_unreachable_points_surface_api_message(self, session):
        session.get.return_value = fake_response(400, {"message": "Connection between locations not found"})
        with self.assertRaises(ServiceError) as ctx:
            gh.route_for(self.origin, self.destination, "car")
        self.assertEqual(str(ctx.exception), "Connection between locations not found")


# When GraphHopper itself returns an error, the user should see a helpful
# message (wrong key, too many requests) instead of a raw error.
class ApiErrorTests(unittest.TestCase):
    def test_invalid_key_message(self):
        error = gh.api_error(fake_response(401, {"message": "Wrong credentials"}), "fallback")
        self.assertIn("rejected the API key", str(error))
        self.assertEqual(error.status, 502)

    def test_quota_exceeded_keeps_429_status(self):
        error = gh.api_error(fake_response(429, {}), "fallback")
        self.assertIn("request limit", str(error))
        self.assertEqual(error.status, 429)

    def test_uses_first_hint_when_message_missing(self):
        error = gh.api_error(fake_response(400, {"hints": [{"message": "Point 0 is out of bounds"}]}), "fallback")
        self.assertEqual(str(error), "Point 0 is out of bounds")

    def test_falls_back_when_body_is_not_json(self):
        response = fake_response(500)
        response.json.side_effect = ValueError("not json")
        self.assertEqual(str(gh.api_error(response, "Location search failed.")), "Location search failed.")


class RateLimitTests(unittest.TestCase):
    """The API allows 20 credits per client per minute (compare costs 3)."""

    def setUp(self):
        reset_rate_limiter()
        self.client = mapquest.app.test_client()

    @patch.object(routes_api, "geocode", return_value=[])
    def test_blocks_geocode_after_budget_is_used(self, _geocode):
        for _ in range(20):
            self.assertEqual(self.client.get("/api/geocode?q=Manila").status_code, 200)
        response = self.client.get("/api/geocode?q=Manila")
        self.assertEqual(response.status_code, 429)
        self.assertIn("wait a minute", response.json["error"])

    @patch.object(routes_api, "geocode", return_value=[])
    def test_budget_resets_after_window(self, _geocode):
        # Fake the clock: 20 searches at second 1000, then try again 61 seconds later.
        with patch.object(routes_api, "monotonic", return_value=1000.0):
            for _ in range(20):
                self.client.get("/api/geocode?q=Manila")
            self.assertEqual(self.client.get("/api/geocode?q=Manila").status_code, 429)
        with patch.object(routes_api, "monotonic", return_value=1061.0):
            self.assertEqual(self.client.get("/api/geocode?q=Manila").status_code, 200)

    def test_home_page_is_not_rate_limited(self):
        for _ in range(25):
            self.assertEqual(self.client.get("/").status_code, 200)


if __name__ == "__main__":
    unittest.main()
