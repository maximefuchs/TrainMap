"""
Trenitalia client using the unofficial ViaggiaTreno API.

Base URL: http://www.viaggiatreno.it/infomobilita/resteasy/viaggiatreno/

No authentication token required.

Key endpoints used:
  cercaStazione/<query>              — JSON station search
  partenze/<station_id>/<date_str>   — departing trains (next ~3h window)
  andamentoTreno/<orig>/<num>/<ts>   — full stop list for a train

Station coordinates come from a bundled CSV (trenitalia_stations.csv) based on
a 2015 dump; missing entries are fetched live from dettaglioStazione/<id>/<reg>.
"""

from __future__ import annotations

import csv
import json
import os
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Station coordinate lookup table (loaded once at import time)
# ---------------------------------------------------------------------------

_CSV_PATH = Path(__file__).parent / "data" / "trenitalia_stations.csv"

# Maps station id (e.g. "S08409") → {"name": str, "lat": float, "lon": float}
_STATION_COORDS: dict[str, dict] = {}

def _load_station_coords() -> None:
    if not _CSV_PATH.exists():
        return
    with _CSV_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("lat") and row["lat"] != "N/A":
                try:
                    _STATION_COORDS[row["id"]] = {
                        "name": row["name"].title(),
                        "lat": float(row["lat"]),
                        "lon": float(row["lon"]),
                    }
                except (ValueError, KeyError):
                    pass

_load_station_coords()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "http://www.viaggiatreno.it/infomobilita/resteasy/viaggiatreno"

# Train categories considered "intercity / national" — we include REG too
# because many Italian routes (e.g. Rome–Nettuno) are regional-only.
TRAIN_CATEGORIES = {"FR", "FA", "FB", "IC", "ICN", "EC", "EN", "REG", "RV"}

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(path: str, timeout: int = 15) -> httpx.Response:
    url = f"{BASE_URL}/{path}"
    t0 = time.monotonic()
    response = httpx.get(url, timeout=timeout)
    elapsed = time.monotonic() - t0
    print(f"[ViaggiaTreno] GET /{path} -> {response.status_code} ({elapsed:.2f}s)")
    return response


def _date_str(date: Optional[str] = None) -> str:
    """
    Return the date string ViaggiaTreno expects for the *partenze* endpoint.
    Format: "Mon Jan 01 2024 10:30:00 GMT+0100"

    ViaggiaTreno's partenze endpoint returns trains departing in the ~3h window
    *after* the given timestamp.  So we must not use midnight (00:00) when the
    caller supplies a date — that window (00:00–03:00) will already have passed
    by the time any user runs a search.

    Strategy:
      - No date given → use now (best for live departures).
      - Date given and it matches today → use now (same as above).
      - Date given for a future day → use 06:00 local time on that day so we
        catch the main morning departure window.
      - Date given for a past day → use 06:00 on that day (historical lookup).
    """
    now = datetime.now()
    if date:
        day = datetime.strptime(date, "%Y%m%d").date()
        if day == now.date():
            dt = now          # today — use current time
        else:
            dt = datetime(day.year, day.month, day.day, 6, 0, 0)
    else:
        dt = now
    return dt.strftime("%a %b %d %Y %H:%M:%S GMT+0100")


# ---------------------------------------------------------------------------
# Station search
# ---------------------------------------------------------------------------


def search_stations(query: str, count: int = 10) -> list[dict]:
    """
    Return up to *count* stations whose name starts with *query*.
    Each item: {id, name, lat, lon}
    """
    if not query.strip():
        return []

    encoded = urllib.parse.quote(query.upper())
    r = _get(f"cercaStazione/{encoded}")
    r.raise_for_status()

    results = []
    for item in r.json():
        sid = item.get("id", "")
        name = item.get("nomeBreve") or item.get("nomeLungo") or sid
        name = name.title()

        coords = _STATION_COORDS.get(sid)
        if coords is None:
            # Try a live lookup for stations not in the CSV
            coords = _fetch_station_coords(sid)

        if coords:
            results.append({
                "id": sid,
                "name": name,
                "lat": coords["lat"],
                "lon": coords["lon"],
            })

        if len(results) >= count:
            break

    return results


