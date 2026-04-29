# Train Map

Interactive map of direct train connections in France. Pick any station, and the app shows every city reachable by a single direct train.

Data is fetched live from the [SNCF / Navitia API](https://numerique.sncf.com/startup/api/).

## How it works

1. You type a city name — the backend queries the Navitia `places` endpoint for matching stations.
2. Once you select a station, the backend fetches all train routes passing through it (`stop_areas/{id}/routes`), then pulls the full-day timetable for each route (`routes/{id}/route_schedules`).
3. Results stream back to the browser via Server-Sent Events (SSE), so the map updates progressively as each route is processed.
4. Each route is drawn as a coloured polyline on the map. Selecting a route highlights it, shows stop markers, and opens the route accordion in the sidebar.

> **Prices** are not available through the free SNCF open API. Pricing data is only accessible via the commercial booking flow (SNCF Connect).

## Installation

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- A free SNCF API token — register at <https://numerique.sncf.com/startup/api/token-developpeur/> (takes ~5 minutes, 150 000 requests/month)

### Steps

```bash
# 1. Clone the repo
git clone <repo-url>
cd train_map

# 2. Install dependencies (uv creates the virtualenv automatically)
uv sync              # runtime deps
uv sync --extra dev  # also installs pytest

# 3. Configure your API token
cp .env.example .env
# Edit .env and set your token:
#   SNCF_API_TOKEN=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# 4. Start the server
uv run uvicorn main:app --reload
```

Open <http://localhost:8000> in your browser.

## Running the tests

Tests are fully mocked — no real API token required.

```bash
uv run pytest test_sncf_client.py -v
```

## Project structure

```
train_map/
├── main.py                  # FastAPI app — /api/stations and /api/connections/stream
├── sncf_client.py           # SNCF/Navitia API client (search + route fetching)
├── test_sncf_client.py      # Unit tests with mocked HTTP responses
├── static/
│   ├── index.html           # Page shell — imports all scripts and styles
│   ├── style.css            # All styles (layout, sidebar, map, components)
│   ├── i18n.js              # Translations (EN/FR) and t() helper
│   ├── map.js               # Leaflet map initialisation and tile layer
│   ├── sidebar.js           # Mobile sidebar sheet (closed / peek / open states)
│   ├── autocomplete.js      # Station search input and suggestion dropdown
│   ├── routes.js            # Route rendering, selection, stop activation, clearMap()
│   └── app.js               # Entry point — i18n, date picker, progress bar, selectStation()
├── pyproject.toml
├── requirements.txt
├── .env                     # Your secret token (git-ignored)
└── .env.example             # Token template
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/stations?q=<query>` | Autocomplete station names |
| `GET` | `/api/connections/stream?station_id=<id>&date=<YYYYMMDD>` | SSE stream of direct connections from a station |

## Frontend architecture

The frontend is plain JavaScript — no framework, no bundler. Each file has a single responsibility and communicates through shared globals (the files are loaded in dependency order via `<script>` tags):

| File | Globals it exposes | Globals it consumes |
|------|--------------------|---------------------|
| `i18n.js` | `t()`, `setLang()`, `currentLang` | — |
| `map.js` | `map` | `L` (Leaflet) |
| `sidebar.js` | `isMobile()`, `setSidebar()`, `peekSidebar()`, `sidebarState()`, `sidebarLabel`, `fabCount` | — |
| `autocomplete.js` | `input` | `selectStation()`, `t()`, `showStatus()` |
| `routes.js` | `renderConnections()`, `clearMap()`, `originMarker`, `originIcon`, `connList`, `connCount` | `map`, `isMobile()`, `peekSidebar()`, `sidebarState()`, `t()` |
| `app.js` | `selectStation()`, `showStatus()`, `selectedStation` | all of the above |
