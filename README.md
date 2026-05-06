# Train & Bus Map

Interactive map of direct train and bus connections across France and Italy. Type any station or city name — the app shows every destination reachable by a single direct service, drawn as colour-coded polylines with intermediate stops and scheduled times.

## Features

- **France trains** — live data from the [SNCF / Navitia API](https://numerique.sncf.com/startup/api/). Requires a free API token.
- **Italy trains** — live data from the unofficial [ViaggiaTreno API](http://www.viaggiatreno.it). No token required.
- **Bus (Europe)** — live data from the [FlixBus public API](https://global.api.flixbus.com). No token required. European-wide city pool; routes include full intermediate stop sequences from a bundled GTFS snapshot.
- **Progressive loading** — results stream to the browser via Server-Sent Events (SSE) so the map updates as each route is fetched.
- **Scheduled times** — departure and arrival times shown in the route summary and per stop in the sidebar accordion.
- **Auto country detection** — country is inferred from the selected station; no manual country selector needed. Autocomplete results show a country flag (🇫🇷 🇮🇹) per suggestion.
- **Multilingual UI** — English and French, persisted in `localStorage`.

## How it works

1. You pick a transport mode (`Train` / `Bus`).
2. You type a city or station name. The backend fans out to all supported countries in parallel and returns merged autocomplete suggestions, each tagged with a country flag.
3. You select a station and a date, then click **Search**.
4. For **trains**, the backend fetches all routes through the station and their full-day timetables. For **bus**, it queries FlixBus for every city in the European pool (~215 cities) in parallel.
5. Results stream back as SSE. Each route is drawn as a coloured polyline. Selecting a route highlights it, shows stop markers, and expands the route accordion in the sidebar.

> **Prices** are not available through the free SNCF or FlixBus public APIs.

## Installation

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- A free SNCF API token for France trains — register at <https://numerique.sncf.com/startup/api/token-developpeur/> (~5 minutes, 150 000 requests/month). **Not required for Italy or bus mode.**

### Steps

```bash
# 1. Clone the repo
git clone <repo-url>
cd train_map

# 2. Install dependencies (uv creates the virtualenv automatically)
uv sync              # runtime deps
uv sync --extra dev  # also installs pytest

# 3. Configure your API token (only needed for France trains)
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
uv run pytest tests/ -v
```

## Refreshing the FlixBus GTFS data

Bus intermediate stops (coordinates and scheduled times) are resolved from a bundled GTFS SQLite snapshot (`flixbus_stops.db.gz`). To refresh it from the latest FlixBus feed published on [MobilityData](https://mobilitydata.org/):

```bash
uv run python3 scripts/rebuild_flixbus_stops.py
```

This downloads the GTFS zip (~35 MB), indexes every `(stop_i, stop_j)` sub-pair within each trip with per-stop departure times, and writes a new `providers/data/flixbus_stops.db.gz` (~13 MB compressed, ~238 MB SQLite). Takes about 10 seconds.

> **Schedule drift:** GTFS times are static and drift from the live FlixBus schedule over time. The backend corrects for this automatically: for each route it computes the offset between the live API departure time and the GTFS first-stop time, then shifts every intermediate stop's time by that amount. The origin stop always matches exactly; small residual drift (a few minutes) may remain at intermediate and final stops.

## Project structure

```
train_map/
├── main.py                          # FastAPI app — /api/stations, /api/connections/stream
├── providers/                       # One module per data source
│   ├── __init__.py
│   ├── navitia.py                   # Train dispatcher: France (Navitia) + Italy (ViaggiaTreno)
│   ├── trenitalia.py                # ViaggiaTreno client for Italy trains
│   ├── flixbus.py                   # FlixBus client: city search, connections, GTFS stop lookup
│   └── data/
│       ├── trenitalia_stations.csv  # Bundled Italian station coordinates (2963 entries)
│       └── flixbus_stops.db.gz      # Pre-processed GTFS SQLite (~13 MB compressed, all (i,j) stop pairs)
├── tests/
│   ├── test_navitia.py              # Unit tests for navitia + trenitalia clients
│   └── test_flixbus.py              # Unit tests for flixbus client
├── scripts/
│   └── rebuild_flixbus_stops.py     # Regenerates providers/data/flixbus_stops.db.gz
├── static/
│   ├── index.html                   # Page shell — imports all scripts and styles
│   ├── style.css                    # All styles (layout, sidebar, map, components)
│   ├── i18n.js                      # Translations (EN/FR) and t() helper
│   ├── map.js                       # Leaflet map initialisation and tile layer
│   ├── sidebar.js                   # Mobile sidebar sheet (closed / peek / open states)
│   ├── autocomplete.js              # Station/city search input, suggestion dropdown, flag rendering
│   ├── routes.js                    # Route rendering, selection, stop activation, clearMap()
│   └── app.js                       # Entry point — mode switch, date picker, selectStation()
├── pyproject.toml
├── requirements.txt
├── .env                             # Your secret token (git-ignored)
└── .env.example                     # Token template
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/stations?q=<query>&mode=train` | Autocomplete station or city names (fans out to all countries) |
| `GET` | `/api/connections/stream?station_id=<id>&country=fr&mode=train&date=<YYYYMMDD>` | SSE stream of direct connections |
| `GET` | `/api/connections?station_id=<id>&country=fr&mode=train&date=<YYYYMMDD>` | Non-streaming equivalent |

- `country` on `/api/connections`: derived from the selected station (`fr` / `it` / `eu`); defaults to `fr`
- `mode`: `train` (default) or `bus`
- `date`: optional `YYYYMMDD`; defaults to the current moment when omitted
- `/api/stations` no longer accepts a `country` param — it always fans out to all supported countries

## Frontend architecture

The frontend is plain JavaScript — no framework, no bundler. Each file has a single responsibility and communicates through shared globals (loaded in dependency order via `<script>` tags):

| File | Globals it exposes | Globals it consumes |
|------|--------------------|---------------------|
| `i18n.js` | `t()`, `setLang()`, `currentLang` | — |
| `map.js` | `map` | `L` (Leaflet) |
| `sidebar.js` | `isMobile()`, `setSidebar()`, `peekSidebar()`, `sidebarState()`, `sidebarLabel`, `fabCount` | — |
| `autocomplete.js` | `input` | `t()`, `showStatus()`, `selectedMode` |
| `routes.js` | `renderConnections()`, `addRoute()`, `clearMap()`, `originMarker`, `originIcon`, `connList`, `connCount`, `busMarkers` | `map`, `isMobile()`, `peekSidebar()`, `sidebarState()`, `t()`, `selectStation()` |
| `app.js` | `selectStation()`, `onStationSelected()`, `showStatus()`, `selectedStation`, `selectedMode`, `dateInput`, `status` | all of the above |

## Data sources and known constraints

| Source | Auth | Notes |
|--------|------|-------|
| SNCF / Navitia | `SNCF_API_TOKEN` | France trains only; free tier: 150 k req/month |
| ViaggiaTreno | none | Italy trains; unofficial API, may change without notice |
| FlixBus public API | none | Bus (EU); city-level, no intra-city stops |
| FlixBus GTFS (MobilityData) | none | Bundled snapshot; run `rebuild_flixbus_stops.py` to refresh |

- Italy station coordinates come from the bundled `trenitalia_stations.csv` (2015 dump); newer stations are fetched live and cached in memory.
- FlixBus bus stop intermediate coordinates and scheduled times come from the bundled GTFS SQLite snapshot. The GTFS is keyed by all `(stop_i, stop_j)` sub-pairs so mid-route boarding and alighting are both handled correctly. Stop times are shift-corrected at query time using the live API departure as the anchor.
- The FlixBus city cache (~215 European cities) is built once per server session from 26 single-letter autocomplete queries (~2 s cold start).
- Map tiles: OpenStreetMap standard tiles (`{s}.tile.openstreetmap.org`). Respect the [OSM tile usage policy](https://operations.osmfoundation.org/policies/tiles/) — do not increase `maxZoom` above 19 or remove attribution.
