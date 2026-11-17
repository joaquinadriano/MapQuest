"""Built-in Metro Manila rail planner (LRT-1, LRT-2, MRT-3).

Used for the Transit mode when no Google Maps key is configured. It needs no
API key and makes no network requests: station positions come from
OpenStreetMap (see scripts/build_rail_data.py), and travel times and fares are
estimates from typical speeds, waits and published fare ranges. It covers
trains only, not jeepneys or buses.
"""

import heapq
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.graphhopper_service import ServiceError

DATA_FILE = Path(__file__).resolve().parent / "data" / "metro_manila_rail.json"
MANILA = timezone(timedelta(hours=8))

WALK_METRES_PER_MIN = 80  # about 4.8 km/h
WALK_DETOUR = 1.3  # streets are longer than a straight line
MAX_WALK_M = 3000  # furthest we suggest walking to or from a station
CANDIDATE_STATIONS = 4  # nearest stations considered at each end
MAX_ROUTES = 3
SLOWER_LIMIT_MS = 12 * 60_000  # alternatives at most 12 minutes slower than the best

# Typical running speed including stops, and the usual wait for a train (half the
# peak headway). Estimates; real service varies.
LINE_PROFILE = {
    "LRT-1": {"speed_kmh": 31, "wait_min": 3, "fare_base": 15, "fare_per_km": 1.05, "fare_max": 45},
    "LRT-2": {"speed_kmh": 35, "wait_min": 4, "fare_base": 15, "fare_per_km": 1.2, "fare_max": 35},
    "MRT-3": {"speed_kmh": 33, "wait_min": 3, "fare_base": 13, "fare_per_km": 0.95, "fare_max": 28},
}
# Walking links between lines: (line, station) <-> (line, station), minutes on foot.
TRANSFERS = [
    (("LRT-1", "EDSA"), ("MRT-3", "Taft Avenue"), 7),
    (("LRT-1", "Doroteo Jose"), ("LRT-2", "Recto"), 6),
    (("LRT-2", "Araneta Center - Cubao"), ("MRT-3", "Cubao"), 9),
]
# Trains usually run from about 4:30 AM to 10:00 PM.
FIRST_TRAIN = (4, 30)
LAST_TRAIN = (22, 0)

_network = None


