"""Commute mode: jeepney, bus, UV Express and LRT/MRT routes found on the web by OpenAI.

The OpenAI Responses API with its web search tool reads commuter guides (Sakay.ph,
Moovit, Rome2Rio, news and forums) and answers in a fixed JSON format. Maply then
checks every leg before showing it:

- LRT/MRT legs are matched to the real stations (OpenStreetMap data), so their
  positions, direction, ride time and fare come from Maply's rail data, not the AI.
- Jeepney/bus stops must be inside Metro Manila; otherwise the previous or next
  known point is used.
- The trip always starts at the origin and ends at the destination.

Answers are cached for a day, each client is rate-limited, and there is a daily cap
(COMMUTE_DAILY_LIMIT) so the OpenAI bill stays predictable. Without an OpenAI key,
or if OpenAI fails, the built-in LRT/MRT planner is used instead.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic

import requests
from dotenv import load_dotenv

from services import rail_service as rail
from services.graphhopper_service import ServiceError, in_metro_manila_box

load_dotenv()

OPENAI_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini").strip() or "gpt-5.4-mini"
DAILY_LIMIT = int(os.getenv("COMMUTE_DAILY_LIMIT", "200") or 200)
OPENAI_URL = "https://api.openai.com/v1/responses"
TIMEOUT = (5, 120)  # web search answers usually take 20-40 seconds
CACHE_SECONDS = 24 * 3600
MANILA = timezone(timedelta(hours=8))
WALK_DETOUR = rail.WALK_DETOUR
session = requests.Session()
_cache: dict[tuple, tuple[float, dict]] = {}
_lock = Lock()
_daily = {"day": None, "count": 0}

MODES = ["walk", "jeepney", "bus", "p2p_bus", "uv_express", "lrt", "mrt", "tricycle", "ferry"]
VEHICLES = {
    "jeepney": ("jeepney", "Jeepney", "#e0a100"),
    "bus": ("bus", "Bus", "#1f5fa8"),
    "p2p_bus": ("bus", "P2P bus", "#0e7490"),
    "uv_express": ("van", "UV Express", "#6b7280"),
    "tricycle": ("tricycle", "Tricycle", "#a16207"),
    "ferry": ("ferry", "Ferry", "#0284c7"),
}
RAIL_MODES = {"lrt", "mrt"}

_LEG_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["mode", "line", "from_stop", "to_stop", "from_lat", "from_lng", "to_lat", "to_lng", "minutes", "fare_php", "instructions"],
    "properties": {
        "mode": {"type": "string", "enum": MODES},
        "line": {"type": "string", "description": "Route or signboard, e.g. 'Quiapo - Cubao jeep', 'EDSA Carousel', 'LRT-1'"},
        "from_stop": {"type": "string"}, "to_stop": {"type": "string"},
        "from_lat": {"type": ["number", "null"]}, "from_lng": {"type": ["number", "null"]},
        "to_lat": {"type": ["number", "null"]}, "to_lng": {"type": ["number", "null"]},
        "minutes": {"type": "integer"},
        "fare_php": {"type": ["number", "null"]},
        "instructions": {"type": "string", "description": "Signboard to look for, where to wait, where to get off"},
    },
}
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["options", "sources"],
    "properties": {
        "options": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["summary", "total_minutes", "legs", "notes"],
            "properties": {
                "summary": {"type": "string"},
                "total_minutes": {"type": "integer"},
                "legs": {"type": "array", "items": _LEG_SCHEMA},
                "notes": {"type": "string"},
            },
        }},
        "sources": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "url"],
            "properties": {"title": {"type": "string"}, "url": {"type": "string"}},
        }},
    },
}
INSTRUCTIONS = (
    "You are a Metro Manila commuting guide. Search the web (commuter guides, Sakay.ph, Moovit, Rome2Rio, "
    "news and forums) for how people actually commute between the two places using jeepneys, buses "
    "(including the EDSA Carousel busway and P2P buses), UV Express, tricycles and the LRT-1, LRT-2 and MRT-3 lines. "
    "Give 2 or 3 realistic options, fastest or most common first, as legs from the starting point to the destination, "
    "including walks. For each leg give the route or signboard, where to board and get off, approximate coordinates "
    "of each stop, minutes and fare in Philippine pesos. Use current fares. Keep 'summary' short. Do not invent routes: "
    "if you are unsure about a jeepney or bus route, say so in notes. Only include places in Metro Manila."
)


def commute_available() -> bool:
    return bool(OPENAI_KEY)


def _is_peak(depart_ms: int) -> bool:
    local = datetime.fromtimestamp(depart_ms / 1000, MANILA)
    return local.weekday() < 5 and (6 <= local.hour < 10 or 16 <= local.hour < 20)


def _ask_openai(origin: dict, destination: dict, depart_ms: int) -> dict:
    local = datetime.fromtimestamp(depart_ms / 1000, MANILA)
    body = {
        "model": OPENAI_MODEL,
        "tools": [{"type": "web_search", "user_location": {"type": "approximate", "country": "PH", "city": "Manila"}}],
        "instructions": INSTRUCTIONS,
        "input": (
            f"From: {origin.get('name') or 'starting point'} ({origin['lat']:.5f}, {origin['lng']:.5f})\n"
            f"To: {destination.get('name') or 'destination'} ({destination['lat']:.5f}, {destination['lng']:.5f})\n"
            f"Departing: {local.strftime('%A %I:%M %p')} ({'rush hour' if _is_peak(depart_ms) else 'off-peak'})"
        ),
        "text": {"format": {"type": "json_schema", "name": "commute_plan", "strict": True, "schema": SCHEMA}},
    }
    try:
        response = session.post(OPENAI_URL, json=body, headers={"Authorization": f"Bearer {OPENAI_KEY}"}, timeout=TIMEOUT)
    except requests.Timeout as exc:
        raise ServiceError("The commute search took too long. Please try again.", 504) from exc
    except requests.RequestException as exc:
        raise ServiceError("Could not reach OpenAI. Check your connection and try again.", 502) from exc
    if response.status_code == 401:
        raise ServiceError("OpenAI rejected the API key. Check OPENAI_API_KEY in .env.", 503)
    if response.status_code == 429:
        raise ServiceError("OpenAI is busy or the account has run out of credit. Please try again later.", 429)
    if not response.ok:
        raise ServiceError("Commute search is not available right now.", 502)
    try:
        data = response.json()
        text = next(part["text"] for item in data.get("output", []) if item.get("type") == "message"
                    for part in item.get("content", []) if part.get("type") == "output_text")
        return json.loads(text)
    except (ValueError, StopIteration, KeyError, TypeError) as exc:
        raise ServiceError("The commute search returned an answer Maply could not read. Please try again.", 502) from exc


def _normalize(name: str) -> str:
    name = name.lower()
    name = re.sub(r"\b(lrt|mrt)[\s-]*\d\b|\bline\s*\d\b|\bstation\b|\bstn\b|\b(lrt|mrt)\b", " ", name)
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def _line_hint(text: str) -> str | None:
    text = text.lower()
    for line_id, patterns in {"LRT-1": ("lrt-1", "lrt 1", "lrt1", "line 1", "green line"),
                              "LRT-2": ("lrt-2", "lrt 2", "lrt2", "line 2", "purple line"),
                              "MRT-3": ("mrt-3", "mrt 3", "mrt3", "line 3", "blue line")}.items():
        if any(pattern in text for pattern in patterns):
            return line_id
    return None


def match_station(stop_name: str, line_hint: str | None, mode: str | None = None) -> tuple | None:
    """Find the real station for a stop name, preferring the named line. Returns a network node."""
    network = rail.load_network()
    wanted = _normalize(stop_name)
    if not wanted:
        return None
    candidates = []
    for node, station in network["stations"].items():
        name = _normalize(station["name"])
        if not name:
            continue
        if wanted == name or wanted in name or name in wanted:
            score = (0 if node[0] == line_hint else 1, 0 if wanted == name else 1, abs(len(name) - len(wanted)))
            if mode == "mrt" and not node[0].startswith("MRT") or mode == "lrt" and not node[0].startswith("LRT"):
                score = (score[0] + 1,) + score[1:]
            candidates.append((score, node))
    return min(candidates)[1] if candidates else None


_CITATION = re.compile(r"\s*\(\[[^\]]*\]\([^)]*\)\)")
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def clean_text(text: str, limit: int = 400) -> str:
    """Remove inline citations like ([site](url)); sources are listed separately."""
    text = _CITATION.sub("", str(text or ""))
    text = _MARKDOWN_LINK.sub(lambda match: match.group(1), text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _point(lat, lng) -> dict | None:
    try:
        point = {"lat": float(lat), "lng": float(lng)}
    except (TypeError, ValueError):
        return None
    return point if in_metro_manila_box(point["lat"], point["lng"]) else None


def _rail_leg(raw: dict) -> dict | None:
    """Rebuild an LRT/MRT leg from Maply's station data (positions, direction, time, fare)."""
    hint = _line_hint(f"{raw['line']} {raw['from_stop']} {raw['to_stop']}")
    board = match_station(raw["from_stop"], hint, raw["mode"])
    alight = match_station(raw["to_stop"], hint or (board[0] if board else None), raw["mode"])
    if not board or not alight or board == alight:
        return None
    network = rail.load_network()
    if board[0] != alight[0]:
        # Same station name on the other line (e.g. Cubao); use the board line if it has it.
        same_line = match_station(raw["to_stop"], board[0], raw["mode"])
        if not same_line or same_line[0] != board[0]:
            return None
        alight = same_line
    line_id = board[0]
    stations = network["stations"]
    first, last = stations[board], stations[alight]
    step = 1 if alight[1] > board[1] else -1
    path = [(line_id, index) for index in range(board[1], alight[1] + step, step)]
    km = abs(last["km"] - first["km"])
    line = network["lines"][line_id]
    profile = rail.LINE_PROFILE[line_id]
    ride_ms = int(km / profile["speed_kmh"] * 3_600_000) + profile["wait_min"] * 60_000
    return {
        "type": "transit", "vehicle": "train", "vehicle_name": "Train", "line": line_id, "line_name": line["name"],
        "color": line["color"], "text_color": "#ffffff",
        "headsign": (line["stations"][-1] if step > 0 else line["stations"][0])["name"],
        "from_stop": first["name"], "to_stop": last["name"], "stops": len(path) - 1,
        "distance_m": round(km * 1000), "duration_ms": ride_ms, "fare": rail.fare(line_id, km),
        "note": clean_text(raw.get("instructions", "")),
        "geometry": [[stations[node]["lat"], stations[node]["lng"]] for node in path],
    }


