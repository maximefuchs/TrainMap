"""
FlixBus client for the Train Map application.

Uses the FlixBus public search API (no auth token required).

Key characteristics:
  - Works at CITY level (not station level) — FlixBus searches are city-to-city.
  - No "all destinations from X" endpoint exists without auth, so we enumerate
    all cities for the country then query each as a destination in parallel.
  - Intermediate stops are resolved from a bundled GTFS-derived lookup file
    (flixbus_stops.json). The lookup maps (dep_station_id, arr_station_id,
    dep_time_hhmm) -> ordered list of stops with coordinates.
  - Results are cached in memory per session to avoid redundant API calls.
"""

from __future__ import annotations

import gzip
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as date_type, datetime
from typing import Callable, Optional

import httpx

# ---------------------------------------------------------------------------
# FlixBus API endpoints
# ---------------------------------------------------------------------------

AUTOCOMPLETE_CITIES_URL = (
    "https://global.api.flixbus.com/search/autocomplete/cities"
    "?q={q}&lang=en&flixbus_cities_only=true"
)
SEARCH_URL = (
    "https://global.api.flixbus.com/search/service/v4/search"
    "?from_city_id={from_id}&to_city_id={to_id}"
    "&departure_date={date}&pax=1&currency=EUR&locale=en_GB"
    "&search_by=cities&products=%7B%22adult%22%3A1%7D"
)

# Single-letter queries (a–z) for city enumeration — covers all major FlixBus
# cities (~200+) across Europe with a ~2 s cold-start build time.
_ALPHA = "abcdefghijklmnopqrstuvwxyz"
_ENUM_QUERIES = list(_ALPHA)

# In-memory city cache per country: {country_code: {city_id: city_dict}}
_city_cache: dict[str, dict[str, dict]] = {}
_cache_lock = threading.Lock()

# Countries that have FlixBus service (used for enumerating destinations)
# We use the ISO 3166-1 alpha-2 code as used by the FlixBus API's `country` field.
COUNTRY_CODES = {
    "fr": "fr",
    "it": "it",
}

# Timeout for individual HTTP requests (seconds)
_TIMEOUT = 10

# ---------------------------------------------------------------------------
# GTFS stop lookup
# ---------------------------------------------------------------------------
# flixbus_stops.json.gz is generated from the FlixBus GTFS feed (MobilityData
# catalog entry de-unknown-flixbus-gtfs-853, updated ~weekly).
#
# Structure: dep_station_id -> arr_station_id -> [{t: "HH:MM", s: [[id,name,lat,lon],...]}]
#
# The lookup is keyed by (any intermediate stop, last stop of the trip) so that
# boarding mid-route is handled correctly — e.g. a bus from Milan stopping in
# Strasbourg on its way to Eindhoven is keyed as Strasbourg -> Eindhoven even
# though Strasbourg is not the trip origin.
#
# arr_station_id is always the last stop of the GTFS trip, which matches the
# station_id returned by the FlixBus search API for the arrival city.
#
# The file is loaded once at module import time (2.5 MB gzipped, ~instant).

_GTFS_PATH = os.path.join(os.path.dirname(__file__), "flixbus_stops.json.gz")

