"""
SNCF API client using the Navitia platform (api.sncf.com).
Coverage: sncf — national French train network (TGV, Intercités, TER, etc.)
"""

from __future__ import annotations

import os
from datetime import datetime
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
        results.append(
            {
                "id": p["id"],
                "name": p["name"],
                "lat": float(coord.get("lat", 0)),
                "lon": float(coord.get("lon", 0)),
            }
        )
    return results


# ---------------------------------------------------------------------------
# Direct connections
# ---------------------------------------------------------------------------


def get_direct_connections(stop_area_id: str, date: Optional[str] = None) -> dict:
    """
    Return all stations reachable on the same route as *stop_area_id*.

    Returns a dict with:
      connections: list of station dicts, each with:
        - id, name, lat, lon : station info
        - lines              : list of line codes serving the connection
      route_paths: list of route path dicts, each with:
        - line_code          : e.g. "TGV", "TER"
        - stops              : ordered list of {id, name, lat, lon}
                               (deduplicated — identical stop sequences are merged)
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
        return {"connections": [], "route_paths": []}

    # 2. For each route fetch the schedule to get the ordered stop list.
    #    connections : {stop_area_id -> station info}
    #    route_paths : {stop_sequence_key -> route path} — deduplicated
    connections: dict[str, dict] = {}
    route_paths: dict[tuple, dict] = {}

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
            _process_route_schedules(schedules, stop_area_id, connections, route_paths)

    return {
        "connections": sorted(connections.values(), key=lambda x: x["name"]),
        "route_paths": list(route_paths.values()),
    }


def _stop_area_id_from_stop_point(stop_point: dict) -> str:
    """Extract stop_area id from a stop_point object."""
    return stop_point.get("stop_area", {}).get("id", "")


def _process_route_schedules(
    schedules: list[dict],
    origin_sa_id: str,
    connections: dict[str, dict],
    route_paths: dict[tuple, dict],
) -> None:
    """
    Parse Navitia route_schedule objects to extract the ordered stop sequence
    and register every stop (upstream and downstream) as a connection.

    A route_schedule contains:
      - table.rows[]: one row per stop_point, in journey order
        Each row has:
          - stop_point: the stop_point object (with stop_area inside)
          - date_times[]: list of departure times for each train trip

    Strategy:
      1. Find the row index where our origin stop_area appears.
      2. Walk all rows (upstream and downstream) to build the full route path.
         Stop the downstream scan if the route doubles back (a stop already seen
         reappears — this happens on loop/return services).
      3. Register every stop on the route in the connections dict so it gets a
         marker on the map.
      4. Deduplicate route paths by their stop-ID sequence.
    """
    for schedule in schedules:
        table = schedule.get("table", {})
        rows = table.get("rows", [])
        line_code = schedule.get("display_informations", {}).get("code", "")

        # Find the origin row index
        origin_idx = None
        for i, row in enumerate(rows):
            if _stop_area_id_from_stop_point(row.get("stop_point", {})) == origin_sa_id:
                origin_idx = i
                break

        if origin_idx is None:
            continue

        # Require at least one active trip at the origin to confirm this
        # schedule is live today (avoids ghost routes with no service).
        origin_row = rows[origin_idx]
        if not any(dt.get("date_time") for dt in origin_row.get("date_times", [])):
            continue

        origin_sp = rows[origin_idx].get("stop_point", {})
        origin_sa = origin_sp.get("stop_area", {})
        origin_coord = origin_sa.get("coord", {})

        # ------------------------------------------------------------------
        # Build the drawable path (upstream stops → origin → downstream stops)
        # and register every stop in connections.
        # ------------------------------------------------------------------

        # seen_ids is seeded with only the origin.  Upstream IDs are NOT
        # included so that legitimate downstream stops whose IDs also appear
        # upstream are not blocked.  A repeat during the downstream scan means
        # the route is doubling back; we break there.
        seen_ids: set[str] = {origin_sa_id}

        stops: list[dict] = []

        # --- Upstream stops (rows before origin, in route order) -----------
        for i in range(origin_idx):
            sp_i = rows[i].get("stop_point", {})
            sa_i = sp_i.get("stop_area", {})
            sa_id_i = sa_i.get("id", "")
            if not sa_id_i:
                continue
            coord_i = sa_i.get("coord", {})
            stop = {
                "id": sa_id_i,
                "name": sa_i.get("name", sp_i.get("name", "")),
                "lat": float(coord_i.get("lat", 0)),
                "lon": float(coord_i.get("lon", 0)),
            }
            stops.append(stop)
            if sa_id_i not in connections:
                connections[sa_id_i] = {
                    **stop,
                    "lines": [line_code] if line_code else [],
                }
            elif line_code and line_code not in connections[sa_id_i]["lines"]:
                connections[sa_id_i]["lines"].append(line_code)

        # --- Origin --------------------------------------------------------
        stops.append(
            {
                "id": origin_sa_id,
                "name": origin_sa.get("name", origin_sp.get("name", "")),
                "lat": float(origin_coord.get("lat", 0)),
                "lon": float(origin_coord.get("lon", 0)),
            }
        )

        # --- Downstream stops ----------------------------------------------
        for j in range(origin_idx + 1, len(rows)):
            dest_row = rows[j]
            dest_sp = dest_row.get("stop_point", {})
            dest_sa = dest_sp.get("stop_area", {})
            dest_sa_id = dest_sa.get("id", "")
            dest_name = dest_sa.get("name", dest_sp.get("name", ""))
            dest_coord = dest_sa.get("coord", {})

            if not dest_sa_id:
                continue
            if dest_sa_id in seen_ids:
                break  # route is doubling back — stop here

            seen_ids.add(dest_sa_id)
            stop = {
                "id": dest_sa_id,
                "name": dest_name,
                "lat": float(dest_coord.get("lat", 0)),
                "lon": float(dest_coord.get("lon", 0)),
            }
            stops.append(stop)
            if dest_sa_id not in connections:
                connections[dest_sa_id] = {
                    **stop,
                    "lines": [line_code] if line_code else [],
                }
            elif line_code and line_code not in connections[dest_sa_id]["lines"]:
                connections[dest_sa_id]["lines"].append(line_code)

        # ------------------------------------------------------------------
        # Register the route path, deduplicated by stop-ID sequence.
        # Stops with no valid coordinates (0, 0) are filtered out.
        # ------------------------------------------------------------------
        path_stops_valid = [s for s in stops if s["lat"] != 0 or s["lon"] != 0]
        if len(path_stops_valid) < 2:
            continue

        seq_key = tuple(s["id"] for s in path_stops_valid)
        if seq_key not in route_paths:
            route_paths[seq_key] = {
                "line_code": line_code,
                "stops": path_stops_valid,
            }
