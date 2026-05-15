"""Tests for flixbus_client.py — all HTTP calls are mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from providers import flixbus as flixbus_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


PARIS = {
    "id": "40de8964-8646-11e6-9066-549f350fcb0c",
    "name": "Paris",
    "location": {"lat": 48.8566, "lon": 2.3522},
    "is_flixbus_city": True,
}

LYON = {
    "id": "40dea4a8-8646-11e6-9066-549f350fcb0c",
    "name": "Lyon",
    "location": {"lat": 45.7640, "lon": 4.8357},
    "is_flixbus_city": True,
}

MARSEILLE = {
    "id": "40dec112-8646-11e6-9066-549f350fcb0c",
    "name": "Marseille",
    "location": {"lat": 43.2965, "lon": 5.3698},
    "is_flixbus_city": True,
}

# A city without coordinates — should be skipped
NO_COORDS_CITY = {
    "id": "bad-city-id",
    "name": "NoCoords",
    "location": {},
    "is_flixbus_city": True,
}


# ---------------------------------------------------------------------------
# search_cities
# ---------------------------------------------------------------------------

class TestSearchCities:
    def test_returns_matching_cities(self):
        with patch("providers.flixbus.httpx.get") as mock_get:
            mock_get.return_value = _mock_response([PARIS, LYON])
            result = flixbus_client.search_cities("par", country="fr")

        assert len(result) == 2
        assert result[0]["name"] == "Paris"
        assert result[0]["lat"] == pytest.approx(48.8566)
        assert result[0]["lon"] == pytest.approx(2.3522)

    def test_skips_cities_without_coords(self):
        with patch("providers.flixbus.httpx.get") as mock_get:
            mock_get.return_value = _mock_response([PARIS, NO_COORDS_CITY])
            result = flixbus_client.search_cities("par", country="fr")

        assert len(result) == 1
        assert result[0]["name"] == "Paris"

    def test_returns_empty_on_error(self):
        with patch("providers.flixbus.httpx.get") as mock_get:
            mock_get.side_effect = Exception("network error")
            result = flixbus_client.search_cities("par", country="fr")

        assert result == []


# ---------------------------------------------------------------------------
# _date_param
# ---------------------------------------------------------------------------

class TestDateParam:
    def test_converts_yyyymmdd_to_ddmmyyyy(self):
        assert flixbus_client._date_param("20260506") == "06.05.2026"

    def test_invalid_returns_today(self):
        result = flixbus_client._date_param("not-a-date")
        # Just verify it's in DD.MM.YYYY format
        parts = result.split(".")
        assert len(parts) == 3
        assert len(parts[0]) == 2

    def test_none_returns_today(self):
        result = flixbus_client._date_param(None)
        parts = result.split(".")
        assert len(parts) == 3


# ---------------------------------------------------------------------------
# _check_connection
# ---------------------------------------------------------------------------

class TestCheckConnection:
    def test_returns_none_for_same_city(self):
        result = flixbus_client._check_connection("city-1", {"id": "city-1"}, "06.05.2026")
        assert result is None

    def test_returns_connection_for_direct_trip(self):
        search_resp = {
            "trips": [{
                "results": {
                    "direct:abc:xyz": {
                        "transfer_type_key": "direct",
                        "legs": [
                            {
                                "departure": {"date": "2026-05-06T08:00:00", "city_id": "city-1", "station_id": "s1"},
                                "arrival":   {"date": "2026-05-06T12:30:00", "city_id": "city-2", "station_id": "s2"},
                            }
                        ],
                    }
                }
            }]
        }
        with patch("providers.flixbus.httpx.get") as mock_get:
            mock_get.return_value = _mock_response(search_resp)
            result = flixbus_client._check_connection(
                "city-1",
                {"id": "city-2", "name": "Lyon", "lat": 45.76, "lon": 4.83},
                "06.05.2026",
            )

        assert result is not None
        assert len(result) == 1
        assert result[0]["city"]["name"] == "Lyon"
        assert result[0]["departure_time"] == "08:00"
        assert result[0]["arrival_time"] == "12:30"
        assert result[0]["dep_iso"] == "2026-05-06T08:00:00"
        assert result[0]["arr_iso"] == "2026-05-06T12:30:00"
        assert len(result[0]["legs"]) == 1
        assert result[0]["dep_station_id"] == "s1"
        assert result[0]["arr_station_id"] == "s2"

    def test_returns_multiple_trips(self):
        search_resp = {
            "trips": [{
                "results": {
                    "direct:abc:xyz": {
                        "transfer_type_key": "direct",
                        "legs": [{"departure": {"date": "2026-05-06T08:00:00", "city_id": "city-1"},
                                  "arrival":   {"date": "2026-05-06T12:30:00", "city_id": "city-2"}}],
                    },
                    "direct:def:uvw": {
                        "transfer_type_key": "direct",
                        "legs": [{"departure": {"date": "2026-05-06T14:00:00", "city_id": "city-1"},
                                  "arrival":   {"date": "2026-05-06T18:00:00", "city_id": "city-2"}}],
                    },
                }
            }]
        }
        with patch("providers.flixbus.httpx.get") as mock_get:
            mock_get.return_value = _mock_response(search_resp)
            result = flixbus_client._check_connection(
                "city-1",
                {"id": "city-2", "name": "Lyon", "lat": 45.76, "lon": 4.83},
                "06.05.2026",
            )

        assert result is not None
        assert len(result) == 2
        dep_times = {r["departure_time"] for r in result}
        assert dep_times == {"08:00", "14:00"}

    def test_returns_none_for_ic_only(self):
        search_resp = {
            "trips": [{
                "results": {
                    "ic:abc:xyz": {
                        "transfer_type_key": "interconnection",
                        "legs": [],
                    }
                }
            }]
        }
        with patch("providers.flixbus.httpx.get") as mock_get:
            mock_get.return_value = _mock_response(search_resp)
            result = flixbus_client._check_connection(
                "city-1",
                {"id": "city-2", "name": "Lyon", "lat": 45.76, "lon": 4.83},
                "06.05.2026",
            )

        assert result is None

    def test_returns_none_on_non_200(self):
        with patch("providers.flixbus.httpx.get") as mock_get:
            mock_get.return_value = _mock_response({}, status_code=404)
            result = flixbus_client._check_connection(
                "city-1",
                {"id": "city-2", "name": "Lyon", "lat": 45.76, "lon": 4.83},
                "06.05.2026",
            )

        assert result is None

    def test_returns_none_on_exception(self):
        with patch("providers.flixbus.httpx.get") as mock_get:
            mock_get.side_effect = Exception("timeout")
            result = flixbus_client._check_connection(
                "city-1",
                {"id": "city-2", "name": "Lyon", "lat": 45.76, "lon": 4.83},
                "06.05.2026",
            )

        assert result is None


# ---------------------------------------------------------------------------
# get_direct_connections
# ---------------------------------------------------------------------------

def _make_legs(dep_city_id, arr_city_id, dep_iso, arr_iso, dep_station_id="s-dep", arr_station_id="s-arr"):
    """Helper: build a minimal legs list for a single-leg trip."""
    return [
        {
            "departure": {"city_id": dep_city_id, "date": dep_iso, "station_id": dep_station_id},
            "arrival":   {"city_id": arr_city_id, "date": arr_iso, "station_id": arr_station_id},
        }
    ]


class TestGetDirectConnections:
    def setup_method(self):
        # Clear the connection cache so tests don't share state
        import providers.flixbus as fb
        fb._connections_cache.clear()

    def test_returns_connections_with_all_trips(self):
        paris_city = {"id": "paris-id", "name": "Paris", "lat": 48.85, "lon": 2.35}
        lyon_city  = {"id": "lyon-id",  "name": "Lyon",  "lat": 45.76, "lon": 4.83}

        def fake_build(country):
            return {"paris-id": paris_city, "lyon-id": lyon_city}

        def fake_check(from_id, to_city, date_str):
            if to_city["id"] == "lyon-id":
                return [
                    {"city": lyon_city, "departure_time": "08:00", "arrival_time": "10:30",
                     "dep_iso": "2026-05-06T08:00:00+02:00", "arr_iso": "2026-05-06T10:30:00+02:00",
                     "dep_station_id": "s1", "arr_station_id": "s2",
                     "legs": _make_legs("paris-id", "lyon-id",
                                        "2026-05-06T08:00:00+02:00", "2026-05-06T10:30:00+02:00")},
                    {"city": lyon_city, "departure_time": "14:00", "arrival_time": "16:30",
                     "dep_iso": "2026-05-06T14:00:00+02:00", "arr_iso": "2026-05-06T16:30:00+02:00",
                     "dep_station_id": "s1", "arr_station_id": "s2",
                     "legs": _make_legs("paris-id", "lyon-id",
                                        "2026-05-06T14:00:00+02:00", "2026-05-06T16:30:00+02:00")},
                ]
            return None

        with patch("providers.flixbus._build_city_cache", side_effect=fake_build):
            with patch("providers.flixbus._check_connection", side_effect=fake_check):
                with patch("providers.flixbus._lookup_stops", return_value=None):
                    result = flixbus_client.get_direct_connections(
                        "paris-id", date="20260506", country="fr"
                    )

        assert len(result["connections"]) == 1
        conn = result["connections"][0]
        assert conn["name"] == "Lyon"
        assert len(conn["lines"]) == 2
        assert conn["lines"][0]["departure_time"] == "08:00"
        assert conn["lines"][1]["departure_time"] == "14:00"

    def test_builds_route_paths_from_legs(self):
        paris_city = {"id": "paris-id", "name": "Paris", "lat": 48.85, "lon": 2.35}
        lyon_city  = {"id": "lyon-id",  "name": "Lyon",  "lat": 45.76, "lon": 4.83}

        def fake_build(country):
            return {"paris-id": paris_city, "lyon-id": lyon_city}

        def fake_check(from_id, to_city, date_str):
            if to_city["id"] == "lyon-id":
                return [
                    {"city": lyon_city, "departure_time": "08:00", "arrival_time": "10:30",
                     "dep_iso": "2026-05-06T08:00:00+02:00", "arr_iso": "2026-05-06T10:30:00+02:00",
                     "dep_station_id": "s1", "arr_station_id": "s2",
                     "legs": _make_legs("paris-id", "lyon-id",
                                        "2026-05-06T08:00:00+02:00", "2026-05-06T10:30:00+02:00")},
                ]
            return None

        with patch("providers.flixbus._build_city_cache", side_effect=fake_build):
            with patch("providers.flixbus._check_connection", side_effect=fake_check):
                with patch("providers.flixbus._lookup_stops", return_value=None):
                    result = flixbus_client.get_direct_connections(
                        "paris-id", date="20260506", country="fr"
                    )

        assert len(result["route_paths"]) == 1
        rp = result["route_paths"][0]
        assert rp["line_code"] == "FlixBus 08:00"
        assert len(rp["stops"]) == 2
        assert rp["stops"][0]["id"] == "paris-id"
        assert rp["stops"][1]["id"] == "lyon-id"

    def test_gtfs_stops_used_when_available(self):
        """When _lookup_stops returns data, route_paths uses full GTFS stop list."""
        paris_city = {"id": "paris-id", "name": "Paris", "lat": 48.85, "lon": 2.35}
        lyon_city  = {"id": "lyon-id",  "name": "Lyon",  "lat": 45.76, "lon": 4.83}

        def fake_build(country):
            return {"paris-id": paris_city, "lyon-id": lyon_city}

        def fake_check(from_id, to_city, date_str):
            if to_city["id"] == "lyon-id":
                return [
                    {"city": lyon_city, "departure_time": "08:00", "arrival_time": "10:30",
                     "dep_iso": "2026-05-06T08:00:00+02:00", "arr_iso": "2026-05-06T10:30:00+02:00",
                     "dep_station_id": "s-stras", "arr_station_id": "s-cdg",
                     "legs": _make_legs("paris-id", "lyon-id",
                                        "2026-05-06T08:00:00+02:00", "2026-05-06T10:30:00+02:00",
                                        "s-stras", "s-cdg")},
                ]
            return None

        # Simulate GTFS returning 3 stops (origin + 1 intermediate + destination)
        gtfs_stops = [
            {"id": "s-stras", "name": "Strasbourg", "lat": 48.57, "lon": 7.75},
            {"id": "s-nancy", "name": "Nancy",       "lat": 48.69, "lon": 6.18},
            {"id": "s-cdg",   "name": "CDG Airport", "lat": 49.01, "lon": 2.56},
        ]

        with patch("providers.flixbus._build_city_cache", side_effect=fake_build):
            with patch("providers.flixbus._check_connection", side_effect=fake_check):
                with patch("providers.flixbus._lookup_stops", return_value=gtfs_stops):
                    result = flixbus_client.get_direct_connections(
                        "paris-id", date="20260506", country="fr"
                    )

        rp = result["route_paths"][0]
        assert len(rp["stops"]) == 3
        assert rp["stops"][1]["name"] == "Nancy"

    def test_route_path_with_intermediate_stops(self):
        """Multi-leg trip produces a path with all leg cities (fallback when no GTFS)."""
        paris_city  = {"id": "paris-id",  "name": "Paris",   "lat": 48.85, "lon": 2.35}
        reims_city  = {"id": "reims-id",  "name": "Reims",   "lat": 49.26, "lon": 4.03}
        berlin_city = {"id": "berlin-id", "name": "Berlin",  "lat": 52.52, "lon": 13.40}

        def fake_build(country):
            return {"paris-id": paris_city, "reims-id": reims_city, "berlin-id": berlin_city}

        def fake_check(from_id, to_city, date_str):
            if to_city["id"] == "berlin-id":
                return [
                    {"city": berlin_city, "departure_time": "08:00", "arrival_time": "20:00",
                     "dep_iso": "2026-05-06T08:00:00+02:00", "arr_iso": "2026-05-06T20:00:00+02:00",
                     "dep_station_id": "s-paris", "arr_station_id": "s-berlin",
                     "legs": [
                         {"departure": {"city_id": "paris-id",  "date": "2026-05-06T08:00:00+02:00", "station_id": "s-paris"},
                          "arrival":   {"city_id": "reims-id",  "date": "2026-05-06T09:30:00+02:00", "station_id": "s-reims"}},
                         {"departure": {"city_id": "reims-id",  "date": "2026-05-06T10:00:00+02:00", "station_id": "s-reims"},
                          "arrival":   {"city_id": "berlin-id", "date": "2026-05-06T20:00:00+02:00", "station_id": "s-berlin"}},
                     ]},
                ]
            return None

        with patch("providers.flixbus._build_city_cache", side_effect=fake_build):
            with patch("providers.flixbus._check_connection", side_effect=fake_check):
                with patch("providers.flixbus._lookup_stops", return_value=None):
                    result = flixbus_client.get_direct_connections(
                        "paris-id", date="20260506", country="fr"
                    )

        rp = result["route_paths"][0]
        stop_ids = [s["id"] for s in rp["stops"]]
        assert stop_ids == ["paris-id", "reims-id", "berlin-id"]

    def test_deduplicates_identical_trips(self):
        paris_city = {"id": "paris-id", "name": "Paris", "lat": 48.85, "lon": 2.35}
        lyon_city  = {"id": "lyon-id",  "name": "Lyon",  "lat": 45.76, "lon": 4.83}

        def fake_build(country):
            return {"paris-id": paris_city, "lyon-id": lyon_city}

        legs = _make_legs("paris-id", "lyon-id",
                          "2026-05-06T08:00:00+02:00", "2026-05-06T10:30:00+02:00")

        def fake_check(from_id, to_city, date_str):
            if to_city["id"] == "lyon-id":
                return [
                    {"city": lyon_city, "departure_time": "08:00", "arrival_time": "10:30",
                     "dep_iso": "2026-05-06T08:00:00+02:00", "arr_iso": "2026-05-06T10:30:00+02:00",
                     "dep_station_id": "s1", "arr_station_id": "s2", "legs": legs},
                    {"city": lyon_city, "departure_time": "08:00", "arrival_time": "10:30",
                     "dep_iso": "2026-05-06T08:00:00+02:00", "arr_iso": "2026-05-06T10:30:00+02:00",
                     "dep_station_id": "s1", "arr_station_id": "s2", "legs": legs},
                ]
            return None

        with patch("providers.flixbus._build_city_cache", side_effect=fake_build):
            with patch("providers.flixbus._check_connection", side_effect=fake_check):
                with patch("providers.flixbus._lookup_stops", return_value=None):
                    result = flixbus_client.get_direct_connections(
                        "paris-id", date="20260506", country="fr"
                    )

        assert len(result["connections"][0]["lines"]) == 1

    def test_progress_callback_called(self):
        lyon_city = {"id": "lyon-id", "name": "Lyon", "lat": 45.76, "lon": 4.83}

        def fake_build(country):
            return {"lyon-id": lyon_city}

        def fake_check(from_id, to_city, date_str):
            return None

        calls = []

        def on_progress(current, total, message):
            calls.append((current, total))

        with patch("providers.flixbus._build_city_cache", side_effect=fake_build):
            with patch("providers.flixbus._check_connection", side_effect=fake_check):
                flixbus_client.get_direct_connections(
                    "paris-id", date="20260506", country="fr",
                    progress_callback=on_progress,
                )

        assert len(calls) == 1
        assert calls[0][1] == 1
