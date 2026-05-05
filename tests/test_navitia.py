"""
Tests for navitia_client.py — all HTTP calls are mocked so no real token needed.
"""

import pytest
from unittest.mock import patch, MagicMock
from providers import navitia as navitia_client


# ---------------------------------------------------------------------------
# Fixtures: sample Navitia API responses
# ---------------------------------------------------------------------------

PLACES_RESPONSE = {
    "places": [
        {
            "id": "stop_area:SNCF:87722025",
            "name": "Paris Gare de Lyon",
            "embedded_type": "stop_area",
            "stop_area": {
                "id": "stop_area:SNCF:87722025",
                "name": "Paris Gare de Lyon",
                "coord": {"lat": "48.844953", "lon": "2.373481"},
            },
        },
        {
            "id": "stop_area:SNCF:87686006",
            "name": "Lyon Part-Dieu",
            "embedded_type": "stop_area",
            "stop_area": {
                "id": "stop_area:SNCF:87686006",
                "name": "Lyon Part-Dieu",
                "coord": {"lat": "45.760590", "lon": "4.859386"},
            },
        },
    ]
}

ROUTES_RESPONSE = {
    "routes": [
        {
            "id": "route:SNCF:TGV-001",
            "name": "TGV Paris-Lyon",
            "line": {
                "id": "line:SNCF:TGV-001",
                "code": "TGV",
                "physical_modes": [
                    {"id": "physical_mode:LongDistanceTrain", "name": "TGV"}
                ],
            },
        },
        {
            "id": "route:SNCF:BUS-001",
            "name": "Bus ligne 42",
            "line": {
                "id": "line:SNCF:BUS-001",
                "code": "42",
                "physical_modes": [{"id": "physical_mode:Bus", "name": "Bus"}],
            },
        },
    ]
}

