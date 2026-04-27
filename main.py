"""FastAPI backend for the Train Map application."""

from __future__ import annotations

import os
from pathlib import Path

import json
import asyncio
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
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


@app.get("/api/connections/stream")
async def stream_connections(
    station_id: str = Query(..., description="stop_area id of the origin station"),
    date: str = Query(None, description="Date in YYYYMMDD format (default: today)"),
):
    """
    SSE endpoint — streams progress events then a final 'done' event with the data.

    Event types:
      progress  {"type": "progress", "current": int, "total": int, "message": str}
      done      {"connections": [...], "route_paths": [...], "count": int}
      error     {"detail": str}
    """
    if not sncf_client.TOKEN:

        async def _err():
            yield f"event: error\ndata: {json.dumps({'detail': 'SNCF_API_TOKEN not configured.'})}\n\n"

        return StreamingResponse(_err(), media_type="text/event-stream")

    async def generate():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def on_progress(current, total, message):
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {
                    "type": "progress",
                    "current": current,
                    "total": total,
                    "message": message,
                },
            )

        import concurrent.futures

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        future = loop.run_in_executor(
            executor,
            lambda: sncf_client.get_direct_connections(
                station_id, date=date, progress_callback=on_progress
            ),
        )

        # Drain progress events while the thread is running
        while not future.done():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.1)
                yield f"event: progress\ndata: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                pass

        # Flush any remaining queued events
        while not queue.empty():
            event = queue.get_nowait()
            yield f"event: progress\ndata: {json.dumps(event)}\n\n"

        try:
            result = await future
            payload = {
                "connections": result["connections"],
                "route_paths": result["route_paths"],
                "count": len(result["connections"]),
            }
            yield f"event: done\ndata: {json.dumps(payload)}\n\n"
        except httpx.HTTPStatusError as e:
            detail = (
                "Invalid SNCF API token." if e.response.status_code == 401 else str(e)
            )
            yield f"event: error\ndata: {json.dumps({'detail': detail})}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'detail': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


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