def _road_leg(raw: dict, start: dict, end: dict) -> dict:
    vehicle, name, color = VEHICLES.get(raw["mode"], VEHICLES["bus"])
    return {
        "type": "transit", "vehicle": vehicle, "vehicle_name": name, "line": clean_text(raw["line"], 60) or name,
        "line_name": clean_text(raw["line"], 120), "color": color, "text_color": "#ffffff", "headsign": "",
        "from_stop": clean_text(raw["from_stop"], 120), "to_stop": clean_text(raw["to_stop"], 120), "stops": 0,
        "distance_m": round(rail.distance_m(start, end) * WALK_DETOUR),
        "duration_ms": max(1, int(raw.get("minutes") or 0)) * 60_000,
        "fare": float(raw["fare_php"]) if isinstance(raw.get("fare_php"), (int, float)) and raw["fare_php"] >= 0 else None,
        "note": clean_text(raw.get("instructions", "")),
        "geometry": [[start["lat"], start["lng"]], [end["lat"], end["lng"]]],
    }


def _short_label(legs: list[dict]) -> str:
    names = []
    for leg in legs:
        if leg["type"] != "transit":
            continue
        name = leg["line"] if leg["vehicle"] == "train" else leg["vehicle_name"]
        if leg["line"].lower().startswith("edsa carousel"):
            name = "EDSA Carousel"
        if not names or names[-1] != name:
            names.append(name)
    return " + ".join(names)