# Route schedule: Paris → Lyon Part-Dieu → Marseille
# Two trips: 07:00 and 09:00 departures from Paris
ROUTE_SCHEDULES_RESPONSE = {
    "route_schedules": [
        {
            "display_informations": {"code": "TGV", "network": "SNCF"},
            "table": {
                "rows": [
                    # Row 0 = Paris (origin)
                    {
                        "stop_point": {
                            "id": "stop_point:SNCF:87722025:1",
                            "name": "Paris Gare de Lyon",
                            "stop_area": {
                                "id": "stop_area:SNCF:87722025",
                                "name": "Paris Gare de Lyon",
                                "coord": {"lat": "48.844953", "lon": "2.373481"},
                            },
                        },
                        "date_times": [
                            {"date_time": "20240101T070000"},
                            {"date_time": "20240101T090000"},
                        ],
                    },
                    # Row 1 = Lyon Part-Dieu (2h after Paris)
                    {
                        "stop_point": {
                            "id": "stop_point:SNCF:87686006:1",
                            "name": "Lyon Part-Dieu",
                            "stop_area": {
                                "id": "stop_area:SNCF:87686006",
                                "name": "Lyon Part-Dieu",
                                "coord": {"lat": "45.760590", "lon": "4.859386"},
                            },
                        },
                        "date_times": [
                            {"date_time": "20240101T090000"},  # 2h after first trip
                            {"date_time": "20240101T110000"},  # 2h after second trip
                        ],
                    },
                    # Row 2 = Marseille (4h after Paris)
                    {
                        "stop_point": {
                            "id": "stop_point:SNCF:87751008:1",
                            "name": "Marseille Saint-Charles",
                            "stop_area": {
                                "id": "stop_area:SNCF:87751008",
                                "name": "Marseille Saint-Charles",
                                "coord": {"lat": "43.302500", "lon": "5.380400"},
                            },
                        },
                        "date_times": [
                            {"date_time": "20240101T110000"},  # 4h after first trip
                            {"date_time": "20240101T130000"},  # 4h after second trip
                        ],
                    },
                ]
            },
        }
    ]
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    mock.raise_for_status = MagicMock()
    return mock


# ---------------------------------------------------------------------------
# Tests: country configuration
# ---------------------------------------------------------------------------


class TestCountryConfig:
    def test_france_uses_sncf_base_url(self):
        url = navitia_client.get_base_url("fr")
        assert "api.sncf.com" in url
        assert "sncf" in url

    def test_italy_raises_on_get_base_url(self):
        """Italy uses ViaggiaTreno, not Navitia — get_base_url should explain."""
        with pytest.raises(ValueError, match="ViaggiaTreno"):
            navitia_client.get_base_url("it")

    def test_france_token_reads_sncf_env_var(self, monkeypatch):
        monkeypatch.setenv("SNCF_API_TOKEN", "fr-test-token")
        assert navitia_client.get_token("fr") == "fr-test-token"

    def test_italy_token_returns_not_required(self):
        """Italy's get_token always returns a truthy sentinel — no env var needed."""
        token = navitia_client.get_token("it")
        assert token  # truthy — so token checks in main.py pass for Italy

    def test_unsupported_country_raises(self):
        with pytest.raises(ValueError, match="Unsupported country"):
            navitia_client.get_base_url("de")


# ---------------------------------------------------------------------------
# Tests: search_stations
# ---------------------------------------------------------------------------


class TestSearchStations:
    def test_returns_parsed_stations(self):
        with patch("providers.navitia.httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.get.return_value = _mock_response(PLACES_RESPONSE)

            results = navitia_client.search_stations("Paris")

        assert len(results) == 2
        paris = results[0]
        assert paris["id"] == "stop_area:SNCF:87722025"
        assert paris["name"] == "Paris Gare de Lyon"
        assert abs(paris["lat"] - 48.844953) < 0.001
        assert abs(paris["lon"] - 2.373481) < 0.001

    def test_empty_query_returns_empty(self):
        with patch("providers.navitia.httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.get.return_value = _mock_response({"places": []})

            results = navitia_client.search_stations("")

        assert results == []

    def test_correct_endpoint_called(self):
        with patch("providers.navitia.httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.get.return_value = _mock_response({"places": []})

            navitia_client.search_stations("Lyon", count=5)

        call_args = instance.get.call_args
        assert "/places" in call_args[0][0]
        assert call_args[1]["params"]["q"] == "Lyon"
        assert call_args[1]["params"]["count"] == 5

    def test_italy_search_delegates_to_trenitalia(self):
        """Italy searches go to trenitalia_client, not httpx directly."""
        with patch("providers.navitia.trenitalia_client.search_stations") as mock_search:
            mock_search.return_value = [{"id": "S08409", "name": "Roma Termini", "lat": 41.9, "lon": 12.5}]
            results = navitia_client.search_stations("Roma", country="it")
        mock_search.assert_called_once_with("Roma", count=10)
        assert results[0]["id"] == "S08409"

    def test_france_search_uses_sncf_base_url(self):
        with patch("providers.navitia.httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.get.return_value = _mock_response({"places": []})

            navitia_client.search_stations("Paris", country="fr")

        call_args = instance.get.call_args
        url = call_args[0][0]
        assert "api.sncf.com" in url


# ---------------------------------------------------------------------------
# Tests: get_direct_connections
# ---------------------------------------------------------------------------


class TestGetDirectConnections:
    ORIGIN_SA_ID = "stop_area:SNCF:87722025"  # Paris Gare de Lyon

    def _setup_mock_client(self, MockClient):
        """Configure the mock client to return fixture data in sequence."""
        instance = MockClient.return_value.__enter__.return_value

        def side_effect(url, **kwargs):
            if "/routes" in url and "route_schedules" not in url:
                return _mock_response(ROUTES_RESPONSE)
            elif "route_schedules" in url:
                return _mock_response(ROUTE_SCHEDULES_RESPONSE)
            return _mock_response({})

        instance.get.side_effect = side_effect
        return instance

    def _get_result(self, MockClient):
        return navitia_client.get_direct_connections(self.ORIGIN_SA_ID)

    def test_returns_connections_and_route_paths(self):
        """Result must be a dict with both keys."""
        with patch("providers.navitia.httpx.Client") as MockClient:
            self._setup_mock_client(MockClient)
            result = self._get_result(MockClient)
        assert "connections" in result
        assert "route_paths" in result

    def test_filters_non_train_routes(self):
        """Bus routes should not produce connections."""
        with patch("providers.navitia.httpx.Client") as MockClient:
            self._setup_mock_client(MockClient)
            result = self._get_result(MockClient)

        ids = {c["id"] for c in result["connections"]}
        assert "stop_area:SNCF:87686006" in ids  # Lyon
        assert "stop_area:SNCF:87751008" in ids  # Marseille

    def test_line_codes_included(self):
        with patch("providers.navitia.httpx.Client") as MockClient:
            self._setup_mock_client(MockClient)
            result = self._get_result(MockClient)

        lyon = next(
            c for c in result["connections"] if c["id"] == "stop_area:SNCF:87686006"
        )
        assert "TGV" in lyon["lines"]

    def test_no_routes_returns_empty(self):
        with patch("providers.navitia.httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.get.return_value = _mock_response({"routes": []})
            result = navitia_client.get_direct_connections(self.ORIGIN_SA_ID)

        assert result["connections"] == []
        assert result["route_paths"] == []

    def test_coordinates_included(self):
        with patch("providers.navitia.httpx.Client") as MockClient:
            self._setup_mock_client(MockClient)
            result = self._get_result(MockClient)

        lyon = next(
            c for c in result["connections"] if c["id"] == "stop_area:SNCF:87686006"
        )
        assert abs(lyon["lat"] - 45.760590) < 0.001
        assert abs(lyon["lon"] - 4.859386) < 0.001

    # ── Route path tests ──────────────────────────────────────────────────────

    def test_route_path_has_correct_structure(self):
        """Each route path must have line_code and stops."""
        with patch("providers.navitia.httpx.Client") as MockClient:
            self._setup_mock_client(MockClient)
            result = self._get_result(MockClient)

        assert len(result["route_paths"]) >= 1
        path = result["route_paths"][0]
        assert "line_code" in path
        assert "stops" in path
        assert isinstance(path["stops"], list)

    def test_route_path_starts_with_origin(self):
        """The first stop of every route path must be the origin station."""
        with patch("providers.navitia.httpx.Client") as MockClient:
            self._setup_mock_client(MockClient)
            result = self._get_result(MockClient)

        for path in result["route_paths"]:
            assert path["stops"][0]["id"] == self.ORIGIN_SA_ID

    def test_route_path_ordered_stops(self):
        """Route path for TGV fixture should be Paris → Lyon → Marseille."""
        with patch("providers.navitia.httpx.Client") as MockClient:
            self._setup_mock_client(MockClient)
            result = self._get_result(MockClient)

        path = result["route_paths"][0]
        stop_ids = [s["id"] for s in path["stops"]]
        assert stop_ids == [
            "stop_area:SNCF:87722025",  # Paris
            "stop_area:SNCF:87686006",  # Lyon
            "stop_area:SNCF:87751008",  # Marseille
        ]

    def test_route_paths_deduplicated(self):
        """Identical stop sequences from different schedules should not be duplicated."""
        doubled_response = {
            "route_schedules": ROUTE_SCHEDULES_RESPONSE["route_schedules"] * 2
        }
        with patch("providers.navitia.httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value

            def side_effect(url, **kwargs):
                if "/routes" in url and "route_schedules" not in url:
                    return _mock_response(ROUTES_RESPONSE)
                elif "route_schedules" in url:
                    return _mock_response(doubled_response)
                return _mock_response({})

            instance.get.side_effect = side_effect
            result = navitia_client.get_direct_connections(self.ORIGIN_SA_ID)

        # Still only one unique path (Paris → Lyon → Marseille)
        assert len(result["route_paths"]) == 1

    def test_route_path_line_code(self):
        """Route path should carry the correct line code."""
        with patch("providers.navitia.httpx.Client") as MockClient:
            self._setup_mock_client(MockClient)
            result = self._get_result(MockClient)

        path = result["route_paths"][0]
        assert path["line_code"] == "TGV"

    def test_italy_country_param_delegates_to_trenitalia(self):
        """When country='it', get_direct_connections delegates to trenitalia_client."""
        with patch("providers.navitia.trenitalia_client.get_direct_connections") as mock_it:
            mock_it.return_value = {"connections": [], "route_paths": []}
            navitia_client.get_direct_connections(self.ORIGIN_SA_ID, country="it")
        mock_it.assert_called_once()
