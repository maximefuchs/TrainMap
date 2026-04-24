"""FastAPI backend for the Train Map application."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

import sncf_client

app = FastAPI(title="Train Map API")

# Serve static frontend files
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main HTML page."""
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text())


@app.get("/api/stations")
async def search_stations(
    q: str = Query(..., min_length=2, description="Search query"),
):
    """Autocomplete station names. Returns list of {id, name, lat, lon}."""
    if not sncf_client.TOKEN:
        raise HTTPException(
            status_code=503,
            detail="SNCF_API_TOKEN not configured. Add your token to .env file.",
        )
    try:
        results = sncf_client.search_stations(q, count=10)
        return {"stations": results}
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Cannot reach SNCF API: {e}")


@app.get("/api/connections")
async def get_connections(
    station_id: str = Query(..., description="stop_area id of the origin station"),
    date: str = Query(None, description="Date in YYYYMMDD format (default: today)"),
):
    """
    Return all stations directly reachable from *station_id*.
    Each item: {id, name, lat, lon, duration_min, first_departure, last_departure, frequency, lines}
    """
    if not sncf_client.TOKEN:
        raise HTTPException(
            status_code=503,
            detail="SNCF_API_TOKEN not configured. Add your token to .env file.",
        )
    try:
        result = sncf_client.get_direct_connections(station_id, date=date)
        return {
            "connections": result["connections"],
            "route_paths": result["route_paths"],
            "count": len(result["connections"]),
        }
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise HTTPException(status_code=401, detail="Invalid SNCF API token.")
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Cannot reach SNCF API: {e}")
