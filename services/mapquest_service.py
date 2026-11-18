"""MapQuest Directions API integration.

Maply asks MapQuest for the same trip GraphHopper planned and shows MapQuest's
summary and turn-by-turn directions as tables. MapQuest is always asked for
kilometres; miles and US gallons are worked out here, so the browser can switch
between metric and English units without another (billable) request.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import requests
from dotenv import dotenv_values, load_dotenv

from services.graphhopper_service import ServiceError

load_dotenv()

ROUTE_URL = "https://www.mapquestapi.com/directions/v2/route"
TIMEOUT = (4, 16)
KM_PER_MILE = 1.609344
LITERS_PER_GALLON = 3.78541
session = requests.Session()
# MapQuest ends each step with "Go for 1.1 km." in the requested unit; the tables show the
# step distance in the user's unit instead, so this is removed.
GO_FOR = re.compile(r"\s*Go for [\d.,]+ (?:m|km|ft|mi)\.?\s*$")

# Maply travel modes -> MapQuest route types.
ROUTE_TYPES = {"car": "fastest", "bike": "bicycle", "foot": "pedestrian"}
UNITS = {"metric": "Metric", "imperial": "English"}

# Friendlier text for the MapQuest status codes people actually run into.
STATUS_MESSAGES = {
    400: "MapQuest could not understand the request.",
    401: "MapQuest rejected the API key. Check MAPQUEST_API_KEY in .env.",
    402: "MapQuest could not find a route between these places.",
    403: "This MapQuest API key does not have access to directions.",
    500: "MapQuest had an internal error. Try again shortly.",
    601: "MapQuest could not read one of the locations.",
    602: "MapQuest could not find one of the locations.",
    611: "MapQuest needs a start and a destination.",
    612: "MapQuest could not resolve one of the locations.",
}


@dataclass
class Maneuver:
    narrative: str
    distance_km: float
    seconds: int


@dataclass
class Trip:
    origin: str
    destination: str
    mode: str
    route_type: str
    distance_km: float
    seconds: int
    formatted_time: str
    fuel_gallons: float | None
    has_toll_road: bool
    has_highway: bool
    maneuvers: list[Maneuver] = field(default_factory=list)

    def to_dict(self) -> dict:
        fuel = None
        if self.fuel_gallons is not None:
            fuel = {"gallons": round(self.fuel_gallons, 2), "liters": round(self.fuel_gallons * LITERS_PER_GALLON, 2)}
        return {
            "origin": self.origin,
            "destination": self.destination,
            "mode": self.mode,
            "route_type": self.route_type,
            "distance": {"km": round(self.distance_km, 2), "mi": round(self.distance_km / KM_PER_MILE, 2)},
            "seconds": self.seconds,
            "formatted_time": self.formatted_time,
            "fuel": fuel,
            "has_toll_road": self.has_toll_road,
            "has_highway": self.has_highway,
            "maneuvers": [
                {
                    "number": index,
                    "narrative": step.narrative,
                    "distance": {"km": round(step.distance_km, 2), "mi": round(step.distance_km / KM_PER_MILE, 2)},
                    "seconds": step.seconds,
                }
                for index, step in enumerate(self.maneuvers, start=1)
            ],
        }


ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def setting(name: str) -> str:
    """A value from .env, read fresh each time so edits work without restarting Maply.

    Falls back to the process environment (for hosting where there is no .env file).
    """
    try:
        value = dotenv_values(ENV_PATH).get(name)
    except OSError:
        value = None
    return str(value or os.getenv(name) or "").strip()


def api_key() -> str:
    return setting("MAPQUEST_API_KEY") or setting("MAPQUEST_KEY")


def parse_route(data: dict, origin: str, destination: str, mode: str) -> Trip:
    """Turn a MapQuest route response (requested in km) into a :class:`Trip`."""
    info = data.get("info") or {}
    status = info.get("statuscode", 0)
    if status != 0:
        messages = [message for message in info.get("messages", []) if message]
        detail = STATUS_MESSAGES.get(status) or (messages[0] if messages else "MapQuest could not plan this route.")
        raise ServiceError(detail, 503 if status in (401, 403) else 502)

    route = data.get("route") or {}
    if not route.get("legs"):
        raise ServiceError("MapQuest returned no route for these places.", 502)

    fuel = route.get("fuelUsed")
    maneuvers = [
        Maneuver(
            narrative=GO_FOR.sub("", str(step.get("narrative") or "")) or "Continue",
            distance_km=float(step.get("distance") or 0.0),
            seconds=int(step.get("time") or 0),
        )
        for leg in route["legs"]
        for step in leg.get("maneuvers", [])
    ]
    return Trip(
        origin=origin,
        destination=destination,
        mode=mode,
        route_type=ROUTE_TYPES[mode],
        distance_km=float(route.get("distance") or 0.0),
        seconds=int(route.get("time") or 0),
        formatted_time=str(route.get("formattedTime") or "00:00:00"),
        fuel_gallons=float(fuel) if fuel is not None else None,
        has_toll_road=bool(route.get("hasTollRoad")),
        has_highway=bool(route.get("hasHighway")),
        maneuvers=maneuvers,
    )


def route_for(origin: dict, destination: dict, mode: str = "car") -> Trip:
    """Ask MapQuest for a route between two Maply places ({name, lat, lng})."""
    key = api_key()
    if not key:
        raise ServiceError("MapQuest is not configured. Add MAPQUEST_API_KEY to your .env file and restart Maply.", 503)
    if mode not in ROUTE_TYPES:
        raise ServiceError("MapQuest directions are available for driving, cycling and walking.", 400)

    params = {
        "key": key,
        "from": f"{origin['lat']},{origin['lng']}",
        "to": f"{destination['lat']},{destination['lng']}",
        "unit": "k",
        "routeType": ROUTE_TYPES[mode],
        "narrativeType": "text",
        "locale": "en_US",
    }
    try:
        response = session.get(ROUTE_URL, params=params, timeout=TIMEOUT)
    except requests.Timeout as exc:
        raise ServiceError("MapQuest took too long to answer. Please try again.", 504) from exc
    except requests.RequestException as exc:
        raise ServiceError("Could not reach MapQuest. Check your connection and try again.", 502) from exc

    if response.status_code in (401, 403):
        raise ServiceError(STATUS_MESSAGES[response.status_code], 503)
    if response.status_code == 429:
        raise ServiceError("MapQuest request limit reached. Please try again later.", 429)
    try:
        data = response.json()
    except ValueError as exc:
        raise ServiceError("MapQuest sent a reply Maply could not read.", 502) from exc

    return parse_route(data, origin.get("name") or "Start", destination.get("name") or "Destination", mode)
