# Agent context — Train Map

## What the app does
Interactive map of direct train and bus connections in France and Italy. The user
picks a country and transport mode (Train / Bus), types a city or station name,
selects it from an autocomplete dropdown, picks a date, and clicks Search. The app
then draws every city reachable by a single direct service as colour-coded
polylines (train) or dot markers (bus) on a Leaflet map. A sidebar lists the
routes/destinations.

## Tech stack
- **Backend:** Python / FastAPI, served by uvicorn. Managed with `uv`.
- **Data sources:**
  - France trains: Navitia REST API at `api.sncf.com/v1/coverage/sncf` (token `SNCF_API_TOKEN`)
  - Italy trains: ViaggiaTreno API at `viaggiatreno.it/infomobilita/resteasy/viaggiatreno` (**no token required**)
  - Bus (FR + IT): FlixBus public API at `global.api.flixbus.com` (**no token required**)
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
  Keys `pageTitle`, `searchPlaceholder` (takes `country, mode`), `metaDescription`
  take `country` as their first arg; `searchPlaceholder` also takes `mode`.

## Multi-country / multi-mode architecture
- `navitia_client.py` is the train dispatcher. Handles France (Navitia) and
  delegates Italy calls to `trenitalia_client.py`.
- `trenitalia_client.py` wraps the ViaggiaTreno unofficial API. No token needed.
  Station coordinates come from the bundled `trenitalia_stations.csv` (2963 entries);
  stations missing from the CSV are fetched live and cached in memory.
- `flixbus_client.py` wraps the FlixBus public search API. No token needed.
  Works at **city** level (not station level). Enumerates all cities for the country
  by querying autocomplete a–z (26 single-letter queries), then checks each as a
  destination in parallel (`ThreadPoolExecutor(max_workers=10)`). Results are cached
  in memory per session (~200 major cities, ~2 s cold start).
- All API endpoints accept `?country=fr|it` and `?mode=train|bus`.
- Frontend persists `selectedCountry` and `selectedMode` in `localStorage`.
  Switching either clears the map and resets the UI.

## FlixBus specifics
- Base URL: `https://global.api.flixbus.com/`
- City autocomplete: `search/autocomplete/cities?q=<q>&lang=en&country=<cc>&flixbus_cities_only=true`
- Route search: `search/service/v4/search?from_city_id=<id>&to_city_id=<id>&departure_date=<DD.MM.YYYY>&...`
- Intermediate stops endpoint (`/search/service/v4/rides/<id>/stops`) requires auth (403) —
  **not used**. Bus mode builds `route_paths` from `legs[]` in the search response.
- `route_paths` for bus mode is built from legs city IDs resolved via the city cache.
  Each leg's `departure.city_id` and `arrival.city_id` are looked up in the cache to
  get coordinates. **Intermediate stops** are resolved from the bundled `flixbus_stops.json`
  GTFS lookup: `(dep_station_id, arr_station_id)` → ordered stop list with coordinates.
  Falls back to city-level leg endpoints when GTFS has no entry for the pair.
  The frontend draws polylines when `route_paths` is non-empty (same rendering path as trains).
- City city cache is populated once per country per server session (a–z queries, ~2 s).
- `flixbus_client.httpx.get` must be mocked in tests (not `httpx.get` directly).

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
| `autocomplete.js` | Station search input (`input`), debounce, suggestion dropdown, `cleanStationName()` |
| `routes.js` | `renderConnections(origin, paths, conns, mode)`, `selectRoute()`, `deselectRoute()`, `activateStop()`, `_activateBusConn()`, `clearMap()`, `originMarker`, `originIcon`, `connList`, `connCount`, `busMarkers` |
| `app.js` | Entry point — `selectStation()`, `onStationSelected()`, `showStatus()`, `setProgress()`, `hideProgress()`, `applyLang()`, `selectedStation`, `selectedCountry`, `selectedMode`, `dateInput`, `status`, mode toggle wiring |

## Critical: script load order in index.html
The files communicate through shared globals and **must** be loaded in this
exact order:
```
i18n.js → map.js → sidebar.js → autocomplete.js → routes.js → app.js
```

