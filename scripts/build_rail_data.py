"""Download Metro Manila LRT-1, LRT-2 and MRT-3 station data from OpenStreetMap.

Writes services/data/metro_manila_rail.json, which the built-in rail planner
uses when no Google Maps key is configured. Run it again to refresh the data:

    python scripts/build_rail_data.py

Data (c) OpenStreetMap contributors, available under the Open Database License (ODbL).
"""

import json
import math
import sys
import time
from pathlib import Path

import requests

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
# south, west, north, east around Metro Manila (wide enough for line ends just outside it).
BBOX = "14.30,120.85,14.80,121.20"
OUTPUT = Path(__file__).resolve().parent.parent / "services" / "data" / "metro_manila_rail.json"

# Which OpenStreetMap route relation is which line. Matched against the route's ref/name.
LINES = {
    "LRT-1": {"match": ["lrt-1", "lrt 1", "line 1", "green line", "yellow line"], "name": "LRT Line 1", "color": "#2fa84f"},
    "LRT-2": {"match": ["lrt-2", "lrt 2", "line 2", "purple line"], "name": "LRT Line 2", "color": "#7b3fa0"},
    "MRT-3": {"match": ["mrt-3", "mrt 3", "line 3", "blue line"], "name": "MRT Line 3", "color": "#2a64c5"},
}
QUERY = f"""
[out:json][timeout:90];
relation["type"="route"]["route"~"^(subway|light_rail|train|monorail)$"]({BBOX});
out body;
>;
out body qt;
"""


def fetch() -> dict:
    last_error = None
    for url in OVERPASS_URLS:
        for attempt in range(3):
            try:
                response = requests.post(url, data={"data": QUERY}, headers={"User-Agent": "maply-rail-data-build"}, timeout=120)
                if response.ok and response.text.lstrip().startswith("{"):
                    return response.json()
                last_error = f"{url} answered HTTP {response.status_code}: {response.text[:120]!r}"
            except requests.RequestException as exc:
                last_error = f"{url}: {exc}"
            time.sleep(5 * (attempt + 1))
    sys.exit(f"Could not download OpenStreetMap data. Last error: {last_error}")


def line_for(tags: dict) -> str | None:
    text = " ".join(str(tags.get(key, "")) for key in ("ref", "name", "name:en")).lower()
    for line_id, line in LINES.items():
        if any(pattern in text for pattern in line["match"]):
            return line_id
    return None


def distance_m(a: dict, b: dict) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (a["lat"], a["lng"], b["lat"], b["lng"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(h))


def build(data: dict) -> dict:
    nodes = {element["id"]: element for element in data["elements"] if element["type"] == "node"}
    named = {element["id"]: element for element in data["elements"] if element["type"] == "node" and element.get("tags", {}).get("name")}
    best: dict[str, list] = {}
    for relation in (element for element in data["elements"] if element["type"] == "relation"):
        line_id = line_for(relation.get("tags", {}))
        if not line_id:
            continue
        stations = []
        for member in relation.get("members", []):
            if member["type"] != "node" or not member.get("role", "").startswith("stop"):
                continue
            node = named.get(member["ref"]) or nodes.get(member["ref"])
            if not node:
                continue
            name = (node.get("tags", {}).get("name") or "").replace(" Station", "").replace(" LRT", "").replace(" MRT", "").strip()
            if name and (not stations or stations[-1]["name"] != name):
                stations.append({"name": name, "lat": round(node["lat"], 6), "lng": round(node["lon"], 6)})
        # Each line has one relation per direction; keep the one with the most stops, in one direction.
        if len(stations) > len(best.get(line_id, [])):
            best[line_id] = stations

    missing = [line_id for line_id in LINES if len(best.get(line_id, [])) < 5]
    if missing:
        sys.exit(f"OpenStreetMap data is missing stations for: {', '.join(missing)}")

    lines = {}
    for line_id, stations in best.items():
        # Store each line north/west to south/east consistently isn't needed; just keep OSM order
        # and record the distance along the line to every station.
        along = 0.0
        for index, station in enumerate(stations):
            if index:
                along += distance_m(stations[index - 1], station)
            station["km"] = round(along / 1000, 3)
        lines[line_id] = {"name": LINES[line_id]["name"], "color": LINES[line_id]["color"], "stations": stations}
    return {
        "source": "OpenStreetMap contributors, ODbL (https://www.openstreetmap.org/copyright)",
        "generated": time.strftime("%Y-%m-%d"),
        "lines": lines,
    }


if __name__ == "__main__":
    result = build(fetch())
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for line_id, line in result["lines"].items():
        stations = line["stations"]
        print(f"{line_id}: {len(stations)} stations, {stations[0]['name']} to {stations[-1]['name']}, {stations[-1]['km']} km")
    print(f"Wrote {OUTPUT}")