def build_option(raw: dict, origin: dict, destination: dict) -> dict | None:
    """Turn one AI option into a checked Maply route (without clock times)."""
    legs: list[dict] = []
    here = dict(origin)
    rides = [leg for leg in raw.get("legs", []) if leg.get("mode") != "walk"]
    if not rides:
        return None
    for raw_leg in rides:
        if raw_leg.get("mode") in RAIL_MODES:
            leg = _rail_leg(raw_leg)
            if not leg:
                continue  # Could not match the stations; skip rather than guess.
            start = {"lat": leg["geometry"][0][0], "lng": leg["geometry"][0][1]}
        else:
            start = _point(raw_leg.get("from_lat"), raw_leg.get("from_lng")) or here
            end = _point(raw_leg.get("to_lat"), raw_leg.get("to_lng"))
            if not end:
                continue
            leg = _road_leg(raw_leg, start, end)
        walk_m = rail.distance_m(here, start) * WALK_DETOUR
        if walk_m > 60:
            legs.append({"type": "walk", "text": "Walk", "distance_m": round(walk_m), "duration_ms": rail.walk_ms(walk_m),
                         "geometry": [[here["lat"], here["lng"]], [start["lat"], start["lng"]]]})
        legs.append(leg)
        here = {"lat": leg["geometry"][-1][0], "lng": leg["geometry"][-1][1]}
    if not any(leg["type"] == "transit" for leg in legs):
        return None
    final_walk = rail.distance_m(here, destination) * WALK_DETOUR
    if final_walk > rail.MAX_WALK_M * 1.5:
        return None  # The last ride doesn't get close enough to the destination.
    if final_walk > 60:
        legs.append({"type": "walk", "text": "Walk", "distance_m": round(final_walk), "duration_ms": rail.walk_ms(final_walk),
                     "geometry": [[here["lat"], here["lng"]], [destination["lat"], destination["lng"]]]})
    fares = [leg.get("fare") for leg in legs if leg["type"] == "transit"]
    return {
        "mode": "commute", "source": "openai",
        "legs": legs,
        "fare": {"amount": round(sum(fare for fare in fares if fare)), "currency": "PHP", "approximate": True} if any(fares) else None,
        "label": _short_label(legs),
        "notice": clean_text(raw.get("notes", "")),
    }