## Key architectural decisions
- **No module system:** all inter-file communication is via globals on `window`.
  This keeps the setup zero-config (no bundler, no `type="module"` CORS issues
  with the FastAPI static file server).
- **SSE streaming:** the `/api/connections/stream` endpoint emits `progress`
  events (one per route/train) and a final `done` event with the full payload.
  The frontend never polls; it just listens.
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

## API endpoints
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/stations?q=<query>&country=fr&mode=train` | Autocomplete station/city names |
| `GET` | `/api/connections/stream?station_id=<id>&country=fr&mode=train&date=<YYYYMMDD>` | SSE stream of direct connections |
| `GET` | `/api/connections?station_id=<id>&country=fr&mode=train&date=<YYYYMMDD>` | Non-streaming equivalent (same payload) |

- `country` defaults to `"fr"` on all endpoints; pass `"it"` for Italy.
- `mode` defaults to `"train"`; pass `"bus"` for FlixBus.
- `date` is optional. When omitted the backend queries from the current moment.

## Environment
- Requires `.env` with `SNCF_API_TOKEN=<uuid>` for France trains. Copy from `.env.example`.
- Italy trains and bus mode require **no token**.
- Run locally: `uv run uvicorn main:app --reload`
- Install dev deps: `uv sync --extra dev` (adds `pytest-asyncio`)
- Run tests (no token needed, fully mocked):
  `uv run pytest test_navitia_client.py test_flixbus_client.py -v`
- Deployed on Render (free tier — cold starts after inactivity).

## Known constraints / gotchas
- `navitia_client` wraps `httpx.Client` in a `_LoggingClient`. Tests must mock
  `navitia_client.httpx.Client` (not `httpx.Client` directly).
- `trenitalia_client` uses `httpx.get()` directly. Mock `trenitalia_client.httpx.get`.
- `flixbus_client` uses `httpx.get()` directly. Mock `flixbus_client.httpx.get`.
- The ViaggiaTreno API is unofficial and undocumented by Trenitalia; it could
  change without notice. The date format for `partenze` must be the JS-style
  `"Mon May 04 2026 10:00:00 GMT+0100"` string, URL-encoded.
- `trenitalia_stations.csv` is from a 2015 dump; newer stations (e.g. Napoli Afragola)
  are missing and fetched live. Stations with `N/A` coordinates are skipped.
- FlixBus intermediate stops endpoint (`/search/service/v4/rides/<id>/stops`) requires auth (403) —
  **not used**. Bus route paths are reconstructed from the `legs[]` array in the search
  response. Each leg's `departure.city_id` and `arrival.city_id` are resolved to
  coordinates via the city cache. Only city-level stops are available (no bus stops within cities).
- FlixBus city cache is built once per server session via a–z (26) single-letter queries (no country filter). Cold start adds ~2 s on first bus search.
- Render free tier spins down after inactivity (~30 s cold start).
- OSM tile servers have a usage policy — do not change `maxZoom` above 19 or
  remove the attribution.
- `tile.openstreetmap.fr/osmfr` returns 404 for many tiles — do not use it.
- Wikimedia Maps tiles are blocked by Chrome CORB — do not use them.
- CARTO tiles render country names in English regardless of UI language — this
  is why we switched to OSM.

## Tech stack
- **Backend:** Python / FastAPI, served by uvicorn. Managed with `uv`.
- **Data sources:**
  - France: Navitia REST API at `api.sncf.com/v1/coverage/sncf` (token `SNCF_API_TOKEN`)
  - Italy: ViaggiaTreno API at `viaggiatreno.it/infomobilita/resteasy/viaggiatreno` (**no token required**)
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
  Some keys (`pageTitle`, `searchPlaceholder`, `metaDescription`) take `country`
  as their first argument to produce country-specific strings.

## Multi-country architecture
- `navitia_client.py` is the dispatcher. It handles France (Navitia) directly
  and delegates Italy calls to `trenitalia_client.py`.
- `trenitalia_client.py` wraps the ViaggiaTreno unofficial API. No token needed.
  Station coordinates come from the bundled `trenitalia_stations.csv` (2963 entries);
  stations missing from the CSV are fetched live from the `regione` + `dettaglioStazione`
  endpoints and cached in memory.
- All API endpoints accept `?country=fr` (default) or `?country=it`.
- Frontend persists `selectedCountry` in `localStorage`; switching country
  clears the map and re-centres it (`fr` → `[46.5, 2.5]` z6, `it` → `[42.5, 12.5]` z6).

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
| `autocomplete.js` | Station search input (`input`), debounce, suggestion dropdown, `cleanStationName()` |
| `routes.js` | `renderConnections()`, `selectRoute()`, `deselectRoute()`, `activateStop()`, `clearMap()`, `originMarker`, `originIcon`, `connList`, `connCount` |
| `app.js` | Entry point — `selectStation()`, `showStatus()`, `setProgress()`, `hideProgress()`, `applyLang()`, `selectedStation`, `selectedCountry`, `dateInput`, `status` |

## Critical: script load order in index.html
The files communicate through shared globals and **must** be loaded in this
exact order:
```
i18n.js → map.js → sidebar.js → autocomplete.js → routes.js → app.js
```

## Key architectural decisions
- **No module system:** all inter-file communication is via globals on `window`.
  This keeps the setup zero-config (no bundler, no `type="module"` CORS issues
  with the FastAPI static file server).
- **SSE streaming:** the `/api/connections/stream` endpoint emits `progress`
  events (one per route/train) and a final `done` event with the full payload.
  The frontend never polls; it just listens.
- **Hit polylines:** each route has two overlapping polylines — a thin visible
  one (weight 3) and a transparent fat one (weight 20) used purely as a
  touch/click target. Do not remove the hit polyline.
- **Markers lazy-added:** stop markers are created during `renderConnections()`
  but only added to the map when a route is selected, to avoid clutter.
- **Re-entrancy guard `_changingRoute`:** prevents the accordion `toggle` event
  (fired when `selectRoute()` programmatically opens a `<details>`) from
  recursively calling `selectRoute()` again.

## API endpoints
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/stations?q=<query>&country=fr` | Autocomplete station names |
| `GET` | `/api/connections/stream?station_id=<id>&country=fr&date=<YYYYMMDD>` | SSE stream of direct connections |
| `GET` | `/api/connections?station_id=<id>&country=fr&date=<YYYYMMDD>` | Non-streaming equivalent (same payload) |

