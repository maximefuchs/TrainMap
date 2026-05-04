"""Tests for flixbus_client.py — all HTTP calls are mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import flixbus_client


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
        with patch("flixbus_client.httpx.get") as mock_get:
            mock_get.return_value = _mock_response([PARIS, LYON])
            result = flixbus_client.search_cities("par", country="fr")

        assert len(result) == 2
        assert result[0]["name"] == "Paris"
        assert result[0]["lat"] == pytest.approx(48.8566)
        assert result[0]["lon"] == pytest.approx(2.3522)

    def test_skips_cities_without_coords(self):
        with patch("flixbus_client.httpx.get") as mock_get:
            mock_get.return_value = _mock_response([PARIS, NO_COORDS_CITY])
            result = flixbus_client.search_cities("par", country="fr")

        assert len(result) == 1
        assert result[0]["name"] == "Paris"

    def test_returns_empty_on_error(self):
        with patch("flixbus_client.httpx.get") as mock_get:
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
                                "departure": {"date": "2026-05-06T08:00:00", "station_id": "s1"},
                                "arrival":   {"date": "2026-05-06T12:30:00", "station_id": "s2"},
                            }
                        ],
                    }
                }
            }]
        }
        with patch("flixbus_client.httpx.get") as mock_get:
            mock_get.return_value = _mock_response(search_resp)
            result = flixbus_client._check_connection(
                "city-1",
                {"id": "city-2", "name": "Lyon", "lat": 45.76, "lon": 4.83},
                "06.05.2026",
            )

        assert result is not None
        assert result["city"]["name"] == "Lyon"
        assert result["departure_time"] == "08:00"
        assert result["arrival_time"] == "12:30"

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
        with patch("flixbus_client.httpx.get") as mock_get:
            mock_get.return_value = _mock_response(search_resp)
            result = flixbus_client._check_connection(
                "city-1",
                {"id": "city-2", "name": "Lyon", "lat": 45.76, "lon": 4.83},
                "06.05.2026",
            )

        assert result is None

    def test_returns_none_on_non_200(self):
        with patch("flixbus_client.httpx.get") as mock_get:
            mock_get.return_value = _mock_response({}, status_code=404)
            result = flixbus_client._check_connection(
                "city-1",
                {"id": "city-2", "name": "Lyon", "lat": 45.76, "lon": 4.83},
                "06.05.2026",
            )

        assert result is None

    def test_returns_none_on_exception(self):
        with patch("flixbus_client.httpx.get") as mock_get:
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

class TestGetDirectConnections:
    def test_returns_connections_and_empty_route_paths(self):
        # Patch _build_city_cache to return a small set of cities
        # Patch _check_connection to return a direct trip for Lyon only
        lyon_city = {"id": "lyon-id", "name": "Lyon", "lat": 45.76, "lon": 4.83}
        marseille_city = {"id": "marseille-id", "name": "Marseille", "lat": 43.29, "lon": 5.37}

        def fake_build(country):
            return {
                "lyon-id":      lyon_city,
                "marseille-id": marseille_city,
            }

        def fake_check(from_id, to_city, date_str):
            if to_city["id"] == "lyon-id":
                return {"city": lyon_city, "departure_time": "08:00", "arrival_time": "12:30"}
            return None

        with patch("flixbus_client._build_city_cache", side_effect=fake_build):
            with patch("flixbus_client._check_connection", side_effect=fake_check):
                result = flixbus_client.get_direct_connections(
                    "paris-id", date="20260506", country="fr"
                )

        assert result["route_paths"] == []
        assert len(result["connections"]) == 1
        conn = result["connections"][0]
        assert conn["name"] == "Lyon"
        assert conn["lines"][0]["code"] == "FlixBus"
        assert conn["lines"][0]["departure_time"] == "08:00"

    def test_progress_callback_called(self):
        lyon_city = {"id": "lyon-id", "name": "Lyon", "lat": 45.76, "lon": 4.83}

        def fake_build(country):
            return {"lyon-id": lyon_city}

        def fake_check(from_id, to_city, date_str):
            return None

        calls = []

        def on_progress(current, total, message):
            calls.append((current, total))

        with patch("flixbus_client._build_city_cache", side_effect=fake_build):
            with patch("flixbus_client._check_connection", side_effect=fake_check):
                flixbus_client.get_direct_connections(
                    "paris-id", date="20260506", country="fr",
                    progress_callback=on_progress,
                )

        assert len(calls) == 1
        assert calls[0][1] == 1  # total = 1 destination