def _load_gtfs_stops() -> dict:
    try:
        with gzip.open(_GTFS_PATH, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

_gtfs_stops: dict = _load_gtfs_stops()


def _lookup_stops(dep_station_id: str, arr_station_id: str, dep_time_hhmm: str) -> list[dict] | None:
    """
    Return the ordered stop list (dep → arr, inclusive) for a leg, or None.

    dep_time_hhmm: "HH:MM" extracted from the ISO departure timestamp.
    Each returned stop: {"id", "name", "lat", "lon"}.

    When only one trip exists for the pair it is returned regardless of time
    (schedules shift between the static GTFS snapshot and the live API).
    When multiple trips exist the one with the closest departure time is used.
    """
    trips = _gtfs_stops.get(dep_station_id, {}).get(arr_station_id, [])
    if not trips:
        return None
    if len(trips) == 1:
        best = trips[0]
    else:
        def norm(t: str) -> int:
            h, m = t.split(":")
            return (int(h) % 24) * 60 + int(m)
        target = norm(dep_time_hhmm)
        best = min(trips, key=lambda e: abs(norm(e["t"]) - target))
    return [{"id": s[0], "name": s[1], "lat": s[2], "lon": s[3]} for s in best["s"]]


# ---------------------------------------------------------------------------
# City search (autocomplete — shown to the user in the search input)
# ---------------------------------------------------------------------------

def search_cities(q: str, country: str = "fr") -> list[dict]:
    """
    Return a list of cities matching *q* for *country*.

    Each item: {"id": str, "name": str, "lat": float, "lon": float}
    """
    cc = COUNTRY_CODES.get(country, country)
    try:
        resp = httpx.get(
            AUTOCOMPLETE_CITIES_URL.format(q=httpx.URL("", params={"q": q}).params["q"]),
            params={"q": q, "lang": "en", "flixbus_cities_only": "true", "country": cc},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        cities = resp.json()
    except Exception:
        return []

    result = []
    for c in cities:
        loc = c.get("location") or {}
        lat = loc.get("lat")
        lon = loc.get("lon")
        if lat is None or lon is None:
            continue
        result.append({
            "id":   c["id"],
            "name": c["name"],
            "lat":  float(lat),
            "lon":  float(lon),
        })
    return result


# ---------------------------------------------------------------------------
# City enumeration (internal — builds the destination pool)
# ---------------------------------------------------------------------------

def _fetch_cities_for_query(q: str) -> list[dict]:
    """Fetch all FlixBus cities whose name starts with *q* across all of Europe."""
    try:
        resp = httpx.get(
            "https://global.api.flixbus.com/search/autocomplete/cities",
            params={
                "q": q,
                "lang": "en",
                "flixbus_cities_only": "true",
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


def _build_city_cache(country: str) -> dict[str, dict]:
    """
    Build (or return cached) mapping city_id → city_dict.

    Destinations are all FlixBus cities in Europe (no country filter),
    so bus searches from any origin show the full range of reachable cities.
    The cache is shared across all countries and keyed as "europe".
    """
    cache_key = "europe"
    with _cache_lock:
        if cache_key in _city_cache:
            return _city_cache[cache_key]

    cities: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_fetch_cities_for_query, q): q for q in _ENUM_QUERIES}
        for fut in as_completed(futs):
            for c in fut.result():
                cid = c.get("id")
                if not cid:
                    continue
                loc = c.get("location") or {}
                lat = loc.get("lat")
                lon = loc.get("lon")
                if lat is None or lon is None:
                    continue
                cities[cid] = {
                    "id":   cid,
                    "name": c["name"],
                    "lat":  float(lat),
                    "lon":  float(lon),
                }

    with _cache_lock:
        _city_cache[cache_key] = cities
    return cities


# ---------------------------------------------------------------------------
# Direct connection lookup
# ---------------------------------------------------------------------------

def _date_param(date: Optional[str]) -> str:
    """Convert YYYYMMDD → DD.MM.YYYY expected by FlixBus API.  Defaults to today."""
    if date:
        try:
            d = datetime.strptime(date, "%Y%m%d").date()
            return d.strftime("%d.%m.%Y")
        except ValueError:
            pass
    return date_type.today().strftime("%d.%m.%Y")


def _check_connection(
    from_id: str,
    to_city: dict,
    date_str: str,
) -> Optional[list]:
    """
    Query the FlixBus search API for a single from→to pair.

    Returns a list of trip dicts for every direct trip found (not just the first),
    so multiple buses departing at different times are all captured.
    Each item: {
      "city", "departure_time", "arrival_time", "dep_iso", "arr_iso",
      "legs",              # raw legs list (city_id level)
      "dep_station_id",   # station_id of the first leg's departure
      "arr_station_id",   # station_id of the last leg's arrival
    }
    """
    to_id = to_city["id"]
    if to_id == from_id:
        return None
    try:
        resp = httpx.get(
            "https://global.api.flixbus.com/search/service/v4/search",
            params={
                "from_city_id": from_id,
                "to_city_id":   to_id,
                "departure_date": date_str,
                "pax":            "1",
                "currency":       "EUR",
                "locale":         "en_GB",
                "search_by":      "cities",
                "products":       '{"adult":1}',
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:
        return None

    trips = data.get("trips", [])
    if not trips:
        return None

    results = trips[0].get("results", {})
    found = []
    for uid, r in results.items():
        if r.get("transfer_type_key") != "direct":
            continue
        legs = r.get("legs", [])
        if not legs:
            continue
        dep_iso = legs[0].get("departure", {}).get("date", "")
        arr_iso = legs[-1].get("arrival", {}).get("date", "")
        dep_time = dep_iso[11:16] if len(dep_iso) >= 16 else dep_iso
        arr_time = arr_iso[11:16] if len(arr_iso) >= 16 else arr_iso
        dep_station_id = legs[0].get("departure", {}).get("station_id", "")
        arr_station_id = legs[-1].get("arrival", {}).get("station_id", "")
        found.append({
            "city":           to_city,
            "departure_time": dep_time,
            "arrival_time":   arr_time,
            "dep_iso":        dep_iso,
            "arr_iso":        arr_iso,
            "legs":           legs,
            "dep_station_id": dep_station_id,
            "arr_station_id": arr_station_id,
        })
    return found if found else None


def get_direct_connections(
    city_id: str,
    date: Optional[str] = None,
    country: str = "fr",
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """
    Return all cities directly reachable by FlixBus from *city_id* on *date*.

    Each connection includes all direct trips (departure/arrival times) for that
    city on the given date.

    Route paths are built from the legs[] returned by the search API.
    Each leg carries the departure and arrival city_id; we resolve coordinates
    from the city cache. This gives polyline data for bus routes at city level
    (no intra-city intermediate stops — those require auth).

    Return format:
      {
        "connections": [
          {
            "id": city_id, "name": city_name, "lat": float, "lon": float,
            "lines": [
              {"code": "FlixBus", "departure_time": "HH:MM", "arrival_time": "HH:MM"},
              ...  # one entry per direct trip on that day
            ],
          }, ...
        ],
        "route_paths": [
          {
            "line_code": "FlixBus HH:MM",
            "stops": [{"id", "name", "lat", "lon"}, ...],
          }, ...
        ],
      }
    """
    date_str = _date_param(date)

    # 1. Build destination pool
    all_cities = _build_city_cache(country)
    destinations = [c for cid, c in all_cities.items() if cid != city_id]
    total = len(destinations)

    # city_id → list of trip dicts (deduped)
    city_trips: dict[str, list[dict]] = {}
    city_meta:  dict[str, dict] = {}
    # (dep_iso, arr_station_id) → trip dict (first seen, for route path building)
    # Keyed by both departure time AND arrival station so that buses serving
    # different destinations at the same departure time (e.g. Frankfurt city
    # centre vs Frankfurt Airport) each get their own route_path entry.
    trips_by_dep: dict[tuple, dict] = {}
    done = 0

    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {
            ex.submit(_check_connection, city_id, city, date_str): city
            for city in destinations
        }
        for fut in as_completed(futs):
            done += 1
            city = futs[fut]
            if progress_callback:
                progress_callback(done, total, city["name"])

            result = fut.result()
            if result is None:
                continue

            cid = city["id"]
            city_meta[cid] = city
            seen = set()
            for trip in result:
                key = (trip["dep_iso"], trip["arr_iso"])
                if key not in seen:
                    seen.add(key)
                    city_trips.setdefault(cid, []).append(trip)
                # Store trip keyed by (dep_iso, arr_station_id) for route_path building
                path_key = (trip["dep_iso"], trip.get("arr_station_id", ""))
                if path_key not in trips_by_dep:
                    trips_by_dep[path_key] = trip

    # 2. Build connections list
    connections = []
    for cid, trips in city_trips.items():
        city = city_meta[cid]
        trips_sorted = sorted(trips, key=lambda t: t["dep_iso"])
        connections.append({
            "id":   city["id"],
            "name": city["name"],
            "lat":  city["lat"],
            "lon":  city["lon"],
            "lines": [
                {
                    "code":           "FlixBus",
                    "departure_time": t["departure_time"],
                    "arrival_time":   t["arrival_time"],
                }
                for t in trips_sorted
            ],
        })

    # 3. Build route_paths.
    #    Priority: GTFS lookup (full intermediate stops) → city-level legs fallback.
    #    Key: (dep_station_id, arr_station_id, dep_time_hhmm) for GTFS match.
    route_paths = []
    seen_paths: set[tuple] = set()
    origin_city = all_cities.get(city_id)

    for (dep_iso, _arr_station), trip in sorted(trips_by_dep.items()):
        legs = trip.get("legs", [])
        if not legs:
            continue

        dep_time = dep_iso[11:16] if len(dep_iso) >= 16 else dep_iso
        dep_station_id = trip.get("dep_station_id", "")
        arr_station_id = trip.get("arr_station_id", "")

        # Try GTFS first — gives full intermediate bus stops with coords
        stops = None
        if dep_station_id and arr_station_id:
            stops = _lookup_stops(dep_station_id, arr_station_id, dep_time)

        # Fallback: city-level stops from legs[] (only major cities, no intermediate)
        if not stops:
            stop_city_ids: list[str] = [legs[0]["departure"]["city_id"]]
            for leg in legs:
                cid_arr = leg["arrival"]["city_id"]
                if cid_arr != stop_city_ids[-1]:
                    stop_city_ids.append(cid_arr)

            stops = []
            for cid in stop_city_ids:
                if cid == city_id and origin_city:
                    stops.append({
                        "id": city_id, "name": origin_city["name"],
                        "lat": origin_city["lat"], "lon": origin_city["lon"],
                    })
                elif cid in all_cities:
                    c = all_cities[cid]
                    stops.append({"id": cid, "name": c["name"], "lat": c["lat"], "lon": c["lon"]})

        if not stops or len(stops) < 2:
            continue

        # Stamp departure time on first stop and arrival time on last stop
        # so the sidebar can show scheduled times next to each city name.
        arr_time = trip.get("arrival_time", "")
        stops[0]["departure_time"] = dep_time
        stops[-1]["arrival_time"]  = arr_time

        # Deduplicate identical stop sequences
        path_key = tuple(s["id"] for s in stops)
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)

        route_paths.append({
            "line_code": f"FlixBus {dep_time}",
            "departure_time": dep_time,
            "arrival_time": trip.get("arrival_time", ""),
            "stops": stops,
        })

    return {
        "connections": connections,
        "route_paths": route_paths,
    }
