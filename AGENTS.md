# Agent context — Train Map

## What the app does
Interactive map of direct train and bus connections across France and Italy. The user
picks a transport mode (Train / Bus), types a city or station name, selects it from
an autocomplete dropdown (results show a country flag per suggestion), picks a date,
and clicks Search. The app then draws every city reachable by a single direct service
as colour-coded polylines on a Leaflet map. A sidebar lists each route with its
departure/arrival times and all intermediate stops with their scheduled times.

Country is **inferred from the selected station** — there is no country selector in the UI.

## Tech stack
- **Backend:** Python / FastAPI, served by uvicorn. Managed with `uv`.
- **Data sources:**
  - France trains: Navitia REST API at `api.sncf.com/v1/coverage/sncf` (token `SNCF_API_TOKEN`)
  - Italy trains: ViaggiaTreno API at `viaggiatreno.it/infomobilita/resteasy/viaggiatreno` (**no token required**)
  - Bus (EU): FlixBus public API at `global.api.flixbus.com` (**no token required**)
- **Streaming:** results are pushed to the browser via Server-Sent Events (SSE)
  so the map updates progressively as each route is fetched.
- **Frontend:** plain HTML + CSS + vanilla JavaScript. No framework, no bundler,
  no npm. Leaflet 1.9.4 is loaded from unpkg CDN.
- **Map tiles:** OpenStreetMap standard tiles (`{s}.tile.openstreetmap.org`).
  The tile pane is darkened with `filter: brightness(0.65)` in `style.css` —
  **do not attempt a div/pane overlay**, it cannot be positioned correctly due
  to Leaflet's `transform` on `.leaflet-map-pane` creating a stacking context.
- **i18n:** custom lightweight system in `i18n.js`. Two locales: `en` and `fr`.
  Language is persisted in `localStorage`. `t(key, ...args)` is a global helper.
  Keys are **country-agnostic**: `pageTitle`, `searchPlaceholder` (takes `mode`),
  `metaDescription` take no country arg. `dataCoverage` key holds the train
  coverage notice (hidden in bus mode).

## Multi-country / multi-mode architecture
- `providers/navitia.py` is the train dispatcher. Handles France (Navitia) and
  delegates Italy calls to `providers/trenitalia.py`.
- `providers/trenitalia.py` wraps the ViaggiaTreno unofficial API. No token needed.
  Station coordinates come from the bundled `providers/data/trenitalia_stations.csv` (2963 entries);
  stations missing from the CSV are fetched live and cached in memory.
- `providers/flixbus.py` wraps the FlixBus public search API. No token needed.
  Works at **city** level (not station level). Enumerates all European cities via
  a–z autocomplete queries (26 queries, ~215 cities, no country filter), then checks
  each as a destination in parallel (`ThreadPoolExecutor(max_workers=10)`). Results
  are cached in memory under a single `"europe"` key per server session (~2 s cold start).
- `/api/stations` fans out to **all** `TRAIN_COUNTRIES` concurrently via `asyncio.gather`;
  missing tokens skip a country silently; each result is tagged with a `country` field
  (`"fr"`, `"it"`, or `"eu"` for bus). No `country` query param is accepted.
- `/api/connections/stream` and `/api/connections` still accept `?country=` (derived
  from `station.country` set client-side at autocomplete time).
- Adding a new country: extend `TRAIN_COUNTRIES` in `main.py` only.
- Frontend persists `selectedMode` in `localStorage`. Switching mode clears the map
  and resets the UI. **No `selectedCountry` in the frontend** — country comes from
  `station.country` set when the user picks a suggestion.

## FlixBus specifics
- Base URL: `https://global.api.flixbus.com/`
- City autocomplete: `search/autocomplete/cities?q=<q>&lang=en&flixbus_cities_only=true`
- Route search: `search/service/v4/search?from_city_id=<id>&to_city_id=<id>&departure_date=<DD.MM.YYYY>&...`
- Intermediate stops endpoint (`/search/service/v4/rides/<id>/stops`) requires auth (403) —
  **not used**.
- `route_paths` for bus mode is built from the bundled GTFS lookup first, falling
  back to city-level legs from the search response when GTFS has no entry.
- GTFS lookup file: `providers/data/flixbus_stops.db.gz` (~13 MB compressed, ~238 MB SQLite).
  At startup `_init_gtfs()` decompresses it once to a `NamedTemporaryFile` and keeps a
  `sqlite3.Connection` open — no full dict load, memory usage near zero.
  Schema: `stops(dep TEXT, arr TEXT, t TEXT, s TEXT)` + index on `(dep, arr)`.
  `dep`/`arr` are station UUIDs; `t` is the GTFS first-stop departure "HH:MM"; `s` is a
  JSON array of `[id, name, lat, lon, "HH:MM"]` entries.
  Every `(stop_i, stop_j)` sub-pair within each GTFS trip is indexed (i < j), so
  mid-route boarding **and** mid-route alighting are both handled.
