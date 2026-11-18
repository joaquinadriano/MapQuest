"""Tests for the MapQuest panel: MapQuest client, Webex summaries and /api/mapquest (no network)."""

import unittest
from unittest import mock

import requests

import app as maply
from routes import api as routes_api
from services import mapquest_service as mq
from services import webex_service as webex
from services import weather_service as ws
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
        self.assertIn("10.0 mi", text)
        self.assertIn("0.50 gal", text)
        self.assertIn("Units:** English", text)
        self.assertIn("1. Head south on EDSA. (1.0 mi)", text)

    def test_summary_in_metric_units(self):
        text = webex.build_summary(self.trip, "metric")
        self.assertIn("16.1 km", text)
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


NOW = 1_800_000_000_000  # a fixed "now" in ms


def reading(time_ms, temp=30.0, feels=31.0, code=800, pop=0.0, rain_mm=0.0):
    return {"time": time_ms, "temp_c": temp, "feels_like_c": feels, "humidity": 70, "wind_kph": 10.0,
            "rain_mm": rain_mm, "pop": pop, "code": code, "condition": "Clear", "description": "Clear sky", "is_day": True}


def forecast(temp=30.0, feels=31.0, code=800, pop=0.0):
    return {"current": reading(NOW, temp, feels, code, pop),
            "forecast": [reading(NOW + hours * 3_600_000, temp, feels, code, pop) for hours in range(0, 120, 3)],
            "place": "Manila"}


class TripWeatherTests(unittest.TestCase):
    def test_now_uses_current_conditions_and_arrival(self):
        with mock.patch.object(ws, "weather_for", return_value=forecast()):
            weather = ws.trip_weather(ORIGIN, DESTINATION, NOW, 1200, "car", NOW)
        self.assertEqual(weather["start"]["time"], NOW)
        self.assertEqual(weather["arrive"], NOW + 1_200_000)
        self.assertEqual(weather["start"]["emoji"], "☀️")
        self.assertEqual(weather["advice"], ["✅ Good weather for this trip."])

    def test_later_departure_uses_the_forecast(self):
        with mock.patch.object(ws, "weather_for", return_value=forecast()):
            weather = ws.trip_weather(ORIGIN, DESTINATION, NOW + 6 * 3_600_000, 600, "car", NOW)
        self.assertEqual(weather["start"]["time"], NOW + 6 * 3_600_000)

    def test_rain_and_heat_advice(self):
        with mock.patch.object(ws, "weather_for", return_value=forecast(feels=38.0, code=500, pop=0.8)):
            advice = ws.trip_weather(ORIGIN, DESTINATION, NOW, 600, "bike", NOW)["advice"]
        self.assertTrue(advice[0].startswith("🌧️ Rain is likely. Bring rain gear"))
        self.assertTrue(advice[1].startswith("☀️ Hot weather"))

    def test_thunderstorm_is_heavy_rain(self):
        self.assertEqual(ws.rain_risk(reading(NOW, code=211)), "heavy")

    def test_weather_failure_returns_none(self):
        with mock.patch.object(ws, "weather_for", side_effect=ServiceError("down", 502)):
            self.assertIsNone(ws.trip_weather(ORIGIN, DESTINATION, NOW, 600, "car", NOW))

    def test_too_far_ahead_returns_none(self):
        with mock.patch.object(ws, "weather_for", return_value=forecast()):
            self.assertIsNone(ws.trip_weather(ORIGIN, DESTINATION, NOW + 10 * 24 * 3_600_000, 600, "car", NOW))


class WebexWeatherTests(unittest.TestCase):
    def setUp(self):
        self.trip = mq.parse_route(SAMPLE, "Cubao, Quezon City", "Makati CBD", "car")
        with mock.patch.object(ws, "weather_for", return_value=forecast(temp=32.0, feels=38.0, pop=0.1)):
            self.weather = ws.trip_weather(ORIGIN, DESTINATION, NOW, 1500, "car", NOW)

    def test_metric_summary_has_times_and_weather(self):
        text = webex.build_summary(self.trip, "metric", self.weather)
        self.assertIn("**Leave:**", text)
        self.assertIn("**Weather**", text)
        self.assertIn("Leaving Cubao", text)
        self.assertIn("**Leaving Cubao (4:00 PM):**", text)
        self.assertIn("**Arriving in Makati CBD (4:25 PM):**", text)
        self.assertIn("32 °C", text)
        self.assertIn("feels like 38 °C", text)
        self.assertIn("10% chance of rain", text)
        self.assertIn("☀️ Hot weather. Bring water.", text)

    def test_english_summary_uses_fahrenheit_and_mph(self):
        text = webex.build_summary(self.trip, "imperial", self.weather)
        self.assertIn("90 °F", text)
        self.assertIn("6 mph", text)
        self.assertNotIn("°C", text)

    def test_times_are_in_manila_time(self):
        # 1_800_000_000_000 ms is 2027-01-15 08:00 UTC = 4:00 PM in Manila.
        self.assertEqual(webex._clock(NOW), "4:00 PM")

    def test_summary_without_weather_has_no_weather_section(self):
        self.assertNotIn("**Weather**", webex.build_summary(self.trip, "metric"))

    def test_tiny_step_distance(self):
        self.assertEqual(webex._distance(0.0, "km"), "< 0.1 km")
        self.assertEqual(webex._distance(1.25, "km"), "1.2 km")


class MapQuestEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = maply.app.test_client()
        routes_api._request_buckets.clear()

    def test_returns_trip_and_webex_status_in_chosen_units(self):
        trip = mq.parse_route(SAMPLE, ORIGIN["name"], DESTINATION["name"], "car")
        with mock.patch.object(routes_api, "mapquest_route_for", return_value=trip) as route, \
                mock.patch.object(routes_api, "trip_weather", return_value=None), \
                mock.patch.object(routes_api, "send_webex_summary", return_value={"sent": True, "message": "ok"}) as send:
            response = self.client.post("/api/mapquest", json={"origin": ORIGIN, "destination": DESTINATION, "mode": "car", "unit": "imperial"})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["trip"]["distance"]["mi"], 10.0)
        self.assertEqual(body["unit"], "imperial")
        self.assertTrue(body["webex"]["sent"])
        self.assertEqual(route.call_args.args[2], "car")
        send.assert_called_once_with(trip, "imperial", None)

    def test_weather_is_sent_with_the_summary(self):
        trip = mq.parse_route(SAMPLE, ORIGIN["name"], DESTINATION["name"], "car")
        weather = {"depart": NOW, "arrive": NOW, "start": None, "end": None, "advice": ["✅ Good weather for this trip."]}
        with mock.patch.object(routes_api, "mapquest_route_for", return_value=trip), \
                mock.patch.object(routes_api, "trip_weather", return_value=weather) as get_weather, \
                mock.patch.object(routes_api, "send_webex_summary", return_value={"sent": True, "message": "ok"}) as send:
            response = self.client.post("/api/mapquest", json={"origin": ORIGIN, "destination": DESTINATION})
        self.assertEqual(response.get_json()["weather"], weather)
        send.assert_called_once_with(trip, "metric", weather)
        self.assertEqual(get_weather.call_args.args[3], trip.seconds)

    def test_bad_departure_time_rejected(self):
        response = self.client.post("/api/mapquest", json={"origin": ORIGIN, "destination": DESTINATION, "depart_at": "soon"})
        self.assertEqual(response.status_code, 400)

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