# ---------------------------------------------------------------------------
# Station coordinates (live fallback)
# ---------------------------------------------------------------------------


def _fetch_station_coords(station_id: str) -> Optional[dict]:
    """
    Fetch station coordinates from the ViaggiaTreno API when the CSV doesn't
    have them.  Requires two calls: regione/<id> then dettaglioStazione/<id>/<reg>.
    Returns {"lat": float, "lon": float} or None on failure.
    """
    try:
        r = _get(f"regione/{station_id}", timeout=5)
        if r.status_code != 200 or not r.text.strip():
            return None
        region_code = r.text.strip()

        r2 = _get(f"dettaglioStazione/{station_id}/{region_code}", timeout=5)
        if r2.status_code != 200:
            return None
        data = r2.json()
        lat = data.get("lat")
        lon = data.get("lon")
        if lat and lon and (lat != 0 or lon != 0):
            result = {"lat": float(lat), "lon": float(lon)}
            # Cache it so we don't hit the API again for the same station
            name = data.get("nomeCitta") or station_id
            _STATION_COORDS[station_id] = {**result, "name": name}
            return result
    except Exception as e:
        print(f"[ViaggiaTreno] coords lookup failed for {station_id}: {e}")
    return None


# ---------------------------------------------------------------------------
# Train stop resolution
# ---------------------------------------------------------------------------

# Cache: train_num (int) → {"orig_id": str, "ts": str}  (today-anchored)
_TRAIN_ANCHOR_CACHE: dict[int, dict] = {}


def _fetch_train_anchor(train_num: int) -> Optional[dict]:
    """
    Return {"orig_id": str, "ts": str} for *train_num* using today's schedule.

    ViaggiaTreno's andamentoTreno endpoint only works with a today-anchored
    timestamp — even for trains queried on a future date the route stops are
    identical, so we resolve stops via today's anchor.
    """
    if train_num in _TRAIN_ANCHOR_CACHE:
        return _TRAIN_ANCHOR_CACHE[train_num]
    try:
        r = _get(f"cercaNumeroTrenoTrenoAutocomplete/{train_num}", timeout=5)
        if r.status_code != 200 or not r.text.strip():
            return None
        # Format: "9628 - NAPOLI CENTRALE - 04/05/26|9628-S09218-1777845600000\n"
        # There may be multiple lines (train runs from several origins); take first.
        line = r.text.strip().splitlines()[0]
        after_pipe = line.split("|")[1]          # "9628-S09218-1777845600000"
        parts = after_pipe.split("-")
        # parts: [train_num, station_id_part1, (station_id_part2 if S-prefixed), ts]
        # Station IDs can contain hyphens? No — they are like S09218. Safe to split on -
        # Last element is the timestamp, second-to-last is the station id segment after S
        # Actually: "9628-S09218-1777845600000" → ["9628", "S09218", "1777845600000"]
        ts = parts[-1]
        orig_id = parts[-2]  # e.g. "S09218" — but split on "-" breaks "S09218" correctly
        result = {"orig_id": orig_id, "ts": ts}
        _TRAIN_ANCHOR_CACHE[train_num] = result
        return result
    except Exception as e:
        print(f"[ViaggiaTreno] anchor lookup failed for {train_num}: {e}")
        return None