- **GTFS time correction:** GTFS schedules drift from the live API over time.
  `_lookup_stops` corrects for this by computing `shift = live_dep_time − gtfs_first_stop_time`
  and applying that offset to every intermediate stop's time. Origin always matches
  the live API exactly; small residual drift may remain at the final stop.
- Trips keyed by `(dep_iso, arr_station_id)` in `get_direct_connections` so that
  buses departing at the same time but serving different destination stations (e.g.
  Frankfurt city centre vs Frankfurt Airport) each get their own route_path.
- `providers.flixbus.httpx.get` must be mocked in tests (not `httpx.get` directly).

## Scheduled times in route_paths
- Each `route_path` carries top-level `departure_time` and `arrival_time` (HH:MM)
  shown in the sidebar route summary.
- Each stop dict carries `departure_time` (HH:MM) shown next to the stop name in
  the sidebar accordion.
- **Train mode (Navitia):** per-stop `departure_time` is extracted from the
  route schedule table column chosen as representative for that trip.
- **Bus mode (FlixBus / GTFS):** per-stop `departure_time` comes from `stop_times.txt`
  in the GTFS feed, shifted by the live API offset (see above). City-level fallback
  stops (no GTFS entry) only have times on the first and last stop.

## GTFS rebuild
- Script: `scripts/rebuild_flixbus_stops.py`
- Downloads the latest FlixBus GTFS feed from MobilityData
  (`de-unknown-flixbus-gtfs-853`), indexes all `(stop_i, stop_j)` sub-pairs with
  per-stop departure times, and writes `providers/data/flixbus_stops.db.gz`.
- Run: `uv run python3 scripts/rebuild_flixbus_stops.py` (~10 s, requires internet).

## Italy / ViaggiaTreno specifics
- Base URL: `http://www.viaggiatreno.it/infomobilita/resteasy/viaggiatreno/`
- Station search: `cercaStazione/<QUERY>` → JSON list with `id`, `nomeLungo`, `nomeBreve`
- Departures: `partenze/<STATION_ID>/<DATE_STR>` where DATE_STR is
  `"Mon May 04 2026 10:00:00 GMT+0100"` (URL-encoded). Returns trains for the next ~3h window.
- Train stops: `andamentoTreno/<ORIGIN_ID>/<TRAIN_NUM>/<TIMESTAMP_MS>` → `fermate[]`
  — stops have **no lat/lon** in this response; coords come from the CSV.
- Station coords fallback: `regione/<STATION_ID>` → int → `dettaglioStazione/<ID>/<INT>` → lat/lon
- Train categories included: `FR, FA, FB, IC, ICN, EC, EN, REG, RV`

## Frontend file responsibilities

| File | Owns |
|------|------|
| `i18n.js` | `TRANSLATIONS`, `t()`, `setLang()`, `currentLang` |
| `map.js` | Leaflet `map` instance, tile layer |
| `sidebar.js` | Mobile sheet states (closed/peek/open), drag gesture, `isMobile()`, `setSidebar/open/peek/closeSidebar()`, `sidebarState()` |
| `autocomplete.js` | Station search input (`input`), debounce, suggestion dropdown, `cleanStationName()`, flag rendering (`FLAGS` map), `station.country` propagation |
| `routes.js` | `renderConnections(origin, paths, conns, mode)`, `addRoute(origin, path)`, `selectRoute()`, `deselectRoute()`, `activateStop()`, `_activateBusConn()`, `clearMap()`, `originMarker`, `originIcon`, `connList`, `connCount`, `busMarkers` |
| `app.js` | Entry point — `selectStation()`, `onStationSelected()`, `showStatus()`, `setProgress()`, `hideProgress()`, `applyLang()`, `selectedStation`, `selectedMode`, `dateInput`, `status`, mode toggle wiring, `#data-coverage` visibility |

## Critical: script load order in index.html
The files communicate through shared globals and **must** be loaded in this
exact order:
```
i18n.js → map.js → sidebar.js → autocomplete.js → routes.js → app.js
```
The `<div id="data-coverage">` must appear in the DOM **before** the scripts so
`applyLang()` can populate it on first load.

## Key architectural decisions
- **No module system:** all inter-file communication is via globals on `window`.
  This keeps the setup zero-config (no bundler, no `type="module"` CORS issues
  with the FastAPI static file server).
- **No country selector:** country is inferred from `station.country` (set at
  autocomplete time). The `/api/stations` fan-out tags each result with its source
  country. `selectStation()` reads `station.country` and passes it to the stream URL.
- **Pan-Europe initial map view:** `map.setView([48, 10], 5)` on startup instead of
  a per-country centre.
- **SSE streaming:** the `/api/connections/stream` endpoint emits `progress` events
  (one per route/train) and a final `done` event with the full payload. The frontend
  never polls; it just listens. Each `route` SSE event carries `{ route_path, connection }`.
