"""
FlixBus client for the Train Map application.

Uses the FlixBus public search API (no auth token required).

Key characteristics:
  - Works at CITY level (not station level) — FlixBus searches are city-to-city.
  - No "all destinations from X" endpoint exists without auth, so we enumerate
    all cities for the country then query each as a destination in parallel.
  - Intermediate stops require auth (/rides/{id}/stops → 403), so we return
    empty route_paths. The frontend renders destination cities as dots only.
  - Results are cached in memory per session to avoid redundant API calls.
"""

from __future__ import annotations

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

# Characters to query for city enumeration — a–z covers virtually all city names
_ENUM_QUERIES = list("abcdefghijklmnopqrstuvwxyz")

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
) -> Optional[dict]:
    """
    Query the FlixBus search API for a single from→to pair.

    Returns a connection dict if at least one direct trip is found, else None.
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
    # Look for a direct trip
    for uid, r in results.items():
        if r.get("transfer_type_key") == "direct":
            # Grab the earliest departure time for display
            dep_time = ""
            arr_time = ""
            legs = r.get("legs", [])
            if legs:
                dep_dt = legs[0].get("departure", {}).get("date", "")
                arr_dt = legs[-1].get("arrival", {}).get("date", "")
                # ISO-8601 → HH:MM
                if dep_dt:
                    dep_time = dep_dt[11:16] if len(dep_dt) >= 16 else dep_dt
                if arr_dt:
                    arr_time = arr_dt[11:16] if len(arr_dt) >= 16 else arr_dt
            return {
                "city": to_city,
                "departure_time": dep_time,
                "arrival_time":   arr_time,
            }
    return None


def get_direct_connections(
    city_id: str,
    date: Optional[str] = None,
    country: str = "fr",
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """
    Return all cities directly reachable by FlixBus from *city_id* on *date*.

    Return format (same shape as navitia_client / trenitalia_client):
      {
        "connections": [
          {
            "id":    city_id,
            "name":  city_name,
            "lat":   float,
            "lon":   float,
            "lines": [{"code": "FlixBus", "departure_time": "HH:MM",
                       "arrival_time": "HH:MM"}],
          },
          ...
        ],
        "route_paths": [],   # empty — intermediate stops require auth
      }

    *progress_callback(current, total, message)* is called once per destination
    checked so the SSE stream can update the progress bar.
    """
    date_str = _date_param(date)

    # 1. Build destination pool
    all_cities = _build_city_cache(country)
    destinations = [c for cid, c in all_cities.items() if cid != city_id]
    total = len(destinations)

    connections = []
    done = 0

    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {
            ex.submit(_check_connection, city_id, city, date_str): city
            for city in destinations
        }
        for fut in as_completed(futs):
            done += 1
            if progress_callback:
                progress_callback(done, total, futs[fut]["name"])

            result = fut.result()
            if result is None:
                continue

            city = result["city"]
            connections.append({
                "id":   city["id"],
                "name": city["name"],
                "lat":  city["lat"],
                "lon":  city["lon"],
                "lines": [{
                    "code":           "FlixBus",
                    "departure_time": result["departure_time"],
                    "arrival_time":   result["arrival_time"],
                }],
            })

    return {
        "connections": connections,
        "route_paths": [],   # no polylines — dots only on the map
    }