def distance_m(a: dict, b: dict) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (a["lat"], a["lng"], b["lat"], b["lng"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(h))


def walk_ms(metres: float) -> int:
    return int(metres / WALK_METRES_PER_MIN * 60_000)


def load_network() -> dict:
    """Stations by (line, index) plus ride and transfer links."""
    global _network
    if _network is None:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        stations = {}
        links: dict[tuple, list] = {}
        for line_id, line in data["lines"].items():
            profile = LINE_PROFILE[line_id]
            for index, station in enumerate(line["stations"]):
                stations[(line_id, index)] = {**station, "line": line_id}
                if index:
                    km = station["km"] - line["stations"][index - 1]["km"]
                    minutes = km / profile["speed_kmh"] * 60
                    links.setdefault((line_id, index - 1), []).append(((line_id, index), minutes, "ride"))
                    links.setdefault((line_id, index), []).append(((line_id, index - 1), minutes, "ride"))
        index_of = {(node[0], station["name"]): node for node, station in stations.items()}
        for (line_a, name_a), (line_b, name_b), minutes in TRANSFERS:
            a, b = index_of.get((line_a, name_a)), index_of.get((line_b, name_b))
            if a and b:
                # Changing lines means walking over and waiting for the next train.
                links.setdefault(a, []).append((b, minutes + LINE_PROFILE[line_b]["wait_min"], "transfer"))
                links.setdefault(b, []).append((a, minutes + LINE_PROFILE[line_a]["wait_min"], "transfer"))
        _network = {"lines": data["lines"], "stations": stations, "links": links}
    return _network


def nearest_stations(place: dict, network: dict) -> list[tuple]:
    """The closest few stations within walking distance, as (node, metres)."""
    ranked = sorted(((node, distance_m(place, station)) for node, station in network["stations"].items()), key=lambda item: item[1])
    seen_names = set()
    picked = []
    for node, metres in ranked:
        if metres > MAX_WALK_M / WALK_DETOUR:
            break
        name = network["stations"][node]["name"]
        if (node[0], name) in seen_names:
            continue
        seen_names.add((node[0], name))
        picked.append((node, metres * WALK_DETOUR))
        if len(picked) == CANDIDATE_STATIONS:
            break
    return picked


def rail_path(start: tuple, end: tuple, network: dict) -> tuple[float, list] | None:
    """Shortest ride (minutes) between two stations, with the stations passed."""
    best = {start: 0.0}
    previous: dict[tuple, tuple] = {}
    queue = [(0.0, start)]
    while queue:
        minutes, node = heapq.heappop(queue)
        if node == end:
            path = [end]
            while path[-1] != start:
                path.append(previous[path[-1]])
            return minutes, path[::-1]
        if minutes > best.get(node, math.inf):
            continue
        for neighbour, cost, _kind in network["links"].get(node, []):
            candidate = minutes + cost
            if candidate < best.get(neighbour, math.inf):
                best[neighbour] = candidate
                previous[neighbour] = node
                heapq.heappush(queue, (candidate, neighbour))
    return None


def fare(line_id: str, km: float) -> float:
    profile = LINE_PROFILE[line_id]
    return float(min(profile["fare_max"], round(profile["fare_base"] + profile["fare_per_km"] * km)))


def _rides(path: list, network: dict) -> list[list]:
    """Split a station path into one list of stations per line."""
    rides = [[path[0]]]
    for node in path[1:]:
        if node[0] == rides[-1][-1][0]:
            rides[-1].append(node)
        else:
            rides.append([node])
    return [ride for ride in rides if len(ride) > 1]


def _itinerary(origin: dict, destination: dict, board: tuple, alight: tuple, path: list, network: dict, depart_ms: int) -> dict:
    stations, lines = network["stations"], network["lines"]
    clock = depart_ms
    legs = []

    def walk(start: dict, end: dict, metres: float) -> None:
        nonlocal clock
        duration = walk_ms(metres)
        legs.append({"type": "walk", "text": "Walk", "distance_m": round(metres), "duration_ms": duration,
                     "geometry": [[start["lat"], start["lng"]], [end["lat"], end["lng"]]]})
        clock += duration

    rides = _rides(path, network)
    walk(origin, stations[rides[0][0]], distance_m(origin, stations[rides[0][0]]) * WALK_DETOUR)
    total_fare = 0.0
    for number, ride in enumerate(rides):
        line_id = ride[0][0]
        first, last = stations[ride[0]], stations[ride[-1]]
        if number:
            # Walk between the two lines' stations at a transfer.
            transfer_minutes = next(m for a, b, m in TRANSFERS if {a, b} == {(rides[number - 1][-1][0], stations[rides[number - 1][-1]]["name"]), (line_id, first["name"])})
            legs.append({"type": "walk", "text": "Walk", "distance_m": transfer_minutes * WALK_METRES_PER_MIN, "duration_ms": transfer_minutes * 60_000,
                         "geometry": [[stations[rides[number - 1][-1]]["lat"], stations[rides[number - 1][-1]]["lng"]], [first["lat"], first["lng"]]]})
            clock += transfer_minutes * 60_000
        profile = LINE_PROFILE[line_id]
        km = abs(last["km"] - first["km"])
        line_stations = lines[line_id]["stations"]
        terminal = line_stations[-1] if ride[-1][1] > ride[0][1] else line_stations[0]
        board_time = clock + profile["wait_min"] * 60_000
        ride_ms = int(km / profile["speed_kmh"] * 3_600_000)
        legs.append({
            "type": "transit", "vehicle": "train", "vehicle_name": "Train",
            "line": line_id, "line_name": lines[line_id]["name"], "color": lines[line_id]["color"], "text_color": "#ffffff",
            "headsign": terminal["name"], "from_stop": first["name"], "to_stop": last["name"],
            "depart_time": board_time, "arrive_time": board_time + ride_ms, "stops": len(ride) - 1,
            "distance_m": round(km * 1000), "duration_ms": profile["wait_min"] * 60_000 + ride_ms,
            "geometry": [[stations[node]["lat"], stations[node]["lng"]] for node in ride],
        })
        clock = board_time + ride_ms
        total_fare += fare(line_id, km)
    walk(stations[rides[-1][-1]], destination, distance_m(stations[rides[-1][-1]], destination) * WALK_DETOUR)

    ride_legs = [leg for leg in legs if leg["type"] == "transit"]
    return {
        "mode": "commute",
        "source": "rail-estimate",
        "distance_m": sum(leg["distance_m"] for leg in legs),
        "duration_ms": clock - depart_ms,
        "geometry": [point for leg in legs for point in leg["geometry"]],
        "legs": legs,
        "instructions": [],
        "fare": {"amount": total_fare, "currency": "PHP", "approximate": True},
        "transfers": len(ride_legs) - 1,
        "walk_m": sum(leg["distance_m"] for leg in legs if leg["type"] == "walk"),
        "depart_time": ride_legs[0]["depart_time"],
        "arrive_time": clock,
        "label": " + ".join(dict.fromkeys(leg["line"] for leg in ride_legs)),
        "notice": service_hours_notice(ride_legs[0]["depart_time"], ride_legs[-1]["arrive_time"]),
    }


def service_hours_notice(board_ms: int, arrive_ms: int) -> str:
    board, arrive = (datetime.fromtimestamp(ms / 1000, MANILA) for ms in (board_ms, arrive_ms))
    if (board.hour, board.minute) < FIRST_TRAIN or (arrive.hour, arrive.minute) > LAST_TRAIN or arrive.date() > board.date():
        return "Trains usually run from about 4:30 AM to 10:00 PM, so they may not be running at this time."
    return ""


def rail_routes(origin: dict, destination: dict, depart_at_ms: int | None = None, now_ms: int | None = None) -> list[dict]:
    """Up to three train routes, fastest first. Raises 404 when trains don't help."""
    network = load_network()
    depart_ms = depart_at_ms if depart_at_ms is not None else (now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000))
    boards, alights = nearest_stations(origin, network), nearest_stations(destination, network)
    if not boards or not alights:
        raise ServiceError("No LRT or MRT station is within walking distance of this trip. Try driving, cycling or walking.", 404)

    options = []
    for board, _ in boards:
        for alight, _ in alights:
            if board == alight:
                continue
            found = rail_path(board, alight, network)
            # Skip paths with no train ride, e.g. only the walk between Doroteo Jose and Recto.
            if not found or not _rides(found[1], network):
                continue
            route = _itinerary(origin, destination, board, alight, found[1], network, depart_ms)
            if route["legs"][0]["type"] == "walk" and len([leg for leg in route["legs"] if leg["type"] == "transit"]) == 0:
                continue
            options.append(route)

    walking_only_ms = walk_ms(distance_m(origin, destination) * WALK_DETOUR)
    options = [route for route in options if route["duration_ms"] < walking_only_ms]
    if not options:
        raise ServiceError("Walking is faster than taking the train for this trip.", 404)

    options.sort(key=lambda route: route["duration_ms"])
    chosen, seen, chosen_keys = [], set(), []
    for route in options:
        key = tuple((leg["line"], leg["from_stop"], leg["to_stop"]) for leg in route["legs"] if leg["type"] == "transit")
        lines_used = tuple(dict.fromkeys(leg["line"] for leg in route["legs"] if leg["type"] == "transit"))
        # Skip near-duplicates: same stations, or same lines boarding one stop over.
        if key in seen or (lines_used, key[0][0]) in seen:
            continue
        # Skip an option that is a chosen one plus an extra ride (e.g. one stop to reach a transfer).
        if any(set(previous) < set(key) for previous in chosen_keys):
            continue
        if chosen and route["duration_ms"] - chosen[0]["duration_ms"] > SLOWER_LIMIT_MS:
            break
        seen.update({key, (lines_used, key[0][0])})
        chosen_keys.append(key)
        chosen.append(route)
        if len(chosen) == MAX_ROUTES:
            break
    return chosen