- **`addRoute(origin, path)`:** appends a single route incrementally to the map and
  sidebar; called per `route` SSE event. `renderConnections` is still used as a
  fallback for dot-only bus rendering when no `route_paths` are available.
- **Bus mode uses polylines when route_paths available:** `renderConnections()` draws
  polylines for bus routes just like trains when `route_paths` is non-empty. Falls back
  to `_renderBusConnections()` (dot markers) only when `route_paths` is empty.
- **Hit polylines (train only):** each route has two overlapping polylines — a thin
  visible one (weight 3) and a transparent fat one (weight 20) used purely as a
  touch/click target. Do not remove the hit polyline.
- **Markers lazy-added (train only):** stop markers are created during
  `renderConnections()` but only added to the map when a route is selected.
- **Bus markers eager-added:** bus destination dots are added to the map immediately
  in `_renderBusConnections()` and removed in `clearMap()`.
- **Re-entrancy guard `_changingRoute`:** prevents the accordion `toggle` event
  (fired when `selectRoute()` programmatically opens a `<details>`) from
  recursively calling `selectRoute()` again.
- **Origin coords patched client-side:** `app.js` overwrites `path.stops[0].lat/lon`
  from the selected `station` object only when the first stop has missing coords or
  matches the origin station id — GTFS first stops are not overwritten.
- **`#data-coverage` element:** fixed-position label showing train country coverage.
  Hidden in bus mode (`dataCoverageEl.hidden = true`) via `applyLang()`, which is
  called on every mode switch.

## API endpoints
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/stations?q=<query>&mode=train` | Autocomplete station/city names (fans out to all countries) |
| `GET` | `/api/connections/stream?station_id=<id>&country=fr&mode=train&date=<YYYYMMDD>` | SSE stream of direct connections |
| `GET` | `/api/connections?station_id=<id>&country=fr&mode=train&date=<YYYYMMDD>` | Non-streaming equivalent (same payload) |

- `country` on connection endpoints: `"fr"`, `"it"`, or `"eu"` — derived from `station.country` client-side.
- `mode` defaults to `"train"`; pass `"bus"` for FlixBus.
- `date` is optional. When omitted the backend queries from the current moment.
- `/api/stations` does **not** accept a `country` param — always fans out to all `TRAIN_COUNTRIES`.

## Environment
- Requires `.env` with `SNCF_API_TOKEN=<uuid>` for France trains. Copy from `.env.example`.
- Italy trains and bus mode require **no token**.
- Run locally: `uv run uvicorn main:app --reload`
- Install dev deps: `uv sync --extra dev` (adds `pytest-asyncio`)
- Run tests (no token needed, fully mocked):
  `uv run pytest -v`
- Refresh FlixBus GTFS stops: `uv run python3 scripts/rebuild_flixbus_stops.py`
- Deployed on Render (free tier — cold starts after inactivity).

## Known constraints / gotchas
- `providers.navitia` wraps `httpx.Client` in a `_LoggingClient`. Tests must mock
  `providers.navitia.httpx.Client` (not `httpx.Client` directly).
- `providers.trenitalia` uses `httpx.get()` directly. Mock `providers.trenitalia.httpx.get`.
- `providers.flixbus` uses `httpx.get()` directly. Mock `providers.flixbus.httpx.get`.
- The ViaggiaTreno API is unofficial and undocumented by Trenitalia; it could
  change without notice. The date format for `partenze` must be the JS-style
  `"Mon May 04 2026 10:00:00 GMT+0100"` string, URL-encoded.
- `providers/data/trenitalia_stations.csv` is from a 2015 dump; newer stations (e.g. Napoli Afragola)
  are missing and fetched live. Stations with `N/A` coordinates are skipped.
- FlixBus intermediate stops endpoint (`/search/service/v4/rides/<id>/stops`) requires
  auth (403) — **not used**. Intermediate stop coords and times come from the bundled
  GTFS snapshot instead.
- FlixBus GTFS times are static and drift from live schedules. `_lookup_stops` applies
  a constant shift equal to `live_dep_time − gtfs_first_stop_time` to all stops in
  the sequence. This corrects systematic offset but cannot fix genuine schedule changes.
- FlixBus city cache is built once per server session via a–z queries (no country filter).
  Cold start adds ~2 s on first bus search.
- `providers/data/flixbus_stops.db.gz` (~13 MB compressed, ~238 MB SQLite): decompressed
  once at startup into a temp file; queried via `sqlite3` on demand. Each stop entry in
  the `s` column is `[id, name, lat, lon, "HH:MM"]`. Run `rebuild_flixbus_stops.py` to regenerate.
- Render free tier spins down after inactivity (~30 s cold start).
- OSM tile servers have a usage policy — do not change `maxZoom` above 19 or
  remove the attribution.
- `tile.openstreetmap.fr/osmfr` returns 404 for many tiles — do not use it.
- Wikimedia Maps tiles are blocked by Chrome CORB — do not use them.
- CARTO tiles render country names in English regardless of UI language — this
  is why we switched to OSM.