- `country` defaults to `"fr"` on all endpoints; pass `"it"` for Italy.
- `date` is optional. When omitted the backend queries from the current moment
  (not midnight), so results reflect trains still running today.

## Environment
- Requires `.env` with `SNCF_API_TOKEN=<uuid>` for France. Copy from `.env.example`.
- Italy requires **no token** — uses ViaggiaTreno.
- Run locally: `uv run uvicorn main:app --reload`
- Install dev deps: `uv sync --extra dev` (adds `pytest-asyncio`)
- Run tests (no token needed, fully mocked): `uv run pytest test_navitia_client.py -v`
- Deployed on Render (free tier — cold starts after inactivity).

## Known constraints / gotchas
- `navitia_client` wraps `httpx.Client` in a `_LoggingClient`. Tests must mock
  `navitia_client.httpx.Client` (not `httpx.Client` directly).
- `trenitalia_client` uses `httpx.get()` directly (not a context-manager client).
  Mock `trenitalia_client.httpx.get` in tests, or mock at the `navitia_client.trenitalia_client` level.
- The ViaggiaTreno API is unofficial and undocumented by Trenitalia; it could
  change without notice. The date format for `partenze` must be the JS-style
  `"Mon May 04 2026 10:00:00 GMT+0100"` string, URL-encoded.
- `trenitalia_stations.csv` is from a 2015 dump; newer stations (e.g. Napoli Afragola)
  are missing and fetched live. Stations with `N/A` coordinates are skipped.
- Render free tier spins down after inactivity (~30 s cold start).
- OSM tile servers have a usage policy — do not change `maxZoom` above 19 or
  remove the attribution.
- `tile.openstreetmap.fr/osmfr` returns 404 for many tiles — do not use it.
- Wikimedia Maps tiles are blocked by Chrome CORB — do not use them.
- CARTO tiles render country names in English regardless of UI language — this
  is why we switched to OSM.
