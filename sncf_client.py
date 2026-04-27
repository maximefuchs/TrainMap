"""
SNCF API client using the Navitia platform (api.sncf.com).
Coverage: sncf — national French train network (TGV, Intercités, TER, etc.)
"""

from __future__ import annotations

import os
import time
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


class _LoggingClient:
    """Thin wrapper around httpx.Client that logs every GET request."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def get(self, url: str, **kwargs) -> httpx.Response:
        params = kwargs.get("params", {})
        param_str = " ".join(f"{k}={v}" for k, v in params.items()) if params else ""
        label = url.replace(BASE_URL, "")
        t0 = time.monotonic()
        response = self._client.get(url, **kwargs)
        elapsed = time.monotonic() - t0
        print(
            f"[SNCF] GET {label}"
            + (f"  {param_str}" if param_str else "")
            + f" -> {response.status_code} ({elapsed:.2f}s)"
        )
        return response

    def __enter__(self):
        self._client = self._client.__enter__()
        return self

    def __exit__(self, *args):
        return self._client.__exit__(*args)


def _client() -> _LoggingClient:
    return _LoggingClient(httpx.Client(auth=(TOKEN, ""), timeout=30))


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


def get_direct_connections(
    stop_area_id: str,
    date: Optional[str] = None,
    progress_callback=None,
) -> dict:
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

    *date* is optional. When omitted the function queries from the current
    datetime for the next 24 hours so results are fresh regardless of time of
    day. Pass an explicit ``YYYYMMDD`` string to query a specific day (e.g.
    from the UI date picker).
    """
    if date is None:
        from_datetime = datetime.now().strftime("%Y%m%dT%H%M%S")
    else:
        from_datetime = f"{date}T000000"

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

    total_routes = len(train_route_ids)
    if progress_callback:
        progress_callback(0, total_routes, "Fetching route schedules…")

    # 2. For each route fetch the schedule to get the ordered stop list.
    #    connections : {stop_area_id -> station info}
    #    route_paths : {stop_sequence_key -> route path} — deduplicated
    connections: dict[str, dict] = {}
    route_paths: dict[tuple, dict] = {}

    with _client() as client:
        for idx, route_id in enumerate(train_route_ids):
            params = {
                "from_datetime": from_datetime,
                "duration": 86400,  # 1 day for the selected date
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

            if progress_callback:
                progress_callback(
                    idx + 1, total_routes, f"Processed {idx + 1}/{total_routes} routes…"
                )

    return {
        "connections": sorted(connections.values(), key=lambda x: x["name"]),
        "route_paths": list(route_paths.values()),
    }


def _stop_area_id_from_stop_point(stop_point: dict) -> str:
    """Extract stop_area id from a stop_point object."""
    return stop_point.get("stop_area", {}).get("id", "")


def _parse_dt_to_seconds(dt_str: str) -> Optional[int]:
    """
    Parse a Navitia datetime string to seconds since midnight.

    Accepts 'YYYYMMDDTHHmmss' or bare 'HHmmss'.  Returns None if the string
    is missing or malformed.  Note: values ≥ 86400 are valid in Navitia for
    trips that run past midnight (e.g. '251500' = 01:15 next day = 90900 s).
    """
    if not dt_str:
        return None
    part = dt_str.split("T")[1] if "T" in dt_str else dt_str
    if len(part) < 6:
        return None
    try:
        return int(part[:2]) * 3600 + int(part[2:4]) * 60 + int(part[4:6])
    except (ValueError, IndexError):
        return None


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

        # Find the origin row index (needed only to confirm the schedule is live)
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

        # ------------------------------------------------------------------
        # Pick the representative trip column: the one that serves the most
        # stops across the *entire* route (not just downstream), so we get
        # the most complete picture of the line.
        # ------------------------------------------------------------------
        all_dts = rows[origin_idx].get("date_times", [])
        n_trips = len(all_dts)
        trip_stop_count = [0] * n_trips
        for row in rows:
            for k, dt in enumerate(row.get("date_times", [])):
                if k < n_trips and dt.get("date_time", ""):
                    trip_stop_count[k] += 1
        rep_col = trip_stop_count.index(max(trip_stop_count)) if n_trips > 0 else 0

        # ------------------------------------------------------------------
        # Collect (departure_seconds, row_index) for every row the rep_col
        # trip actually serves, then sort by time to get the correct stop
        # order — Navitia row order alone is not always reliable.
        # ------------------------------------------------------------------
        row_times: list[tuple[int, int]] = []
        for i, row in enumerate(rows):
            dts = row.get("date_times", [])
            if rep_col < len(dts):
                t = _parse_dt_to_seconds(dts[rep_col].get("date_time", ""))
                if t is not None:
                    row_times.append((t, i))

        if not row_times:
            continue

        # Correct for past-midnight wraparound: the first decrease in time
        # signals that subsequent stops are on the next calendar day.
        for j in range(1, len(row_times)):
            if row_times[j][0] < row_times[j - 1][0]:
                for k in range(j, len(row_times)):
                    row_times[k] = (row_times[k][0] + 86400, row_times[k][1])
                break  # at most one midnight crossing per trip

        row_times.sort(key=lambda x: x[0])

        # ------------------------------------------------------------------
        # Build the ordered stop list and register every stop in connections.
        # Duplicates (same stop_area_id appearing twice in a route) are
        # skipped after the first occurrence.
        # ------------------------------------------------------------------
        seen_ids: set[str] = set()
        stops: list[dict] = []

        for _, i in row_times:
            row = rows[i]
            sp = row.get("stop_point", {})
            sa = sp.get("stop_area", {})
            sa_id = sa.get("id", "")
            if not sa_id or sa_id in seen_ids:
                continue
            seen_ids.add(sa_id)

            coord = sa.get("coord", {})
            stop = {
                "id": sa_id,
                "name": sa.get("name", sp.get("name", "")),
                "lat": float(coord.get("lat", 0)),
                "lon": float(coord.get("lon", 0)),
            }
            stops.append(stop)

            if sa_id != origin_sa_id:
                if sa_id not in connections:
                    connections[sa_id] = {
                        **stop,
                        "lines": [line_code] if line_code else [],
                    }
                elif line_code and line_code not in connections[sa_id]["lines"]:
                    connections[sa_id]["lines"].append(line_code)

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
