"""Unit conversion and calculation utilities for distance, duration, and fuel.

Provides standardized metric and imperial conversion logic and human-friendly
formatting across both the REST API and CLI tools.
"""

from __future__ import annotations

METERS_PER_KILOMETER: float = 1000.0
METERS_PER_MILE: float = 1609.344
FEET_PER_METER: float = 3.28084
GALLONS_PER_LITER: float = 0.264172


def meters_to_km(meters: float, decimals: int = 1) -> float:
    """Convert meters to kilometers, rounded to specified decimal places."""
    return round(float(meters) / METERS_PER_KILOMETER, decimals)


def meters_to_miles(meters: float, decimals: int = 1) -> float:
    """Convert meters to international miles, rounded to specified decimal places."""
    return round(float(meters) / METERS_PER_MILE, decimals)


def meters_to_feet(meters: float) -> int:
    """Convert meters to feet, rounded to the nearest integer."""
    return round(float(meters) * FEET_PER_METER)


def format_step_distance(meters: float, unit: str = "metric") -> str:
    """Format step turn distance depending on selected unit system.

    For metric: under 1000 meters displays in meters ('300 m'), otherwise 'X.X km'.
    For imperial: under 0.1 miles (~160m) displays in feet ('450 ft'), otherwise 'X.X mi'.
    """
    m = max(0.0, float(meters))
    if unit.lower() == "imperial":
        if m < 160.934:
            return f"{meters_to_feet(m)} ft"
        return f"{meters_to_miles(m, 1):.1f} mi"
    if m < 1000.0:
        return f"{round(m)} m"
    return f"{meters_to_km(m, 1):.1f} km"


def format_duration(milliseconds: float) -> str:
    """Format a millisecond duration into a clean human-readable string (e.g. '1 hr 15 min' or '12 min')."""
    total_minutes = max(1, round(float(milliseconds) / 60000.0))
    if total_minutes < 60:
        return f"{total_minutes} min"
    hours = total_minutes // 60
    rest_minutes = total_minutes % 60
    return f"{hours} hr {rest_minutes} min" if rest_minutes else f"{hours} hr"


def parse_duration(milliseconds: float) -> dict[str, int | str]:
    """Parse duration milliseconds into structured components (hours, minutes, seconds)."""
    ms = max(0.0, float(milliseconds))
    total_seconds = int(ms / 1000.0)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return {
        "milliseconds": int(ms),
        "total_seconds": total_seconds,
        "hours": hours,
        "minutes": minutes,
        "seconds": seconds,
        "formatted": format_duration(ms),
    }


def calculate_fuel(
    distance_m: float,
    efficiency_km_per_l: float = 12.0,
    price_per_l: float = 65.0,
) -> dict[str, float | str]:
    """Calculate estimated fuel consumption and cost in Philippine Pesos (PHP)."""
    distance_km = max(0.0, float(distance_m)) / METERS_PER_KILOMETER
    distance_mi = max(0.0, float(distance_m)) / METERS_PER_MILE
    eff = max(0.1, float(efficiency_km_per_l))
    price = max(0.0, float(price_per_l))

    liters = distance_km / eff
    gallons = liters * GALLONS_PER_LITER
    cost = liters * price

    return {
        "distance_km": round(distance_km, 2),
        "distance_mi": round(distance_mi, 2),
        "fuel_liters": round(liters, 2),
        "fuel_gallons": round(gallons, 2),
        "efficiency_km_per_l": eff,
        "price_per_l": price,
        "estimated_cost_php": round(cost, 2),
        "formatted_cost": f"₱{cost:,.2f}",
        "formatted_liters": f"{liters:.1f} L",
    }


def build_route_summary(
    distance_m: float,
    duration_ms: float,
    fuel_efficiency: float = 12.0,
    fuel_price: float = 65.0,
) -> dict:
    """Generate an enriched summary dictionary containing both metric & imperial data and fuel cost."""
    dist_m = max(0.0, float(distance_m))
    dur_ms = max(0.0, float(duration_ms))

    km = meters_to_km(dist_m, 1)
    mi = meters_to_miles(dist_m, 1)

    return {
        "distance": {
            "meters": dist_m,
            "kilometers": km,
            "miles": mi,
            "text_metric": f"{km:.1f} km",
            "text_imperial": f"{mi:.1f} mi",
        },
        "duration": parse_duration(dur_ms),
        "fuel_estimate": calculate_fuel(dist_m, fuel_efficiency, fuel_price),
    }
