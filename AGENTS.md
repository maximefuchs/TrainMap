# Agent context — Train Map

## What the app does
Interactive map of direct train connections in France. The user types a station
name, selects it from an autocomplete dropdown, and the app draws every city
reachable by a single direct train as colour-coded polylines on a Leaflet map.
A sidebar lists the routes as accordions with a stop-by-stop timeline.

## Tech stack
- **Backend:** Python / FastAPI, served by uvicorn. Managed with `uv`.
- **Data source:** SNCF / Navitia REST API (token in `.env` as `SNCF_API_TOKEN`).
- **Streaming:** results are pushed to the browser via Server-Sent Events (SSE)
  so the map updates progressively as each route is fetched.
- **Frontend:** plain HTML + CSS + vanilla JavaScript. No framework, no bundler,
  no npm. Leaflet 1.9.4 is loaded from unpkg CDN.
- **Map tiles:** OpenStreetMap standard tiles (`{s}.tile.openstreetmap.org`).
  The tile pane is darkened with `filter: brightness(0.65)` in `style.css` —
  **do not attempt a div/pane overlay**, it cannot be positioned correctly due
  to Leaflet's `transform` on `.leaflet-map-pane` creating a stacking context.
- **i18n:** custom lightweight system in `i18n.js`. Two locales: `en` and `fr`.
  Language is persisted in `localStorage`. `t(key)` is a global helper.

## Frontend file responsibilities

| File | Owns |
|------|------|
| `i18n.js` | `TRANSLATIONS`, `t()`, `setLang()`, `currentLang` |
| `map.js` | Leaflet `map` instance, tile layer |
| `sidebar.js` | Mobile sheet states (closed/peek/open), drag gesture, `isMobile()`, `setSidebar/open/peek/closeSidebar()`, `sidebarState()` |
| `autocomplete.js` | Station search input (`input`), debounce, suggestion dropdown, `cleanStationName()` |
| `routes.js` | `renderConnections()`, `selectRoute()`, `deselectRoute()`, `activateStop()`, `clearMap()`, `originMarker`, `originIcon`, `connList`, `connCount` |
| `app.js` | Entry point — `selectStation()`, `showStatus()`, `setProgress()`, `hideProgress()`, `applyLang()`, `selectedStation`, `dateInput`, `status` |

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
  events (one per route) and a final `done` event with the full payload.
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
| `GET` | `/api/stations?q=<query>` | Autocomplete station names |
| `GET` | `/api/connections/stream?station_id=<id>&date=<YYYYMMDD>` | SSE stream of direct connections |
| `GET` | `/api/connections?station_id=<id>&date=<YYYYMMDD>` | Non-streaming equivalent (same payload) |

- `date` is optional in both connection endpoints. When omitted the backend
  queries from the current moment (not midnight), so results reflect trains
  still running today.

## Environment
- Requires `.env` with `SNCF_API_TOKEN=<uuid>`. Copy from `.env.example`.
- Run locally: `uv run uvicorn main:app --reload`
- Install dev deps: `uv sync --extra dev` (adds `pytest-asyncio`)
- Run tests (no token needed, fully mocked): `uv run pytest test_sncf_client.py -v`
- Deployed on Render (free tier — cold starts after inactivity).

## Known constraints / gotchas
- The SNCF API token is required for any backend functionality. Tests mock it.
- `sncf_client` wraps `httpx.Client` in a `_LoggingClient`. Tests must mock
  `sncf_client.httpx.Client` (not `httpx.Client` directly) — see existing
  test fixtures for the correct patch target.
- Render free tier spins down after inactivity (~30 s cold start).
- OSM tile servers have a usage policy — do not change `maxZoom` above 19 or
  remove the attribution.
- `tile.openstreetmap.fr/osmfr` returns 404 for many tiles — do not use it.
- Wikimedia Maps tiles are blocked by Chrome CORB — do not use them.
- CARTO tiles render country names in English regardless of UI language — this
  is why we switched to OSM.
