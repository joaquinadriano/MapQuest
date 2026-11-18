"""Unit and endpoint tests for Maply.

All GraphHopper calls are mocked, so these tests need no API key, no internet
connection, and consume no API credits. Run with:

    python -m unittest discover -s tests -v
"""

import json
import time
import unittest
from unittest.mock import MagicMock, patch

import requests

import app as maply
from routes import api as routes_api
from services import graphhopper_service as gh
from services.graphhopper_service import ServiceError
from services import commute_service as cs
from services import weather_service as ws


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
class MaplyEndpointTests(unittest.TestCase):
    # setUp runs before every single test so each one starts fresh.
    def setUp(self):
        reset_rate_limiter()
        self.client = maply.app.test_client()
        self.origin = {"name": "Manila", "lat": 14.5995, "lng": 120.9842}
        self.destination = {"name": "Makati", "lat": 14.5547, "lng": 121.0244}

    def test_home_page_loads_the_planner(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Maply", response.data)

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
        # ThreadPoolExecutor runs concurrently; mock function returning per mode
        def mock_route_for(_origin, _dest, mode, **_kwargs):
            if mode == "bike":
                raise ServiceError("Bike profile unavailable", 400)
            return {"distance_m": 1000, "duration_ms": 60000}

        route_for.side_effect = mock_route_for
        response = self.client.post("/api/compare", json={"origin": self.origin, "destination": self.destination})
        self.assertEqual(response.status_code, 200)
        modes_result = {item["mode"]: item for item in response.json["comparison"]}
        self.assertTrue(modes_result["car"]["available"])
        self.assertFalse(modes_result["bike"]["available"])
        self.assertEqual(modes_result["bike"]["error"], "Bike profile unavailable")
        self.assertTrue(modes_result["foot"]["available"])

    def test_route_rejects_identical_origin_and_destination(self):
        response = self.client.post("/api/route", json={"origin": self.origin, "destination": self.origin, "mode": "car"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot be the same", response.json.get("error", ""))

    def test_compare_rejects_identical_origin_and_destination(self):
        response = self.client.post("/api/compare", json={"origin": self.origin, "destination": self.origin})
        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot be the same", response.json.get("error", ""))

    @patch.object(routes_api, "route_for", return_value={"mode": "car", "distance_m": 5200, "duration_ms": 780000})
    def test_route_forwards_custom_fuel_params(self, route_for):
        response = self.client.post(
            "/api/route",
            json={"origin": self.origin, "destination": self.destination, "fuel_efficiency": 15.5, "fuel_price": 70.0},
        )
        self.assertEqual(response.status_code, 200)
        route_for.assert_called_once_with(self.origin, self.destination, "car", fuel_efficiency=15.5, fuel_price=70.0)

    @patch.object(routes_api, "route_for", return_value={"distance_m": 1, "duration_ms": 1})
    def test_compare_covers_every_profile(self, _route_for):
        response = self.client.post("/api/compare", json={"origin": self.origin, "destination": self.destination})
        self.assertEqual([item["mode"] for item in response.json["comparison"]], ["car", "bike", "foot"])

    # The app refuses requests bigger than 32 KB so nobody can flood the server.
    def test_oversized_request_is_rejected(self):
        response = self.client.post("/api/route", data="x" * (33 * 1024), content_type="application/json")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json["error"], "Request is too large.")

    def test_home_page_has_saved_places_and_trip_history(self):
        html = self.client.get("/").get_data(as_text=True)
        for element_id in ("placesList", "historyList", "placeDialog", "historyDialog", "historySearch"):
            self.assertIn(f'id="{element_id}"', html)
        # The app script is an ES module so it can import the storage module.
        self.assertIn('type="module"', html)

    def test_unknown_page_returns_404(self):
        self.assertEqual(self.client.get("/does-not-exist").status_code, 404)

    # Maply is a Metro Manila app: routes outside it are refused before GraphHopper is called.
    @patch.object(routes_api, "route_for")
    def test_route_outside_metro_manila_is_rejected(self, route_for):
        cebu = {"name": "Cebu City", "lat": 10.3157, "lng": 123.8854}
        response = self.client.post("/api/route", json={"origin": self.origin, "destination": cebu, "mode": "car"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Metro Manila", response.json["error"])
        route_for.assert_not_called()

    @patch.object(routes_api, "route_for")
    def test_compare_outside_metro_manila_is_rejected(self, route_for):
        baguio = {"name": "Baguio", "lat": 16.4023, "lng": 120.5960}
        response = self.client.post("/api/compare", json={"origin": baguio, "destination": self.destination})
        self.assertEqual(response.status_code, 400)
        self.assertIn("starting point", response.json["error"])
        route_for.assert_not_called()

    # The browser switches between Drive/Cycle/Walk using these routes, so it
    # never has to ask GraphHopper again after a search.
    @patch.object(routes_api, "route_for")
    def test_compare_returns_full_routes_so_modes_switch_without_new_requests(self, route_for):
        car_route = {"mode": "car", "distance_m": 5200, "duration_ms": 780000, "geometry": [[14.59, 120.98], [14.55, 121.02]], "instructions": []}

        # Modes are fetched in parallel, so answer by mode rather than by call order.
        def fake_route(_origin, _destination, mode, **_kwargs):
            if mode == "bike":
                raise ServiceError("Bike profile unavailable")
            return {**car_route, "mode": mode}

        route_for.side_effect = fake_route
        response = self.client.post("/api/compare", json={"origin": self.origin, "destination": self.destination})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(route_for.call_count, 3)
        comparison = response.json["comparison"]
        self.assertEqual(comparison[0]["route"], car_route)
        self.assertNotIn("route", comparison[1])
        self.assertEqual(comparison[2]["route"]["mode"], "foot")
        self.assertEqual(response.json["origin"]["name"], "Manila")
        self.assertEqual(response.json["destination"]["name"], "Makati")


class MissingApiKeyTests(unittest.TestCase):
    """Objective: handle a missing API key gracefully instead of crashing."""

    def setUp(self):
        reset_rate_limiter()
        self.client = maply.app.test_client()

    @patch.object(gh, "GRAPHHOPPER_KEY", "")
    def test_geocode_without_key_returns_503_with_setup_hint(self):
        response = self.client.get("/api/geocode?q=Manila")
        self.assertEqual(response.status_code, 503)
        self.assertIn("GRAPHHOPPER_API_KEY", response.json["error"])

    @patch.object(gh, "GRAPHHOPPER_KEY", "")
    def test_route_without_key_returns_503(self):
        # Use two different places: the same place twice is rejected with 400
        # before the API key is checked.
        origin = {"lat": 14.6, "lng": 121.0}
        destination = {"lat": 14.55, "lng": 121.02}
        response = self.client.post("/api/route", json={"origin": origin, "destination": destination})
        self.assertEqual(response.status_code, 503)
        self.assertIn("GRAPHHOPPER_API_KEY", response.json["error"])

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
    def test_only_metro_manila_places_are_suggested(self, session):
        session.get.return_value = fake_response(payload={"hits": [
            {"name": "SM City Manila", "city": "Manila", "state": "Metro Manila", "point": {"lat": 14.5898, "lng": 120.9831}},
            # Inside the search box but in Rizal province, so it is dropped.
            {"name": "Cainta", "state": "Rizal", "point": {"lat": 14.5780, "lng": 121.1163}},
            # Outside the box entirely.
            {"name": "Cebu City", "city": "Cebu City", "state": "Central Visayas", "point": {"lat": 10.3157, "lng": 123.8854}},
            # No region given: kept because the city is in Metro Manila.
            {"name": "Ayala Triangle", "city": "Makati", "point": {"lat": 14.5573, "lng": 121.0237}},
        ]})
        results = gh.geocode("SM")
        self.assertEqual([place["name"] for place in results], ["SM City Manila", "Ayala Triangle"])

    @patch.object(gh, "session")
    def test_search_asks_graphhopper_for_metro_manila_only(self, session):
        session.get.return_value = fake_response(payload={"hits": []})
        gh.geocode("Manila")
        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["bbox"], "120.9,14.34,121.14,14.79")
        self.assertEqual(params["point"], "14.5995,120.9842")
        # GraphHopper's free plan rejects searches asking for more than 10 results.
        self.assertLessEqual(params["limit"], 10)

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
        step = route["instructions"][0]
        self.assertEqual({key: step[key] for key in ("text", "distance", "time", "sign")}, {"text": "Turn left", "distance": 100, "time": 6000, "sign": -2})
        # Each step also carries ready-made distances for both unit systems.
        self.assertEqual(step["formatted_metric"], "100 m")
        self.assertEqual(step["formatted_imperial"], "328 ft")
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
def fake_path(minutes, roads, distance=9000):
    """A GraphHopper path that drives along the given (road name, metres) pairs."""
    return {
        "distance": distance, "time": minutes * 60000,
        "points": {"type": "LineString", "coordinates": [[120.98, 14.59], [121.02, 14.55]]},
        "instructions": [{"text": f"Continue onto {name}", "street_name": name, "distance": metres, "time": 1000, "sign": 0} for name, metres in roads],
    }


# Alternative routes: GraphHopper can return up to three different routes for one mode.
@patch.object(gh, "GRAPHHOPPER_KEY", "test-key")
class AlternativeRouteTests(unittest.TestCase):
    origin = {"lat": 14.5895, "lng": 120.9747}
    destination = {"lat": 14.5547, "lng": 121.0244}

    @patch.object(gh, "session")
    def test_returns_routes_fastest_first_with_road_labels(self, session):
        session.get.return_value = fake_response(payload={"paths": [
            fake_path(19, [("Pedro Gil Street", 1900), ("Ayala Avenue", 1200)]),
            fake_path(15, [("Taft Avenue", 2200), ("Ayala Avenue", 1300)]),
            fake_path(16, [("Roxas Boulevard", 3700), ("Ayala Avenue", 1300)]),
        ]})
        routes = gh.route_alternatives(self.origin, self.destination, "car")
        self.assertEqual([route["duration_ms"] // 60000 for route in routes], [15, 16, 19])
        # Ayala Avenue is shared by every route, so each is named after a road only it uses.
        self.assertEqual([route["label"] for route in routes], ["via Taft Avenue", "via Roxas Boulevard", "via Pedro Gil Street"])

    @patch.object(gh, "session")
    def test_asks_graphhopper_for_alternatives(self, session):
        session.get.return_value = fake_response(payload={"paths": [fake_path(15, [("Taft Avenue", 2200)])]})
        gh.route_alternatives(self.origin, self.destination, "bike")
        params = session.get.call_args.kwargs["params"]
        self.assertIn(("algorithm", "alternative_route"), params)
        self.assertIn(("alternative_route.max_paths", "3"), params)
        self.assertIn(("profile", "bike"), params)

    @patch.object(gh, "session")
    def test_single_route_still_works(self, session):
        session.get.return_value = fake_response(payload={"paths": [fake_path(15, [("Taft Avenue", 2200)])]})
        routes = gh.route_alternatives(self.origin, self.destination, "car")
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["label"], "via Taft Avenue")

    @patch.object(gh, "session")
    def test_route_without_road_names_gets_a_numbered_label(self, session):
        session.get.return_value = fake_response(payload={"paths": [fake_path(15, [("", 2200)]), fake_path(17, [("", 900)])]})
        self.assertEqual([route["label"] for route in gh.route_alternatives(self.origin, self.destination, "foot")], ["Route 1", "Route 2"])

    @patch.object(gh, "session")
    def test_skips_an_alternative_without_map_geometry(self, session):
        broken = fake_path(16, [("Roxas Boulevard", 3700)])
        broken["points"] = {"coordinates": []}
        session.get.return_value = fake_response(payload={"paths": [fake_path(15, [("Taft Avenue", 2200)]), broken]})
        self.assertEqual(len(gh.route_alternatives(self.origin, self.destination, "car")), 1)

    def test_endpoint_returns_routes_for_the_chosen_mode(self):
        reset_rate_limiter()
        client = maply.app.test_client()
        routes = [{"label": "via Taft Avenue", "distance_m": 9000, "duration_ms": 900000, "geometry": [], "instructions": []}]
        with patch.object(routes_api, "route_alternatives", return_value=routes) as alternatives:
            response = client.post("/api/alternatives", json={"origin": self.origin, "destination": self.destination, "mode": "bike"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["routes"], routes)
        self.assertEqual(alternatives.call_args.args[2], "bike")

    def test_endpoint_rejects_places_outside_metro_manila(self):
        reset_rate_limiter()
        client = maply.app.test_client()
        with patch.object(routes_api, "route_alternatives") as alternatives:
            response = client.post("/api/alternatives", json={"origin": {"lat": 10.3, "lng": 123.9}, "destination": self.destination})
        self.assertEqual(response.status_code, 400)
        alternatives.assert_not_called()

    def test_endpoint_counts_two_credits_against_the_rate_limit(self):
        reset_rate_limiter()
        client = maply.app.test_client()
        with patch.object(routes_api, "route_alternatives", return_value=[]):
            statuses = [client.post("/api/alternatives", json={"origin": self.origin, "destination": self.destination}).status_code for _ in range(11)]
        # 10 requests x 2 credits uses the whole budget of 20 per minute.
        self.assertEqual(statuses[:10], [200] * 10)
        self.assertEqual(statuses[10], 429)


def owm_entry(dt, temp=30.0, code=800, pop=None, rain=None, icon="01d"):
    """An OpenWeatherMap reading as the API returns it."""
    entry = {"dt": dt, "main": {"temp": temp, "feels_like": temp + 4, "humidity": 70},
             "weather": [{"id": code, "main": "Rain" if code < 700 else "Clear", "description": "light rain" if code < 700 else "clear sky", "icon": icon}],
             "wind": {"speed": 5}}
    if pop is not None:
        entry["pop"] = pop
    if rain is not None:
        entry["rain"] = rain
    return entry


# Weather planning: OpenWeatherMap current weather and 3-hour forecast.
@patch.object(ws, "OPENWEATHER_KEY", "test-key")
class WeatherServiceTests(unittest.TestCase):
    def setUp(self):
        ws.clear_cache()

    def answer(self, session, current=None, forecast=None):
        responses = {
            "weather": fake_response(payload=current or owm_entry(1_800_000_000) | {"name": "Makati"}),
            "forecast": fake_response(payload=forecast or {"city": {"name": "Makati City"}, "list": [
                owm_entry(1_800_010_800, code=500, pop=0.8, rain={"3h": 2.4}),
                owm_entry(1_800_021_600, pop=0.1, icon="01n"),
            ]}),
        }
        session.get.side_effect = lambda url, **_kwargs: responses[url.rsplit("/", 1)[1]]

    @patch.object(ws, "session")
    def test_reads_current_weather_and_forecast(self, session):
        self.answer(session)
        data = ws.weather_for(14.5547, 121.0244)
        self.assertEqual(data["place"], "Makati City")
        self.assertEqual(data["current"]["time"], 1_800_000_000_000)
        self.assertEqual(data["current"]["feels_like_c"], 34.0)
        self.assertEqual(data["current"]["wind_kph"], 18.0)
        self.assertEqual(data["current"]["pop"], 0)
        rainy, night = data["forecast"]
        self.assertEqual((rainy["code"], rainy["pop"], rainy["rain_mm"]), (500, 0.8, 2.4))
        self.assertEqual(rainy["description"], "Light rain")
        self.assertFalse(night["is_day"])

    @patch.object(ws, "session")
    def test_raining_now_counts_as_certain_rain(self, session):
        self.answer(session, current=owm_entry(1_800_000_000, code=501, rain={"1h": 3.1}))
        current = ws.weather_for(14.55, 121.02)["current"]
        self.assertEqual((current["pop"], current["rain_mm"]), (1, 3.1))

    @patch.object(ws, "session")
    def test_asks_for_metric_units_with_the_key(self, session):
        self.answer(session)
        ws.weather_for(14.5547, 121.0244)
        params = session.get.call_args.kwargs["params"]
        self.assertEqual((params["units"], params["appid"], params["lat"], params["lon"]), ("metric", "test-key", 14.5547, 121.0244))

    @patch.object(ws, "session")
    def test_nearby_requests_reuse_the_cached_answer(self, session):
        self.answer(session)
        ws.weather_for(14.5547, 121.0244)
        ws.weather_for(14.5560, 121.0250)  # a few hundred metres away
        self.assertEqual(session.get.call_count, 2, "one current + one forecast call, then cached")

    @patch.object(ws, "session")
    def test_rejected_key_explains_activation_delay(self, session):
        session.get.return_value = fake_response(401, {"message": "Invalid API key"})
        with self.assertRaises(ServiceError) as ctx:
            ws.weather_for(14.55, 121.02)
        self.assertEqual(ctx.exception.status, 503)
        self.assertIn("OPENWEATHER_API_KEY", str(ctx.exception))

    @patch.object(ws, "session")
    def test_rate_limit_and_outage_errors(self, session):
        session.get.return_value = fake_response(429, {})
        with self.assertRaises(ServiceError) as ctx:
            ws.weather_for(14.55, 121.02)
        self.assertEqual(ctx.exception.status, 429)
        session.get.side_effect = requests.ConnectionError("offline")
        with self.assertRaises(ServiceError) as ctx:
            ws.weather_for(14.60, 121.00)
        self.assertIn("Check your connection", str(ctx.exception))

    def test_endpoint_returns_weather_for_a_place(self):
        client = maply.app.test_client()
        with patch.object(routes_api, "weather_for", return_value={"current": {}, "forecast": [], "place": "Makati"}) as weather:
            response = client.get("/api/weather?lat=14.5547&lng=121.0244")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["place"], "Makati")
        weather.assert_called_once_with(14.5547, 121.0244)

    def test_endpoint_rejects_bad_or_outside_places(self):
        client = maply.app.test_client()
        with patch.object(routes_api, "weather_for") as weather:
            self.assertEqual(client.get("/api/weather?lat=abc&lng=121").status_code, 400)
            self.assertEqual(client.get("/api/weather?lat=10.3&lng=123.9").status_code, 400)
        weather.assert_not_called()


class WeatherNotConfiguredTests(unittest.TestCase):
    @patch.object(ws, "OPENWEATHER_KEY", "")
    def test_missing_key_returns_503_with_setup_hint(self):
        ws.clear_cache()
        response = maply.app.test_client().get("/api/weather?lat=14.5547&lng=121.0244")
        self.assertEqual(response.status_code, 503)
        self.assertIn("OPENWEATHER_API_KEY", response.json["error"])


def ai_leg(mode, line, start, end, start_point=None, end_point=None, minutes=10, fare=13, note="Look for the signboard"):
    """One leg as the OpenAI commute answer returns it."""
    start_point, end_point = start_point or (None, None), end_point or (None, None)
    return {"mode": mode, "line": line, "from_stop": start, "to_stop": end,
            "from_lat": start_point[0], "from_lng": start_point[1], "to_lat": end_point[0], "to_lng": end_point[1],
            "minutes": minutes, "fare_php": fare, "instructions": note}


def openai_answer(options, sources=None):
    """A Responses API reply whose text is the commute plan JSON."""
    plan = {"options": options, "sources": sources if sources is not None else [{"title": "Commute guide", "url": "https://example.ph/guide"}]}
    return {"output": [{"type": "web_search_call"}, {"type": "message", "content": [{"type": "output_text", "text": json.dumps(plan)}]}]}


UST = {"name": "UST, Manila", "lat": 14.6096, "lng": 120.9894}
QUIAPO_CHURCH = {"name": "Quiapo Church", "lat": 14.5988, "lng": 120.9838}
INTRAMUROS = {"name": "Intramuros", "lat": 14.5895, "lng": 120.9747}
MEGAMALL = {"name": "SM Megamall", "lat": 14.5849, "lng": 121.0566}
NOW = 1_800_000_000_000  # a Friday, 4 AM in Manila (off-peak)


# Commute mode: OpenAI web search answers, checked and corrected by Maply.
@patch.object(cs, "OPENAI_KEY", "test-key")
class CommuteServiceTests(unittest.TestCase):
    def setUp(self):
        cs.clear_cache()
        # No GraphHopper in these tests: legs stay straight unless a test supplies road paths.
        patcher = patch.object(cs, "road_path", side_effect=ServiceError("no roads in tests"))
        self.road_path = patcher.start()
        self.addCleanup(patcher.stop)

    @patch.object(cs, "session")
    def test_jeepney_trip_with_walks_added_and_times(self, session):
        session.post.return_value = fake_response(payload=openai_answer([{
            "summary": "Jeep along Espana", "total_minutes": 15,
            "legs": [ai_leg("jeepney", "Quiapo (Barbosa) jeep", "Espana Blvd near UST", "Quezon Blvd, Quiapo",
                            (14.6085, 120.9890), (14.5995, 120.9840), minutes=13, fare=13)],
        }]))
        route = cs.commute_routes(UST, QUIAPO_CHURCH, now_ms=NOW)[0]
        self.assertEqual((route["mode"], route["source"], route["label"]), ("commute", "openai", "Jeepney"))
        self.assertEqual([leg["type"] for leg in route["legs"]], ["walk", "transit", "walk"])
        jeep = route["legs"][1]
        self.assertEqual((jeep["vehicle"], jeep["vehicle_name"], jeep["note"]), ("jeepney", "Jeepney", "Look for the signboard"))
        self.assertEqual(jeep["depart_time"], NOW + route["legs"][0]["duration_ms"])
        self.assertEqual(route["duration_ms"], sum(leg["duration_ms"] for leg in route["legs"]))
        self.assertEqual(route["fare"], {"amount": 13, "currency": "PHP", "approximate": True})
        self.assertEqual(route["sources"], [{"title": "Commute guide", "url": "https://example.ph/guide"}])
        self.assertEqual(route["notice"], "", "no AI notes are shown")

    @patch.object(cs, "session")
    def test_jeep_and_walk_legs_follow_real_roads(self, session):
        session.post.return_value = fake_response(payload=openai_answer([{
            "summary": "Jeep", "total_minutes": 15,
            "legs": [ai_leg("jeepney", "Jeep", "Espana", "Quiapo", (14.6085, 120.989), (14.5995, 120.984))]}]))
        road = [[14.6085, 120.989], [14.605, 120.987], [14.5995, 120.984]]
        self.road_path.side_effect = lambda start, end, profile: (road, 1500.0) if profile == "car" else ([[start["lat"], start["lng"]], [end["lat"], end["lng"]]], 400.0)
        route = cs.commute_routes(UST, QUIAPO_CHURCH, now_ms=NOW)[0]
        jeep = route["legs"][1]
        self.assertEqual((jeep["geometry"], jeep["distance_m"]), (road, 1500))
        profiles = sorted(call.args[2] for call in self.road_path.call_args_list)
        self.assertEqual(profiles, ["car", "foot"], "the short last walk stays straight")
        walk = route["legs"][0]
        self.assertEqual((walk["distance_m"], walk["duration_ms"]), (400, cs.rail.walk_ms(400)))

    @patch.object(cs, "session")
    def test_drops_repeats_and_long_walks_between_rides(self, session):
        jeep = ai_leg("jeepney", "Jeep", "Espana", "Quiapo", (14.6085, 120.989), (14.5995, 120.984))
        far_jeep = ai_leg("jeepney", "Jeep 2", "Taft", "Pasay", (14.5600, 120.995), (14.5995, 120.984))
        session.post.return_value = fake_response(payload=openai_answer([
            {"summary": "Jeep", "total_minutes": 15, "legs": [jeep]},
            {"summary": "Same jeep", "total_minutes": 15, "legs": [jeep]},
            {"summary": "Jeep, long walk, jeep", "total_minutes": 40, "legs": [jeep, far_jeep]},
        ]))
        routes = cs.commute_routes(UST, QUIAPO_CHURCH, now_ms=NOW)
        self.assertEqual(len(routes), 1)

    @patch.object(cs, "session")
    def test_train_legs_are_not_sent_to_the_road_router(self, session):
        session.post.return_value = fake_response(payload=openai_answer([{
            "summary": "MRT", "total_minutes": 30,
            "legs": [ai_leg("mrt", "MRT-3", "Taft Avenue", "Ortigas")]}]))
        cs.commute_routes(INTRAMUROS, MEGAMALL, now_ms=NOW)
        self.assertNotIn("car", [call.args[2] for call in self.road_path.call_args_list])

    @patch.object(cs, "session")
    def test_train_legs_use_real_station_data_not_the_ai(self, session):
        # The AI gets the direction, positions and fares wrong; Maply's rail data fixes them.
        session.post.return_value = fake_response(payload=openai_answer([{
            "summary": "LRT-1 then MRT-3", "total_minutes": 50,
            "legs": [ai_leg("lrt", "LRT-1 southbound", "Central Station", "EDSA Station", (1, 1), (2, 2), fare=99),
                     ai_leg("mrt", "MRT-3 southbound", "MRT-3 Taft Avenue Station", "Ortigas Station", fare=99)],
        }]))
        route = cs.commute_routes(INTRAMUROS, MEGAMALL, now_ms=NOW)[0]
        rides = [leg for leg in route["legs"] if leg["type"] == "transit"]
        self.assertEqual([(leg["line"], leg["from_stop"], leg["to_stop"]) for leg in rides],
                         [("LRT-1", "Central Terminal", "EDSA"), ("MRT-3", "Taft Avenue", "Ortigas")])
        self.assertEqual(rides[1]["headsign"], "North Avenue", "Taft Avenue to Ortigas is northbound")
        self.assertLessEqual(rides[1]["fare"], 28)
        self.assertEqual(route["label"], "LRT-1 + MRT-3")
        self.assertEqual(route["transfers"], 1)
        self.assertEqual(route["legs"][2]["type"], "walk", "walk between EDSA and Taft Avenue")

    def test_removes_inline_citations_from_tips(self):
        text = "Alight at Ayala. Fares are discounted ([dotrmrt3.gov.ph](https://www.dotrmrt3.gov.ph/x.pdf?utm_source=openai)). See [LRMC](https://lrmc.ph) too."
        self.assertEqual(cs.clean_text(text), "Alight at Ayala. Fares are discounted. See LRMC too.")

    def test_matches_station_names_written_in_different_ways(self):
        self.assertEqual(cs.match_station("Araneta Center-Cubao LRT-2 Station", "LRT-2"), ("LRT-2", 5))
        self.assertEqual(cs.match_station("Cubao", None, "mrt")[0], "MRT-3")
        self.assertEqual(cs.match_station("Cubao", "LRT-2")[0], "LRT-2")
        self.assertIsNone(cs.match_station("Nowhere Station", None))

    @patch.object(cs, "session")
    def test_drops_legs_and_options_it_cannot_check(self, session):
        session.post.return_value = fake_response(payload=openai_answer([
            # Unknown station names: skipped, so this option has no rides left.
            {"summary": "Mystery train", "total_minutes": 20, "legs": [ai_leg("mrt", "MRT-3", "Atlantis", "El Dorado")]},
            # A bus stop in Cebu is outside Metro Manila: skipped.
            {"summary": "Bus", "total_minutes": 30, "legs": [ai_leg("bus", "Bus", "A", "B", (14.59, 120.98), (10.3, 123.9))]},
            {"summary": "Jeep", "total_minutes": 15, "legs": [ai_leg("jeepney", "Jeep", "Espana", "Quiapo", (14.6085, 120.989), (14.5995, 120.984))]},
        ]))
        routes = cs.commute_routes(UST, QUIAPO_CHURCH, now_ms=NOW)
        self.assertEqual([route["label"] for route in routes], ["Jeepney"])

    @patch.object(cs, "session")
    def test_falls_back_to_trains_when_the_ai_has_nothing_usable(self, session):
        session.post.return_value = fake_response(payload=openai_answer([]))
        route = cs.commute_routes(INTRAMUROS, MEGAMALL, now_ms=NOW)[0]
        self.assertEqual(route["source"], "rail-estimate")

    @patch.object(cs, "session")
    def test_sends_web_search_request_with_trip_details(self, session):
        session.post.return_value = fake_response(payload=openai_answer([]))
        cs.commute_routes(INTRAMUROS, MEGAMALL, now_ms=NOW)
        body = session.post.call_args.kwargs["json"]
        self.assertEqual(body["tools"][0]["type"], "web_search")
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertIn("Intramuros", body["input"])
        self.assertIn("SM Megamall", body["input"])
        self.assertIn("jeepneys", body["instructions"])
        self.assertEqual(session.post.call_args.kwargs["headers"]["Authorization"], "Bearer test-key")

    @patch.object(cs, "session")
    def test_same_trip_is_cached_and_re_timed(self, session):
        session.post.return_value = fake_response(payload=openai_answer([{
            "summary": "Jeep", "total_minutes": 15,
            "legs": [ai_leg("jeepney", "Jeep", "Espana", "Quiapo", (14.6085, 120.989), (14.5995, 120.984))]}]))
        first = cs.commute_routes(UST, QUIAPO_CHURCH, now_ms=NOW)[0]
        later = cs.commute_routes(UST, QUIAPO_CHURCH, depart_at_ms=NOW + 3_600_000)[0]
        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(later["depart_time"] - first["depart_time"], 3_600_000)

    @patch.object(cs, "session")
    def test_daily_limit_protects_the_openai_bill(self, session):
        session.post.return_value = fake_response(payload=openai_answer([]))
        with patch.object(cs, "DAILY_LIMIT", 1):
            cs.commute_routes(INTRAMUROS, MEGAMALL, now_ms=NOW)
            with self.assertRaises(ServiceError) as ctx:
                cs.commute_routes(UST, QUIAPO_CHURCH, now_ms=NOW)
        self.assertEqual(ctx.exception.status, 429)

    @patch.object(cs, "session")
    def test_openai_errors_become_clear_messages(self, session):
        session.post.return_value = fake_response(401, {"error": {"message": "bad key"}})
        with self.assertRaises(ServiceError) as ctx:
            cs.commute_routes(UST, QUIAPO_CHURCH, now_ms=NOW)
        self.assertIn("OPENAI_API_KEY", str(ctx.exception))
        session.post.return_value = fake_response(payload={"output": []})
        with self.assertRaises(ServiceError) as ctx:
            cs.commute_routes(INTRAMUROS, MEGAMALL, now_ms=NOW)
        self.assertIn("could not read", str(ctx.exception))

    @patch.object(cs, "session")
    def test_dropped_connection_is_retried_once(self, session):
        session.post.side_effect = [requests.ConnectionError("RemoteDisconnected"), fake_response(payload=openai_answer([]))]
        cs.commute_routes(INTRAMUROS, MEGAMALL, now_ms=NOW)
        self.assertEqual(session.post.call_count, 2)

    @patch.object(cs, "session")
    def test_unreachable_openai_falls_back_to_rail_without_using_quota(self, session):
        session.post.side_effect = requests.ConnectionError("down")
        route = cs.commute_routes(INTRAMUROS, MEGAMALL, now_ms=NOW)[0]
        self.assertEqual(route["source"], "rail-estimate")
        self.assertEqual(session.post.call_count, 2)
        self.assertEqual(cs._daily["count"], 0)


class CommuteWithoutKeyTests(unittest.TestCase):
    @patch.object(cs, "OPENAI_KEY", "")
    @patch.object(cs, "session")
    def test_without_openai_key_uses_the_built_in_rail_planner(self, session):
        cs.clear_cache()
        route = cs.commute_routes(INTRAMUROS, MEGAMALL, now_ms=NOW)[0]
        self.assertEqual(route["source"], "rail-estimate")
        session.post.assert_not_called()


class CommuteEndpointTests(unittest.TestCase):
    origin = {"lat": 14.5547, "lng": 121.0244}
    destination = {"lat": 14.6565, "lng": 121.0322}

    def setUp(self):
        reset_rate_limiter()
        self.client = maply.app.test_client()

    def post(self, **extra):
        return self.client.post("/api/commute", json={"origin": self.origin, "destination": self.destination, **extra})

    def test_returns_routes(self):
        with patch.object(routes_api, "commute_routes", return_value=[{"label": "Jeepney"}]) as commute:
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["routes"], [{"label": "Jeepney"}])
        self.assertIsNone(commute.call_args.args[2], "no departure time means leave now")

    def test_passes_a_future_departure_time(self):
        later = int(time.time() * 1000) + 3_600_000
        with patch.object(routes_api, "commute_routes", return_value=[]) as commute:
            self.assertEqual(self.post(depart_at=later).status_code, 200)
        self.assertEqual(commute.call_args.args[2], later)

    def test_rejects_bad_or_far_off_departure_times(self):
        with patch.object(routes_api, "commute_routes") as commute:
            self.assertEqual(self.post(depart_at="soon").status_code, 400)
            self.assertEqual(self.post(depart_at=int(time.time() * 1000) - 3_600_000).status_code, 400)
            self.assertEqual(self.post(depart_at=int(time.time() * 1000) + 40 * 86_400_000).status_code, 400)
        commute.assert_not_called()

    def test_rejects_places_outside_metro_manila(self):
        with patch.object(routes_api, "commute_routes") as commute:
            response = self.client.post("/api/commute", json={"origin": {"lat": 10.3, "lng": 123.9}, "destination": self.destination})
        self.assertEqual(response.status_code, 400)
        commute.assert_not_called()

    def test_counts_two_against_the_rate_limit(self):
        with patch.object(routes_api, "commute_routes", return_value=[]):
            statuses = [self.post().status_code for _ in range(11)]
        # 10 searches x 2 uses the whole budget of 20 per minute.
        self.assertEqual(statuses, [200] * 10 + [429])


class ApiErrorTests(unittest.TestCase):
    def test_invalid_key_message(self):
        error = gh.api_error(fake_response(401, {"message": "Wrong credentials"}), "fallback")
        self.assertIn("rejected the API key", str(error))
        # A rejected key is a server setup problem, like a missing key, so both return 503.
        self.assertEqual(error.status, 503)

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
        self.client = maply.app.test_client()

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
