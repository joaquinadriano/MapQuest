"""Unit tests for metric/miles conversions, duration parsing, and fuel calculations."""

import unittest

from services.converters import (
    build_route_summary,
    calculate_fuel,
    format_duration,
    format_step_distance,
    meters_to_feet,
    meters_to_km,
    meters_to_miles,
    parse_duration,
)


class ConverterTests(unittest.TestCase):
    def test_meters_to_km(self):
        self.assertEqual(meters_to_km(0), 0.0)
        self.assertEqual(meters_to_km(1000), 1.0)
        self.assertEqual(meters_to_km(5200), 5.2)
        self.assertEqual(meters_to_km(12345, decimals=2), 12.35)

    def test_meters_to_miles(self):
        self.assertEqual(meters_to_miles(0), 0.0)
        self.assertEqual(meters_to_miles(1609.344), 1.0)
        self.assertEqual(meters_to_miles(3218.688), 2.0)
        self.assertEqual(meters_to_miles(8046.72, decimals=2), 5.0)

    def test_meters_to_feet(self):
        self.assertEqual(meters_to_feet(0), 0)
        self.assertEqual(meters_to_feet(1), 3)
        self.assertEqual(meters_to_feet(100), 328)

    def test_format_step_distance_metric(self):
        self.assertEqual(format_step_distance(0, "metric"), "0 m")
        self.assertEqual(format_step_distance(450, "metric"), "450 m")
        self.assertEqual(format_step_distance(999, "metric"), "999 m")
        self.assertEqual(format_step_distance(1000, "metric"), "1.0 km")
        self.assertEqual(format_step_distance(2540, "metric"), "2.5 km")

    def test_format_step_distance_imperial(self):
        self.assertEqual(format_step_distance(0, "imperial"), "0 ft")
        self.assertEqual(format_step_distance(50, "imperial"), "164 ft")
        self.assertEqual(format_step_distance(160, "imperial"), "525 ft")
        self.assertEqual(format_step_distance(161, "imperial"), "0.1 mi")
        self.assertEqual(format_step_distance(1609.344, "imperial"), "1.0 mi")

    def test_format_duration(self):
        self.assertEqual(format_duration(30000), "1 min")
        self.assertEqual(format_duration(600000), "10 min")
        self.assertEqual(format_duration(3600000), "1 hr")
        self.assertEqual(format_duration(5400000), "1 hr 30 min")
        self.assertEqual(format_duration(9000000), "2 hr 30 min")

    def test_parse_duration(self):
        parsed = parse_duration(3665000)
        self.assertEqual(parsed["hours"], 1)
        self.assertEqual(parsed["minutes"], 1)
        self.assertEqual(parsed["seconds"], 5)
        self.assertEqual(parsed["formatted"], "1 hr 1 min")

    def test_calculate_fuel(self):
        # 12 km, 12 km/L efficiency, 65 PHP/L -> 1.0 L, 65.00 PHP
        fuel = calculate_fuel(12000, efficiency_km_per_l=12.0, price_per_l=65.0)
        self.assertEqual(fuel["distance_km"], 12.0)
        self.assertEqual(fuel["fuel_liters"], 1.0)
        self.assertEqual(fuel["estimated_cost_php"], 65.0)
        self.assertEqual(fuel["formatted_cost"], "₱65.00")
        self.assertEqual(fuel["formatted_liters"], "1.0 L")

    def test_calculate_fuel_handles_zero_and_defaults(self):
        fuel = calculate_fuel(0)
        self.assertEqual(fuel["distance_km"], 0.0)
        self.assertEqual(fuel["fuel_liters"], 0.0)
        self.assertEqual(fuel["estimated_cost_php"], 0.0)

    def test_build_route_summary(self):
        summary = build_route_summary(5200, 780000)
        self.assertIn("distance", summary)
        self.assertIn("duration", summary)
        self.assertIn("fuel_estimate", summary)
        self.assertEqual(summary["distance"]["kilometers"], 5.2)
        self.assertEqual(summary["duration"]["formatted"], "13 min")
        self.assertGreater(summary["fuel_estimate"]["estimated_cost_php"], 0)


if __name__ == "__main__":
    unittest.main()
