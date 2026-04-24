# Train Map

Interactive map of direct train connections in France. Pick any station, and the app shows every city reachable by a single direct train — along with travel time, number of trains per day, and first/last departure times.

Data is fetched live from the [SNCF / Navitia API](https://numerique.sncf.com/startup/api/).

## How it works

1. You type a city name — the backend queries the Navitia `places` endpoint for matching stations.
2. Once you select a station, the backend fetches all train routes passing through it (`stop_areas/{id}/routes`), then pulls the full-day timetable for each route (`routes/{id}/route_schedules`).
3. From the timetables it computes, for every downstream stop:
   - **Travel time** — minimum across all trips that day
   - **Frequency** — number of direct trains per day
   - **First / last departure** — from the origin station
4. Results are displayed on a Leaflet map with colour-coded markers (green = fast, purple = slow) and a sortable sidebar list.

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
# Edit .env and replace "your_token_here" with your real token:
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
├── sncf_client.py        # SNCF/Navitia API logic (search + connections)
├── main.py               # FastAPI app (two endpoints: /api/stations, /api/connections)
├── test_sncf_client.py   # Unit tests with mocked HTTP responses
├── static/
│   └── index.html        # Frontend: Leaflet map + autocomplete + sidebar
├── pyproject.toml
├── requirements.txt
├── .env                  # Your secret token (git-ignored)
└── .env.example          # Token template
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/stations?q=<query>` | Autocomplete station names |
| `GET` | `/api/connections?station_id=<id>` | Direct connections from a station |
