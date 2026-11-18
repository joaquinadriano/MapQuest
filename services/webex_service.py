"""Post MapQuest trip summaries to a Webex Teams room."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from services.mapquest_service import UNITS, Trip, setting

load_dotenv()

MESSAGES_URL = "https://webexapis.com/v1/messages"
TIMEOUT = (4, 10)
MANILA = timezone(timedelta(hours=8))  # Philippine time has no daylight saving
MODE_NAMES = {"car": "Driving", "bike": "Cycling", "foot": "Walking"}
session = requests.Session()


def settings() -> tuple[str, str]:
    return setting("WEBEX_ACCESS_TOKEN"), setting("WEBEX_ROOM_ID")


def configured() -> bool:
    token, room = settings()
    return bool(token and room)


def _clock(ms: int) -> str:
    """'3:20 PM' in Metro Manila time."""
    return datetime.fromtimestamp(ms / 1000, MANILA).strftime("%I:%M %p").lstrip("0")


def _short(place: str) -> str:
    return place.split(",")[0].strip() or place


def _distance(value: float, unit: str) -> str:
    return f"< 0.1 {unit}" if 0 < value < 0.05 or value == 0 else f"{value:.1f} {unit}"


def _temp(celsius: float, english: bool) -> str:
    return f"{celsius * 9 / 5 + 32:.0f} °F" if english else f"{celsius:.0f} °C"


def _weather_line(label: str, when_ms: int, reading: dict, english: bool) -> str:
    wind = f"{reading['wind_kph'] / 1.609344:.0f} mph" if english else f"{reading['wind_kph']:.0f} km/h"
    return (
        f"- **{label} ({_clock(when_ms)}):** {reading['emoji']} {_temp(reading['temp_c'], english)}, "
        f"{reading['description'].lower()} · feels like {_temp(reading['feels_like_c'], english)} · "
        f"{reading['rain_chance']}% chance of rain · wind {wind}"
    )


def build_summary(trip: Trip, unit: str = "metric", weather: dict | None = None, max_steps: int = 10) -> str:
    """A Markdown trip summary in the chosen unit system ('metric' or 'imperial'), with weather if given."""
    english = unit == "imperial"
    dist_unit = "mi" if english else "km"
    data = trip.to_dict()
    lines = [
        "### 🗺️ Maply trip summary (MapQuest)",
        f"**From:** {trip.origin}  ",
        f"**To:** {trip.destination}  ",
        f"**Mode:** {MODE_NAMES.get(trip.mode, trip.mode)} · **Units:** {UNITS.get(unit, 'Metric')}"
        + ("  " if weather else ""),
    ]
    if weather:
        lines.append(f"**Leave:** {_clock(weather['depart'])} · **Arrive:** about {_clock(weather['arrive'])}")
    lines += [
        "",
        f"- **Distance:** {_distance(data['distance'][dist_unit], dist_unit)}",
        f"- **Trip duration:** {trip.formatted_time}",
    ]
    if data["fuel"]:
        fuel = f"{data['fuel']['gallons']:.2f} gal" if english else f"{data['fuel']['liters']:.2f} L"
        lines.append(f"- **Fuel used (est.):** {fuel}")
    extras = [name for flag, name in ((trip.has_toll_road, "toll roads"), (trip.has_highway, "highways")) if flag]
    if extras:
        lines.append(f"- **Includes:** {', '.join(extras)}")

    if weather:
        lines += ["", "**Weather**"]
        if weather.get("start"):
            lines.append(_weather_line(f"Leaving {_short(trip.origin)}", weather["depart"], weather["start"], english))
        if weather.get("end"):
            lines.append(_weather_line(f"Arriving in {_short(trip.destination)}", weather["arrive"], weather["end"], english))
        lines += [f"- {tip}" for tip in weather.get("advice", [])]

    steps = data["maneuvers"]
    if steps:
        lines += ["", "**Directions**"]
        for step in steps[:max_steps]:
            lines.append(f"{step['number']}. {step['narrative']} ({_distance(step['distance'][dist_unit], dist_unit)})")
        if len(steps) > max_steps:
            more = len(steps) - max_steps
            lines.append(f"_…and {more} more step{'s' if more != 1 else ''}._")
    return "\n".join(lines)


def send_summary(trip: Trip, unit: str = "metric", weather: dict | None = None) -> dict:
    """Post the summary. Never raises: returns {"sent": bool, "message": str} for the page to show."""
    token, room = settings()
    if not (token and room):
        return {"sent": False, "message": "Webex is not set up. Add WEBEX_ACCESS_TOKEN and WEBEX_ROOM_ID to .env."}
    try:
        response = session.post(
            MESSAGES_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"roomId": room, "markdown": build_summary(trip, unit, weather)},
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        return {"sent": False, "message": "Could not reach Webex, so the trip summary was not sent."}

    if response.status_code == 401:
        return {"sent": False, "message": "Webex rejected the access token (it may have expired). Update WEBEX_ACCESS_TOKEN."}
    if response.status_code == 404:
        return {"sent": False, "message": "Webex could not find the room. Check WEBEX_ROOM_ID."}
    if response.status_code >= 400:
        return {"sent": False, "message": f"Webex did not accept the summary (HTTP {response.status_code})."}
    return {"sent": True, "message": "Trip summary sent to Webex."}
