"""
SNCF API client using the Navitia platform (api.sncf.com).
Coverage: sncf — national French train network (TGV, Intercités, TER, etc.)
"""
from __future__ import annotations

import os
from datetime import datetime, time
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.sncf.com/v1/coverage/sncf"
TOKEN = os.getenv("SNCF_API_TOKEN", "")

# Physical modes to consider (intercity / national trains only)
TRAIN_MODES = [
    "physical_mode:LongDistanceTrain",
    "physical_mode:Train",
    "physical_mode:LocalTrain",
    "physical_mode:RapidTransit",
]


def _client() -> httpx.Client:
    return httpx.Client(auth=(TOKEN, ""), timeout=30)


# ---------------------------------------------------------------------------
# Station search / autocomplete
# ---------------------------------------------------------------------------

def search_stations(query: str, count: int = 10) -> list[dict]:
    """
    Return a list of stop_area objects matching *query*.
    Each item has: id, name, coord (lat/lon).
    """
    params = {
        "q": query,
        "type[]": "stop_area",
        "count": count,
    }
    with _client() as client:
        r = client.get(f"{BASE_URL}/places", params=params)
        r.raise_for_status()

    places = r.json().get("places", [])
    results = []
    for p in places:
        sa = p.get("stop_area", {})
        coord = sa.get("coord", {})
        results.append({
            "id": p["id"],
            "name": p["name"],
            "lat": float(coord.get("lat", 0)),
            "lon": float(coord.get("lon", 0)),
        })
    return results


# ---------------------------------------------------------------------------
# Direct connections
# ---------------------------------------------------------------------------

def _parse_navitia_time(t: str) -> Optional[str]:
    """
    Navitia represents times as 'HHMMSS' strings.
    Returns a HH:MM string, handling times past midnight (e.g. '251500' = 01:15 next day).
    """
    if not t or len(t) < 6:
        return None
    h, m = int(t[:2]) % 24, int(t[2:4])
    return f"{h:02d}:{m:02d}"


def get_direct_connections(stop_area_id: str, date: Optional[str] = None) -> list[dict]:
    """
    Return all stations directly reachable (without transfer) from *stop_area_id*.

    Each returned item:
      - id, name, lat, lon      : destination station info
      - duration_min            : fastest travel time in minutes
      - first_departure         : HH:MM of first direct train today
      - last_departure          : HH:MM of last direct train today
      - frequency               : number of direct trains per day
      - lines                   : list of line names / codes serving the connection
    """
    if date is None:
        date = datetime.now().strftime("%Y%m%d")

    # 1. Fetch all routes passing through this stop_area
    with _client() as client:
        r = client.get(
            f"{BASE_URL}/stop_areas/{stop_area_id}/routes",
            params={"count": 500},
        )
        r.raise_for_status()
        routes_data = r.json().get("routes", [])

    # Keep only train routes
    train_route_ids = []
    for route in routes_data:
        line = route.get("line", {})
        physical_modes = line.get("physical_modes", [])
        if any(pm.get("id", "") in TRAIN_MODES for pm in physical_modes):
            train_route_ids.append(route["id"])

    if not train_route_ids:
        return []

    # 2. For each route, fetch route_schedules to get per-stop timetables
    #    We aggregate: {destination_stop_area_id -> connection_info}
    connections: dict[str, dict] = {}

    with _client() as client:
        for route_id in train_route_ids:
            params = {
                "from_datetime": f"{date}T000000",
                "duration": 86400,  # full day
                "items_per_schedule": 500,
            }
            r = client.get(
                f"{BASE_URL}/routes/{route_id}/route_schedules",
                params=params,
            )
            if r.status_code != 200:
                continue

            schedules = r.json().get("route_schedules", [])
            _process_route_schedules(schedules, stop_area_id, connections)

    return sorted(connections.values(), key=lambda x: x["duration_min"])


def _stop_area_id_from_stop_point(stop_point: dict) -> str:
    """Extract stop_area id from a stop_point object."""
    return stop_point.get("stop_area", {}).get("id", "")


