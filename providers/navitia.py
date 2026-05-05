"""
Multi-country train client.

Supported countries:
  "fr" — French national trains via api.sncf.com/v1/coverage/sncf (requires SNCF_API_TOKEN)
  "it" — Italian national trains via the ViaggiaTreno API (no token required)

Italy is handled by trenitalia_client; this module acts as the dispatcher.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Optional

import httpx
from dotenv import load_dotenv

from providers import trenitalia as trenitalia_client

load_dotenv()

# ---------------------------------------------------------------------------
# Country configuration (France only — Italy goes to trenitalia_client)
# ---------------------------------------------------------------------------

COUNTRY_CONFIG: dict[str, dict] = {
    "fr": {
        "base_url": "https://api.sncf.com/v1/coverage/sncf",
        "token_env": "SNCF_API_TOKEN",
    },
    # "it" is handled by trenitalia_client — no token, no Navitia
}

# Physical modes to consider (intercity / national trains only).
TRAIN_MODES = [
    "physical_mode:LongDistanceTrain",
    "physical_mode:Train",
    "physical_mode:LocalTrain",
    "physical_mode:RapidTransit",
]


def get_base_url(country: str) -> str:
    cfg = COUNTRY_CONFIG.get(country)
    if not cfg:
        if country == "it":
            raise ValueError("Italy uses ViaggiaTreno — call trenitalia_client directly.")
        raise ValueError(f"Unsupported country: {country!r}. Supported: {list(COUNTRY_CONFIG) + ['it']}")
    return cfg["base_url"]


def get_token(country: str) -> str:
    """Return the API token for *country*, or "" if none is needed (Italy)."""
    if country == "it":
        return "not-required"  # ViaggiaTreno needs no token
    cfg = COUNTRY_CONFIG.get(country)
    if not cfg:
        raise ValueError(f"Unsupported country: {country!r}. Supported: {list(COUNTRY_CONFIG) + ['it']}")
    return os.getenv(cfg["token_env"], "")


# ---------------------------------------------------------------------------
# HTTP client (France / Navitia only)
# ---------------------------------------------------------------------------


class _LoggingClient:
    """Thin wrapper around httpx.Client that logs every GET request."""

    def __init__(self, client: httpx.Client, base_url: str) -> None:
        self._client = client
        self._base_url = base_url

    def get(self, url: str, **kwargs) -> httpx.Response:
        params = kwargs.get("params", {})
        param_str = " ".join(f"{k}={v}" for k, v in params.items()) if params else ""
        label = url.replace(self._base_url, "")
        t0 = time.monotonic()
        response = self._client.get(url, **kwargs)
        elapsed = time.monotonic() - t0
        print(
            f"[Navitia] GET {label}"
            + (f"  {param_str}" if param_str else "")
            + f" -> {response.status_code} ({elapsed:.2f}s)"
        )
        return response

    def __enter__(self):
        self._client = self._client.__enter__()
        return self

    def __exit__(self, *args):
        return self._client.__exit__(*args)


def _client(country: str = "fr") -> _LoggingClient:
    token = get_token(country)
    base_url = get_base_url(country)
    return _LoggingClient(httpx.Client(auth=(token, ""), timeout=30), base_url)


# ---------------------------------------------------------------------------
# Station search
# ---------------------------------------------------------------------------


def search_stations(query: str, count: int = 10, country: str = "fr") -> list[dict]:
    """
    Return up to *count* stop_area objects matching *query*.
    Each item: {id, name, lat, lon}
    Dispatches to trenitalia_client for Italy.
    """
    if country == "it":
        return trenitalia_client.search_stations(query, count=count)

    base_url = get_base_url(country)
    params = {
        "q": query,
        "type[]": "stop_area",
        "count": count,
    }
    with _client(country) as client:
        r = client.get(f"{base_url}/places", params=params)
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
    country: str = "fr",
) -> dict:
    """
    Return all stations reachable on a direct train from *stop_area_id*.
    Dispatches to trenitalia_client for Italy.

    Returns:
      {
        connections: [ {id, name, lat, lon, lines: [...]}, ... ]
        route_paths: [ {line_code: str, stops: [{id, name, lat, lon}, ...]}, ... ]
      }
    """
    if country == "it":
        return trenitalia_client.get_direct_connections(
            stop_area_id, date=date, progress_callback=progress_callback
        )

    base_url = get_base_url(country)

    if date is None:
        from_datetime = datetime.now().strftime("%Y%m%dT%H%M%S")
    else:
        from_datetime = f"{date}T000000"

    # 1. Fetch all routes passing through this stop_area
    with _client(country) as client:
        r = client.get(
            f"{base_url}/stop_areas/{stop_area_id}/routes",
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

    connections: dict[str, dict] = {}
    route_paths: dict[tuple, dict] = {}

    with _client(country) as client:
        for idx, route_id in enumerate(train_route_ids):
            params = {
                "from_datetime": from_datetime,
                "duration": 86400,
                "items_per_schedule": 500,
            }
            r = client.get(
                f"{base_url}/routes/{route_id}/route_schedules",
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


# ---------------------------------------------------------------------------
# Internal parsing helpers (France / Navitia)
# ---------------------------------------------------------------------------


def _stop_area_id_from_stop_point(stop_point: dict) -> str:
    return stop_point.get("stop_area", {}).get("id", "")


def _parse_dt_to_seconds(dt_str: str) -> Optional[int]:
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
    for schedule in schedules:
        table = schedule.get("table", {})
        rows = table.get("rows", [])
        line_code = schedule.get("display_informations", {}).get("code", "")

        origin_idx = None
        for i, row in enumerate(rows):
            if _stop_area_id_from_stop_point(row.get("stop_point", {})) == origin_sa_id:
                origin_idx = i
                break

        if origin_idx is None:
            continue

        origin_row = rows[origin_idx]
        if not any(dt.get("date_time") for dt in origin_row.get("date_times", [])):
            continue

        all_dts = rows[origin_idx].get("date_times", [])
        n_trips = len(all_dts)
        trip_stop_count = [0] * n_trips
        for row in rows:
            for k, dt in enumerate(row.get("date_times", [])):
                if k < n_trips and dt.get("date_time", ""):
                    trip_stop_count[k] += 1
        rep_col = trip_stop_count.index(max(trip_stop_count)) if n_trips > 0 else 0

        row_times: list[tuple[int, int]] = []
        for i, row in enumerate(rows):
            dts = row.get("date_times", [])
            if rep_col < len(dts):
                t = _parse_dt_to_seconds(dts[rep_col].get("date_time", ""))
                if t is not None:
                    row_times.append((t, i))

        if not row_times:
            continue

        for j in range(1, len(row_times)):
            if row_times[j][0] < row_times[j - 1][0]:
                for k in range(j, len(row_times)):
                    row_times[k] = (row_times[k][0] + 86400, row_times[k][1])
                break

        row_times.sort(key=lambda x: x[0])

        seen_ids: set[str] = set()
        stops: list[dict] = []
        stop_times_hhmm: list[Optional[str]] = []

        for secs, i in row_times:
            row = rows[i]
            sp = row.get("stop_point", {})
            sa = sp.get("stop_area", {})
            sa_id = sa.get("id", "")
            if not sa_id or sa_id in seen_ids:
                continue
            seen_ids.add(sa_id)

            coord = sa.get("coord", {})
            # Convert seconds-since-midnight back to HH:MM (may exceed 24h for overnight)
            h = (secs // 3600) % 24
            m = (secs % 3600) // 60
            hhmm = f"{h:02d}:{m:02d}"

            stop = {
                "id": sa_id,
                "name": sa.get("name", sp.get("name", "")),
                "lat": float(coord.get("lat", 0)),
                "lon": float(coord.get("lon", 0)),
                "departure_time": hhmm,
            }
            stops.append(stop)
            stop_times_hhmm.append(hhmm)

            if sa_id != origin_sa_id:
                if sa_id not in connections:
                    connections[sa_id] = {
                        "id": sa_id,
                        "name": sa.get("name", sp.get("name", "")),
                        "lat": float(coord.get("lat", 0)),
                        "lon": float(coord.get("lon", 0)),
                        "lines": [line_code] if line_code else [],
                    }
                elif line_code and line_code not in connections[sa_id]["lines"]:
                    connections[sa_id]["lines"].append(line_code)

        path_stops_valid = [s for s in stops if s["lat"] != 0 or s["lon"] != 0]
        if len(path_stops_valid) < 2:
            continue

        seq_key = tuple(s["id"] for s in path_stops_valid)
        if seq_key not in route_paths:
            dep_time = path_stops_valid[0].get("departure_time", "")
            arr_time = path_stops_valid[-1].get("departure_time", "")
            route_paths[seq_key] = {
                "line_code": line_code,
                "departure_time": dep_time,
                "arrival_time": arr_time,
                "stops": path_stops_valid,
            }
