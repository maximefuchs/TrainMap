#!/usr/bin/env python3
"""
Rebuild flixbus_stops.json.gz from the latest FlixBus GTFS feed.

Downloads the GTFS zip from the MobilityData public catalog, processes it
into a compact lookup table, and writes flixbus_stops.json.gz to the project root.

Usage:
    uv run python3 scripts/rebuild_flixbus_stops.py

The output file is used by flixbus_client._lookup_stops() to resolve intermediate
bus stop coordinates for polyline rendering on the map.

Lookup structure:
    dep_station_id -> arr_station_id -> [{t: "HH:MM", s: [[id, name, lat, lon], ...]}]

Keyed by (any stop i, last stop of trip) so mid-route boarding is handled correctly.
"""

import csv
import collections
import gzip
import io
import json
import os
import time
import zipfile

import httpx

GTFS_URL = (
    "https://storage.googleapis.com/storage/v1/b/mdb-latest/o/"
    "de-unknown-flixbus-gtfs-853.zip?alt=media"
)
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "flixbus_stops.json.gz")


def main():
    t0 = time.time()

    # 1. Download GTFS zip
    print("Downloading FlixBus GTFS feed...")
    resp = httpx.get(GTFS_URL, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    raw = resp.content
    print(f"  Downloaded {len(raw)/1024/1024:.1f} MB in {time.time()-t0:.1f}s")

    # 2. Extract stops.txt and stop_times.txt from zip in memory
    t1 = time.time()
    zf = zipfile.ZipFile(io.BytesIO(raw))

    stops: dict[str, list] = {}
    with zf.open("stops.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
            if row["stop_lat"] and row["stop_lon"]:
                stops[row["stop_id"]] = [
                    row["stop_id"],
                    row["stop_name"],
                    float(row["stop_lat"]),
                    float(row["stop_lon"]),
                ]
    print(f"  Loaded {len(stops)} stops")

    trip_stops: dict[str, list] = collections.defaultdict(list)
    with zf.open("stop_times.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
            sid = row["stop_id"]
            if sid in stops:
                trip_stops[row["trip_id"]].append(
                    (int(row["stop_sequence"]), sid, row["departure_time"])
                )

    for tid in trip_stops:
        trip_stops[tid].sort()

    print(f"  Loaded {len(trip_stops)} trips in {time.time()-t1:.1f}s")

    # 3. Build lookup: (any stop i) -> (last stop) -> deduped trip slices
    t2 = time.time()
    lookup: dict[str, dict[str, dict]] = collections.defaultdict(
        lambda: collections.defaultdict(dict)
    )

    for tid, seq in trip_stops.items():
        n = len(seq)
        if n < 2:
            continue
        last_sid = seq[-1][1]
        if last_sid not in stops:
            continue
        for i in range(n - 1):
            dep_sid = seq[i][1]
            dep_hhmm = seq[i][2][:5]
            stop_slice = [stops[seq[k][1]] for k in range(i, n)]
            # Deduplicate by full stop-sequence fingerprint
            key = dep_hhmm + "|" + "|".join(s[0] for s in stop_slice)
            if key not in lookup[dep_sid][last_sid]:
                lookup[dep_sid][last_sid][key] = stop_slice

    print(f"  Built lookup in {time.time()-t2:.1f}s")

    # 4. Flatten and write gzipped JSON
    out: dict[str, dict] = {}
    for dep_sid, arr_dict in lookup.items():
        out[dep_sid] = {}
        for arr_sid, entries in arr_dict.items():
            out[dep_sid][arr_sid] = [
                {"t": k.split("|")[0], "s": v} for k, v in entries.items()
            ]

    data = json.dumps(out, separators=(",", ":")).encode("utf-8")
    out_path = os.path.realpath(OUT_PATH)
    with gzip.open(out_path, "wb") as f:
        f.write(data)

    gz_size = os.path.getsize(out_path)
    dep_count = len(out)
    pair_count = sum(len(v) for v in out.values())
    print(
        f"  Written {out_path}\n"
        f"  Uncompressed: {len(data)/1024/1024:.1f} MB  "
        f"Compressed: {gz_size/1024/1024:.1f} MB\n"
        f"  Dep stations: {dep_count}  (dep,arr) pairs: {pair_count}\n"
        f"  Total time: {time.time()-t0:.1f}s"
    )


if __name__ == "__main__":
    main()
