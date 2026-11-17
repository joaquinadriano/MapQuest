"""Tests for the built-in Metro Manila rail planner (services/rail_service.py).

The planner works offline from OpenStreetMap station data, so these tests use
real station positions and need no API key.
"""

import unittest
from datetime import datetime, timedelta, timezone

from services import rail_service as rail
from services.graphhopper_service import ServiceError

MANILA = timezone(timedelta(hours=8))
MORNING = int(datetime(2026, 11, 18, 8, 0, tzinfo=MANILA).timestamp() * 1000)
LATE_NIGHT = int(datetime(2026, 11, 18, 23, 30, tzinfo=MANILA).timestamp() * 1000)

CUBAO = {"lat": 14.6196, "lng": 121.0517}
AYALA = {"lat": 14.5547, "lng": 121.0244}
INTRAMUROS = {"lat": 14.5895, "lng": 120.9747}
MONUMENTO = {"lat": 14.6544, "lng": 120.9838}
QUIAPO = {"lat": 14.5990, "lng": 120.9836}
KATIPUNAN = {"lat": 14.6310, "lng": 121.0730}


def rides(route):
    return [(leg["line"], leg["from_stop"], leg["to_stop"]) for leg in route["legs"] if leg["type"] == "transit"]


class RailNetworkTests(unittest.TestCase):
    def test_has_all_three_lines_with_their_stations(self):
        lines = rail.load_network()["lines"]
        self.assertEqual(set(lines), {"LRT-1", "LRT-2", "MRT-3"})
        self.assertEqual(len(lines["MRT-3"]["stations"]), 13)
        self.assertEqual(len(lines["LRT-2"]["stations"]), 13)
        self.assertGreaterEqual(len(lines["LRT-1"]["stations"]), 20)

    def test_every_transfer_station_exists(self):
        stations = rail.load_network()["stations"]
        names = {(station["line"], station["name"]) for station in stations.values()}
        for a, b, _minutes in rail.TRANSFERS:
            self.assertIn(a, names)
            self.assertIn(b, names)

    def test_fares_stay_within_each_line_range(self):
        self.assertEqual(rail.fare("MRT-3", 0.5), 13.0)
        self.assertEqual(rail.fare("MRT-3", 30), 28.0)
        self.assertLessEqual(rail.fare("LRT-2", 16), 35.0)
        self.assertLessEqual(rail.fare("LRT-1", 24), 45.0)


class RailPlannerTests(unittest.TestCase):
    def test_direct_ride_on_one_line(self):
        best = rail.rail_routes(CUBAO, AYALA, MORNING)[0]
        self.assertEqual(rides(best), [("MRT-3", "Cubao", "Ayala")])
        self.assertEqual(best["transfers"], 0)
        self.assertEqual(best["source"], "rail-estimate")
        self.assertTrue(best["fare"]["approximate"])
        self.assertEqual([leg["type"] for leg in best["legs"]], ["walk", "transit", "walk"])

    def test_headsign_is_the_end_of_the_line_in_the_direction_of_travel(self):
        ride = next(leg for leg in rail.rail_routes(CUBAO, AYALA, MORNING)[0]["legs"] if leg["type"] == "transit")
        self.assertEqual(ride["headsign"], "Taft Avenue")
        back = next(leg for leg in rail.rail_routes(AYALA, CUBAO, MORNING)[0]["legs"] if leg["type"] == "transit")
        self.assertEqual(back["headsign"], "North Avenue")

    def test_transfer_from_lrt1_to_mrt3_at_edsa_taft(self):
        best = rail.rail_routes(INTRAMUROS, AYALA, MORNING)[0]
        self.assertEqual([ride[0] for ride in rides(best)], ["LRT-1", "MRT-3"])
        self.assertEqual(rides(best)[0][2], "EDSA")
        self.assertEqual(rides(best)[1][1], "Taft Avenue")
        self.assertEqual(best["transfers"], 1)
        self.assertEqual(best["label"], "LRT-1 + MRT-3")

    def test_transfer_from_lrt1_to_lrt2_at_doroteo_jose_recto(self):
        best = rail.rail_routes(MONUMENTO, CUBAO, MORNING)[0]
        self.assertIn(("LRT-1", "Yamaha Monumento", "Doroteo Jose"), rides(best))
        self.assertEqual(rides(best)[-1][0:2], ("LRT-2", "Recto"))

    def test_times_add_up_and_fastest_comes_first(self):
        routes = rail.rail_routes(QUIAPO, KATIPUNAN, MORNING)
        durations = [route["duration_ms"] for route in routes]
        self.assertEqual(durations, sorted(durations))
        best = routes[0]
        self.assertEqual(best["duration_ms"], sum(leg["duration_ms"] for leg in best["legs"]))
        self.assertGreater(best["arrive_time"], best["depart_time"])
        self.assertLessEqual(len(routes), rail.MAX_ROUTES)

    def test_does_not_add_a_pointless_extra_ride(self):
        # Quiapo to Katipunan: walk to Recto and ride LRT-2, not LRT-1 for one stop first.
        for route in rail.rail_routes(QUIAPO, KATIPUNAN, MORNING):
            self.assertNotIn(("LRT-1", "Carriedo", "Doroteo Jose"), rides(route))

    def test_walking_is_suggested_for_short_trips(self):
        with self.assertRaises(ServiceError) as ctx:
            rail.rail_routes(AYALA, {"lat": 14.5560, "lng": 121.0270}, MORNING)
        self.assertEqual(ctx.exception.status, 404)
        self.assertIn("Walking is faster", str(ctx.exception))

    def test_no_station_within_walking_distance(self):
        alabang = {"lat": 14.4180, "lng": 121.0430}
        with self.assertRaises(ServiceError) as ctx:
            rail.rail_routes(alabang, AYALA, MORNING)
        self.assertEqual(ctx.exception.status, 404)

    def test_notice_when_trains_may_not_be_running(self):
        self.assertEqual(rail.rail_routes(CUBAO, AYALA, MORNING)[0]["notice"], "")
        self.assertIn("4:30 AM", rail.rail_routes(CUBAO, AYALA, LATE_NIGHT)[0]["notice"])


if __name__ == "__main__":
    unittest.main()
