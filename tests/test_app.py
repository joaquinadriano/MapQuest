import unittest
from unittest.mock import patch

import app as mapquest
from routes import api as routes_api
from services.graphhopper_service import ServiceError


class MapQuestEndpointTests(unittest.TestCase):
    def setUp(self):
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

    @patch.object(routes_api, "route_for", return_value={"mode": "car", "distance_m": 5200, "duration_ms": 780000, "geometry": [[14.59, 120.98], [14.55, 121.02]], "instructions": []})
    def test_route_uses_selected_mode_and_returns_route(self, route_for):
        response = self.client.post("/api/route", json={"origin": self.origin, "destination": self.destination, "mode": "car"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["route"]["distance_m"], 5200)
        route_for.assert_called_once_with(self.origin, self.destination, "car")

    def test_route_rejects_invalid_coordinates(self):
        response = self.client.post("/api/route", json={"origin": {"lat": 110, "lng": 1}, "destination": self.destination, "mode": "car"})
        self.assertEqual(response.status_code, 400)

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


if __name__ == "__main__":
    unittest.main()