def _process_route_schedules(
    schedules: list[dict],
    origin_sa_id: str,
    connections: dict[str, dict],
) -> None:
    """
    Parse Navitia route_schedule objects.

    A route_schedule contains:
      - table.rows[]: one row per stop_point, in journey order
        Each row has:
          - stop_point: the stop_point object (with stop_area inside)
          - date_times[]: list of departure times for each train trip

    Strategy:
      1. Find the row index where our origin stop_area appears.
      2. For every subsequent row (downstream stops), compute travel time as
         min over all trips of (downstream_departure - origin_departure).
      3. Record first_departure, last_departure, frequency from the origin row.
    """
    for schedule in schedules:
        table = schedule.get("table", {})
        rows = table.get("rows", [])
        line = schedule.get("display_informations", {})
        line_code = line.get("code", "")
        line_name = line.get("network", "") + " " + line_code

        # Find the origin row index
        origin_idx = None
        for i, row in enumerate(rows):
            sa_id = _stop_area_id_from_stop_point(row.get("stop_point", {}))
            if sa_id == origin_sa_id:
                origin_idx = i
                break

        if origin_idx is None:
            continue

        origin_row = rows[origin_idx]
        # Collect departure times for origin (list of 'HHMMSS' strings)
        origin_times = [
            dt.get("date_time", "")
            for dt in origin_row.get("date_times", [])
            if dt.get("date_time", "")
        ]
        if not origin_times:
            continue

        # First/last departure from origin (raw Navitia times = 'YYYYMMDDTHHmmss')
        def _extract_time_part(dt_str: str) -> str:
            # Navitia datetime: '20240101T143000' -> '143000'
            if "T" in dt_str:
                return dt_str.split("T")[1]
            return dt_str

        origin_times_hhmm = sorted(
            [_extract_time_part(t) for t in origin_times]
        )
        first_dep = _parse_navitia_time(origin_times_hhmm[0])
        last_dep = _parse_navitia_time(origin_times_hhmm[-1])
        frequency = len(origin_times_hhmm)

        # Downstream stops
        for j in range(origin_idx + 1, len(rows)):
            dest_row = rows[j]
            dest_sp = dest_row.get("stop_point", {})
            dest_sa = dest_sp.get("stop_area", {})
            dest_sa_id = dest_sa.get("id", "")
            dest_name = dest_sa.get("name", dest_sp.get("name", ""))
            dest_coord = dest_sa.get("coord", {})

            if not dest_sa_id or dest_sa_id == origin_sa_id:
                continue

            dest_times = [
                _extract_time_part(dt.get("date_time", ""))
                for dt in dest_row.get("date_times", [])
                if dt.get("date_time", "")
            ]
            if not dest_times:
                continue

            # Compute min travel time across all trips (same column = same trip)
            min_duration = None
            for k, orig_t in enumerate(origin_times_hhmm):
                if k >= len(dest_times):
                    break
                dest_t = dest_times[k]
                try:
                    orig_sec = int(orig_t[:2]) * 3600 + int(orig_t[2:4]) * 60
                    dest_sec = int(dest_t[:2]) * 3600 + int(dest_t[2:4]) * 60
                    diff = dest_sec - orig_sec
                    if diff < 0:
                        diff += 86400  # next day
                    if diff > 0 and (min_duration is None or diff < min_duration):
                        min_duration = diff
                except (ValueError, IndexError):
                    continue

            if min_duration is None:
                continue

            duration_min = min_duration // 60

            if dest_sa_id not in connections:
                connections[dest_sa_id] = {
                    "id": dest_sa_id,
                    "name": dest_name,
                    "lat": float(dest_coord.get("lat", 0)),
                    "lon": float(dest_coord.get("lon", 0)),
                    "duration_min": duration_min,
                    "first_departure": first_dep,
                    "last_departure": last_dep,
                    "frequency": frequency,
                    "lines": [line_code] if line_code else [],
                }
            else:
                existing = connections[dest_sa_id]
                existing["duration_min"] = min(existing["duration_min"], duration_min)
                existing["frequency"] = max(existing["frequency"], frequency)
                if line_code and line_code not in existing["lines"]:
                    existing["lines"].append(line_code)
                # Keep earliest first and latest last departure
                if first_dep and (not existing["first_departure"] or first_dep < existing["first_departure"]):
                    existing["first_departure"] = first_dep
                if last_dep and (not existing["last_departure"] or last_dep > existing["last_departure"]):
                    existing["last_departure"] = last_dep