def _fetch_train_stops(train_num: int, orig_id: str, ts) -> Optional[dict]:
    """
    Return the andamentoTreno JSON for *train_num*, falling back to the
    today-anchored orig/ts when the direct call returns 204 (future date).
    """
    # First try with the supplied orig_id / ts (works for today's trains)
    r = _get(f"andamentoTreno/{orig_id}/{train_num}/{ts}")
    if r.status_code == 200 and r.content:
        return r.json()

    # 204 or empty → train is future/not-yet-running; use today's anchor
    anchor = _fetch_train_anchor(train_num)
    if anchor is None:
        return None

    r2 = _get(f"andamentoTreno/{anchor['orig_id']}/{train_num}/{anchor['ts']}")
    if r2.status_code == 200 and r2.content:
        return r2.json()

    return None


# ---------------------------------------------------------------------------
# Direct connections
# ---------------------------------------------------------------------------


def get_direct_connections(
    station_id: str,
    date: Optional[str] = None,
    progress_callback=None,
) -> dict:
    """
    Return all stations reachable by a direct train from *station_id*.

    Returns:
      {
        connections: [ {id, name, lat, lon, lines: [category, ...]}, ... ]
        route_paths: [ {line_code: str, stops: [{id, name, lat, lon}, ...] }, ... ]
      }
    """
    date_str = _date_str(date)
    encoded_date = urllib.parse.quote(date_str)

    # 1. Fetch departures from this station
    r = _get(f"partenze/{station_id}/{encoded_date}")
    r.raise_for_status()
    departures = r.json()

    # Filter to train categories we care about
    trains = [
        t for t in departures
        if t.get("categoriaDescrizione", "").strip() in TRAIN_CATEGORIES
    ]

    if not trains:
        return {"connections": [], "route_paths": []}

    total = len(trains)
    if progress_callback:
        progress_callback(0, total, "Fetching train stops…")

    connections: dict[str, dict] = {}
    route_paths: dict[tuple, dict] = {}

    # Resolve origin station coords
    origin_coords = _STATION_COORDS.get(station_id) or _fetch_station_coords(station_id)

    for idx, train in enumerate(trains):
        train_num = train.get("numeroTreno")
        orig_id = train.get("codOrigine", station_id)
        ts = train.get("dataPartenzaTreno")
        category = train.get("categoriaDescrizione", "").strip()
        comp_num = train.get("compNumeroTreno", "").strip()
        line_code = comp_num or category

        if not train_num or not ts:
            continue

        try:
            train_data = _fetch_train_stops(train_num, orig_id, ts)
            if train_data is None:
                continue
        except Exception as e:
            print(f"[ViaggiaTreno] stop fetch failed for {train_num}: {e}")
            continue

        stops_raw = train_data.get("fermate", [])
        if not stops_raw:
            continue

        # Build ordered stop list with coordinates
        stops: list[dict] = []
        seen_ids: set[str] = set()

        for stop in stops_raw:
            sid = stop.get("id", "")
            if not sid or sid in seen_ids:
                continue
            seen_ids.add(sid)

            name = (stop.get("stazione") or sid).title()
            coords = _STATION_COORDS.get(sid) or _fetch_station_coords(sid)
            if not coords:
                continue  # skip stops with no known location

            stop_dict = {
                "id": sid,
                "name": name,
                "lat": coords["lat"],
                "lon": coords["lon"],
            }
            stops.append(stop_dict)

            # Every stop that isn't the origin is a connection
            if sid != station_id:
                if sid not in connections:
                    connections[sid] = {**stop_dict, "lines": [line_code]}
                elif line_code not in connections[sid]["lines"]:
                    connections[sid]["lines"].append(line_code)

        # Register route path, deduplicated by stop-ID sequence
        if len(stops) >= 2:
            seq_key = tuple(s["id"] for s in stops)
            if seq_key not in route_paths:
                route_paths[seq_key] = {
                    "line_code": line_code,
                    "stops": stops,
                }

        if progress_callback:
            progress_callback(idx + 1, total, f"Processed {idx + 1}/{total} trains…")

    return {
        "connections": sorted(connections.values(), key=lambda x: x["name"]),
        "route_paths": list(route_paths.values()),
    }
