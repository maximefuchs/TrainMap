#!/usr/bin/env python3
"""
Rebuild flixbus_stops.db.gz from the latest FlixBus GTFS feed.

Downloads the GTFS zip from the MobilityData public catalog, processes it
into a SQLite database, gzip-compresses it, and writes
providers/data/flixbus_stops.db.gz.

Usage:
    uv run python3 scripts/rebuild_flixbus_stops.py

The output file is used by providers/flixbus.py at startup. It is decompressed
once into a temporary file and queried on-demand via sqlite3, keeping memory
usage near zero regardless of the number of routes indexed.

Schema:
    stops(dep TEXT, arr TEXT, t TEXT, s TEXT)
      dep  — departure station UUID
      arr  — arrival station UUID
      t    — GTFS departure time of first stop, "HH:MM" (may exceed 24:00)
      s    — JSON array of stop entries [[id, name, lat, lon, "HH:MM"], ...]

    Index: (dep, arr) for fast lookup.

Every (stop_i, stop_j) sub-pair within each trip is indexed (i < j), so a bus
that originates in Milan but boards in Strasbourg on its way to Hamburg is
correctly found as Strasbourg -> Hamburg.
"""

import csv
import collections
import gzip
import io
import json
import os
import sqlite3
import tempfile
import time
import zipfile

import httpx

GTFS_URL = (
    "https://storage.googleapis.com/storage/v1/b/mdb-latest/o/"
    "de-unknown-flixbus-gtfs-853.zip?alt=media"
)
OUT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "providers", "data", "flixbus_stops.db.gz"
)


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

    # 3. Build all (stop_i, stop_j) sub-pairs with i < j within each trip.
    t2 = time.time()
    # Use a dict to deduplicate before inserting into SQLite.
    # Key: (dep_sid, arr_sid, fingerprint) → (t, s_json)
    rows: dict[tuple, tuple] = {}

    for tid, seq in trip_stops.items():
        n = len(seq)
        if n < 2:
            continue
        for i in range(n - 1):
            dep_sid = seq[i][1]
            dep_hhmm = seq[i][2][:5]
            for j in range(i + 1, n):
                arr_sid = seq[j][1]
                stop_slice = [
                    stops[seq[k][1]] + [seq[k][2][:5]]
                    for k in range(i, j + 1)
                ]
                fingerprint = dep_hhmm + "|" + "|".join(s[0] for s in stop_slice)
                key = (dep_sid, arr_sid, fingerprint)
                if key not in rows:
                    rows[key] = (
                        dep_hhmm,
                        json.dumps(stop_slice, separators=(",", ":")),
                    )

    print(f"  Built {len(rows)} rows in {time.time()-t2:.1f}s")

    # 4. Write SQLite db to a temp file, then gzip it.
    t3 = time.time()
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        con = sqlite3.connect(db_path)
        con.execute(
            "CREATE TABLE stops "
            "(dep TEXT NOT NULL, arr TEXT NOT NULL, t TEXT NOT NULL, s TEXT NOT NULL)"
        )
        con.executemany(
            "INSERT INTO stops VALUES (?, ?, ?, ?)",
            [
                (dep_sid, arr_sid, t_val, s_json)
                for (dep_sid, arr_sid, _fp), (t_val, s_json) in rows.items()
            ],
        )
        con.execute("CREATE INDEX idx_dep_arr ON stops (dep, arr)")
        con.commit()
        con.close()

        db_size = os.path.getsize(db_path)

        out_path = os.path.realpath(OUT_PATH)
        with open(db_path, "rb") as f_in, gzip.open(out_path, "wb") as f_out:
            f_out.write(f_in.read())

        gz_size = os.path.getsize(out_path)
    finally:
        os.unlink(db_path)

    print(
        f"  Written {out_path}\n"
        f"  SQLite: {db_size/1024/1024:.1f} MB  "
        f"Compressed: {gz_size/1024/1024:.1f} MB\n"
        f"  Rows: {len(rows)}\n"
        f"  Total time: {time.time()-t0:.1f}s"
    )


if __name__ == "__main__":
    main()
