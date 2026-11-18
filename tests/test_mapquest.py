"""Tests for the MapQuest panel: MapQuest client, Webex summaries and /api/mapquest (no network)."""

import unittest
from unittest import mock

import requests

import app as maply
from routes import api as routes_api
from services import mapquest_service as mq
from services import webex_service as webex
from services.graphhopper_service import ServiceError

ORIGIN = {"name": "Cubao, Quezon City", "lat": 14.6197, "lng": 121.0537}
DESTINATION = {"name": "Makati CBD", "lat": 14.5547, "lng": 121.0244}

SAMPLE = {
    "info": {"statuscode": 0, "messages": []},
    "route": {
        "distance": 16.0934,
        "time": 1500,
        "formattedTime": "00:25:00",
        "fuelUsed": 0.5,
        "hasTollRoad": True,
        "hasHighway": False,
        "legs": [
            {
                "maneuvers": [
                    {"narrative": "Head south on EDSA. Go for 1.6 km.", "distance": 1.609344, "time": 40},
                    {"narrative": "Turn right onto Ayala Ave.", "distance": 14.484, "time": 1460},
                ]
            }
        ],
    },
}


def fake_response(status=200, payload=None):
    response = mock.Mock(status_code=status)
    response.json.return_value = payload if payload is not None else {}
    return response


def setUpModule():
    # Never read the developer's real .env (it may hold real keys); tests set os.environ instead.
    patcher = mock.patch.object(mq, "ENV_PATH", "does-not-exist.env")
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


class ParseRouteTests(unittest.TestCase):
    def test_gives_both_unit_systems(self):
        data = mq.parse_route(SAMPLE, "A", "B", "car").to_dict()
        self.assertEqual(data["distance"], {"km": 16.09, "mi": 10.0})
        self.assertEqual(data["fuel"], {"gallons": 0.5, "liters": 1.89})
        self.assertEqual(data["route_type"], "fastest")
        self.assertEqual(data["maneuvers"][0]["number"], 1)
        self.assertEqual(data["maneuvers"][0]["distance"], {"km": 1.61, "mi": 1.0})
        self.assertTrue(data["has_toll_road"])

    def test_no_fuel_reported(self):
        payload = {**SAMPLE, "route": {**SAMPLE["route"], "fuelUsed": None}}
        self.assertIsNone(mq.parse_route(payload, "A", "B", "car").to_dict()["fuel"])

    def test_bad_status_is_friendly(self):
        with self.assertRaises(ServiceError) as ctx:
            mq.parse_route({"info": {"statuscode": 402, "messages": ["x"]}}, "A", "B", "car")
        self.assertIn("could not find a route", str(ctx.exception))


class RouteForTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict("os.environ", {"MAPQUEST_API_KEY": "test-key"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sends_coordinates_km_and_route_type(self):
        with mock.patch.object(mq.session, "get", return_value=fake_response(payload=SAMPLE)) as get:
            trip = mq.route_for(ORIGIN, DESTINATION, "bike")
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["from"], "14.6197,121.0537")
        self.assertEqual(params["to"], "14.5547,121.0244")
        self.assertEqual(params["unit"], "k")
        self.assertEqual(params["routeType"], "bicycle")
        self.assertEqual(trip.origin, "Cubao, Quezon City")

    def test_missing_key(self):
        with mock.patch.dict("os.environ", {"MAPQUEST_API_KEY": "", "MAPQUEST_KEY": ""}):
            with self.assertRaises(ServiceError) as ctx:
                mq.route_for(ORIGIN, DESTINATION)
        self.assertEqual(ctx.exception.status, 503)

    def test_rejected_key(self):
        with mock.patch.object(mq.session, "get", return_value=fake_response(status=401)):
            with self.assertRaises(ServiceError) as ctx:
                mq.route_for(ORIGIN, DESTINATION)
        self.assertIn("rejected", str(ctx.exception))

    def test_timeout(self):
        with mock.patch.object(mq.session, "get", side_effect=requests.Timeout()):
            with self.assertRaises(ServiceError) as ctx:
                mq.route_for(ORIGIN, DESTINATION)
        self.assertEqual(ctx.exception.status, 504)

    def test_commute_mode_rejected(self):
        with self.assertRaises(ServiceError):
            mq.route_for(ORIGIN, DESTINATION, "commute")


class WebexTests(unittest.TestCase):
    def setUp(self):
        self.trip = mq.parse_route(SAMPLE, "Cubao", "Makati", "car")

    def test_summary_in_english_units(self):
        text = webex.build_summary(self.trip, "imperial")
        self.assertIn("**From:** Cubao", text)
        self.assertIn("10.00 mi", text)
        self.assertIn("0.50 gal", text)
        self.assertIn("Units:** English", text)
        self.assertIn("1. Head south on EDSA. (1.00 mi)", text)

    def test_summary_in_metric_units(self):
        text = webex.build_summary(self.trip, "metric")
        self.assertIn("16.09 km", text)
        self.assertIn("1.89 L", text)

    def test_summary_truncates_long_directions(self):
        text = webex.build_summary(self.trip, max_steps=1)
        self.assertIn("1 more step", text)

    def test_not_configured(self):
        with mock.patch.dict("os.environ", {"WEBEX_ACCESS_TOKEN": "", "WEBEX_ROOM_ID": ""}):
            result = webex.send_summary(self.trip)
        self.assertFalse(result["sent"])
        self.assertIn("not set up", result["message"])

    def test_posts_markdown_to_room(self):
        with mock.patch.dict("os.environ", {"WEBEX_ACCESS_TOKEN": "tok", "WEBEX_ROOM_ID": "room-1"}):
            with mock.patch.object(webex.session, "post", return_value=fake_response(payload={"id": "m1"})) as post:
                result = webex.send_summary(self.trip, "metric")
        self.assertTrue(result["sent"])
        self.assertEqual(post.call_args.args[0], webex.MESSAGES_URL)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(post.call_args.kwargs["json"]["roomId"], "room-1")
        self.assertIn("Maply trip summary", post.call_args.kwargs["json"]["markdown"])

    def test_expired_token(self):
        with mock.patch.dict("os.environ", {"WEBEX_ACCESS_TOKEN": "tok", "WEBEX_ROOM_ID": "room"}):
            with mock.patch.object(webex.session, "post", return_value=fake_response(status=401)):
                result = webex.send_summary(self.trip)
        self.assertFalse(result["sent"])
        self.assertIn("expired", result["message"])

    def test_network_error_never_raises(self):
        with mock.patch.dict("os.environ", {"WEBEX_ACCESS_TOKEN": "tok", "WEBEX_ROOM_ID": "room"}):
            with mock.patch.object(webex.session, "post", side_effect=requests.ConnectionError()):
                result = webex.send_summary(self.trip)
        self.assertFalse(result["sent"])


class MapQuestEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = maply.app.test_client()
        routes_api._request_buckets.clear()

    def test_returns_trip_and_webex_status_in_chosen_units(self):
        trip = mq.parse_route(SAMPLE, ORIGIN["name"], DESTINATION["name"], "car")
        with mock.patch.object(routes_api, "mapquest_route_for", return_value=trip) as route, \
                mock.patch.object(routes_api, "send_webex_summary", return_value={"sent": True, "message": "ok"}) as send:
            response = self.client.post("/api/mapquest", json={"origin": ORIGIN, "destination": DESTINATION, "mode": "car", "unit": "imperial"})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["trip"]["distance"]["mi"], 10.0)
        self.assertEqual(body["unit"], "imperial")
        self.assertTrue(body["webex"]["sent"])
        self.assertEqual(route.call_args.args[2], "car")
        send.assert_called_once_with(trip, "imperial")

    def test_outside_metro_manila_rejected(self):
        response = self.client.post("/api/mapquest", json={"origin": {"lat": 38.9, "lng": -77.0}, "destination": DESTINATION})
        self.assertEqual(response.status_code, 400)

    def test_mapquest_error_is_json(self):
        with mock.patch.object(routes_api, "mapquest_route_for", side_effect=ServiceError("MapQuest is down", 502)):
            response = self.client.post("/api/mapquest", json={"origin": ORIGIN, "destination": DESTINATION})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"], "MapQuest is down")


if __name__ == "__main__":
    unittest.main()
