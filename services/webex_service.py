"""Post MapQuest trip summaries to a Webex Teams room."""

from __future__ import annotations

import requests
from dotenv import load_dotenv

from services.mapquest_service import UNITS, Trip, setting

load_dotenv()

MESSAGES_URL = "https://webexapis.com/v1/messages"
TIMEOUT = (4, 10)
MODE_NAMES = {"car": "Driving", "bike": "Cycling", "foot": "Walking"}
session = requests.Session()


def settings() -> tuple[str, str]:
    return setting("WEBEX_ACCESS_TOKEN"), setting("WEBEX_ROOM_ID")


def configured() -> bool:
    token, room = settings()
    return bool(token and room)


def build_summary(trip: Trip, unit: str = "metric", max_steps: int = 10) -> str:
    """A Markdown trip summary in the chosen unit system ('metric' or 'imperial')."""
    english = unit == "imperial"
    dist_unit = "mi" if english else "km"
    data = trip.to_dict()
    lines = [
        "### 🗺️ Maply trip summary (MapQuest)",
        f"**From:** {trip.origin}  ",
        f"**To:** {trip.destination}  ",
        f"**Mode:** {MODE_NAMES.get(trip.mode, trip.mode)} · **Units:** {UNITS.get(unit, 'Metric')}",
        "",
        f"- **Distance:** {data['distance'][dist_unit]:.2f} {dist_unit}",
        f"- **Trip duration:** {trip.formatted_time}",
    ]
    if data["fuel"]:
        fuel = f"{data['fuel']['gallons']:.2f} gal" if english else f"{data['fuel']['liters']:.2f} L"
        lines.append(f"- **Fuel used (est.):** {fuel}")
    extras = [name for flag, name in ((trip.has_toll_road, "toll roads"), (trip.has_highway, "highways")) if flag]
    if extras:
        lines.append(f"- **Includes:** {', '.join(extras)}")
    steps = data["maneuvers"]
    if steps:
        lines += ["", "**Directions**"]
        for step in steps[:max_steps]:
            lines.append(f"{step['number']}. {step['narrative']} ({step['distance'][dist_unit]:.2f} {dist_unit})")
        if len(steps) > max_steps:
            more = len(steps) - max_steps
            lines.append(f"_…and {more} more step{'s' if more != 1 else ''}._")
    return "\n".join(lines)


def send_summary(trip: Trip, unit: str = "metric") -> dict:
    """Post the summary. Never raises: returns {"sent": bool, "message": str} for the page to show."""
    token, room = settings()
    if not (token and room):
        return {"sent": False, "message": "Webex is not set up. Add WEBEX_ACCESS_TOKEN and WEBEX_ROOM_ID to .env."}
    try:
        response = session.post(
            MESSAGES_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"roomId": room, "markdown": build_summary(trip, unit)},
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