def _with_times(route: dict, depart_ms: int, sources: list) -> dict:
    """Add clock times from the departure time (cached routes are re-timed per request)."""
    clock = depart_ms
    legs = []
    for leg in route["legs"]:
        leg = dict(leg)
        if leg["type"] == "transit":
            leg["depart_time"] = clock
            leg["arrive_time"] = clock + leg["duration_ms"]
        clock += leg["duration_ms"]
        legs.append(leg)
    rides = [leg for leg in legs if leg["type"] == "transit"]
    return {
        **route, "legs": legs,
        "distance_m": sum(leg["distance_m"] for leg in legs),
        "duration_ms": clock - depart_ms,
        "geometry": [point for leg in legs for point in leg["geometry"]],
        "instructions": [],
        "transfers": max(0, len(rides) - 1),
        "walk_m": sum(leg["distance_m"] for leg in legs if leg["type"] == "walk"),
        "depart_time": rides[0]["depart_time"], "arrive_time": clock,
        "sources": sources,
    }


def _count_today() -> None:
    today = datetime.now(MANILA).date()
    with _lock:
        if _daily["day"] != today:
            _daily.update(day=today, count=0)
        if _daily["count"] >= DAILY_LIMIT:
            raise ServiceError("Maply has reached today's limit for commute searches. Driving, cycling and walking still work.", 429)
        _daily["count"] += 1


def commute_routes(origin: dict, destination: dict, depart_at_ms: int | None = None, now_ms: int | None = None) -> list[dict]:
    """Up to three commute routes (jeepney, bus, UV Express, LRT/MRT), fastest first."""
    depart_ms = depart_at_ms if depart_at_ms is not None else (now_ms or int(datetime.now(timezone.utc).timestamp() * 1000))
    if not OPENAI_KEY:
        return rail.rail_routes(origin, destination, depart_ms)

    key = (round(origin["lat"], 3), round(origin["lng"], 3), round(destination["lat"], 3), round(destination["lng"], 3), _is_peak(depart_ms))
    with _lock:
        cached = _cache.get(key)
    if cached and monotonic() - cached[0] < CACHE_SECONDS:
        plan = cached[1]
    else:
        _count_today()
        answer = _ask_openai(origin, destination, depart_ms)
        options = [option for option in (build_option(raw, origin, destination) for raw in answer.get("options", [])[:3]) if option]
        sources = [{"title": str(source.get("title", ""))[:120], "url": source["url"]} for source in answer.get("sources", [])
                   if str(source.get("url", "")).startswith(("https://", "http://"))][:6]
        plan = {"options": options, "sources": sources}
        with _lock:
            _cache[key] = (monotonic(), plan)

    if not plan["options"]:
        # Nothing usable from the web search; fall back to the trains Maply knows.
        try:
            return rail.rail_routes(origin, destination, depart_ms)
        except ServiceError:
            raise ServiceError("No commute route was found for this trip. Try driving, cycling or walking.", 404)
    routes = [_with_times(option, depart_ms, plan["sources"]) for option in plan["options"]]
    routes.sort(key=lambda route: route["duration_ms"])
    return routes


def clear_cache() -> None:
    with _lock:
        _cache.clear()
        _daily.update(day=None, count=0)
