"""FastAPI backend for the Train Map application."""

from __future__ import annotations

import json
import asyncio
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import navitia_client

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


def _check_token(country: str) -> None:
    """
    Raise HTTPException 503 if a token is required but not configured.
    Italy uses ViaggiaTreno (no token needed) so it always passes.
    """
    if country == "it":
        return  # ViaggiaTreno requires no token
    token = navitia_client.get_token(country)
    if not token:
        cfg = navitia_client.COUNTRY_CONFIG.get(country, {})
        env_var = cfg.get("token_env", "?")
        raise HTTPException(
            status_code=503,
            detail=f"API token not configured for country '{country}'. Add {env_var} to .env file.",
        )


@app.get("/api/stations")
async def search_stations(
    q: str = Query(..., min_length=2, description="Search query"),
    country: str = Query("fr", description="Country code: 'fr' or 'it'"),
):
    """Autocomplete station names. Returns list of {id, name, lat, lon}."""
    _check_token(country)
    try:
        results = navitia_client.search_stations(q, count=10, country=country)
        return {"stations": results}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Cannot reach API: {e}")


@app.get("/api/connections/stream")
async def stream_connections(
    station_id: str = Query(..., description="stop_area id of the origin station"),
    date: str = Query(None, description="Date in YYYYMMDD format (default: today)"),
    country: str = Query("fr", description="Country code: 'fr' or 'it'"),
):
    """
    SSE endpoint — streams progress events then a final 'done' event with the data.

    Event types:
      progress  {"type": "progress", "current": int, "total": int, "message": str}
      done      {"connections": [...], "route_paths": [...], "count": int}
      error     {"detail": str}
    """
    try:
        _check_token(country)
    except HTTPException as e:
        async def _err():
            yield f"event: error\ndata: {json.dumps({'detail': e.detail})}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    async def generate():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def on_progress(current, total, message):
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "progress", "current": current, "total": total, "message": message},
            )

        import concurrent.futures
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        future = loop.run_in_executor(
            executor,
            lambda: navitia_client.get_direct_connections(
                station_id, date=date, progress_callback=on_progress, country=country
            ),
        )

        while not future.done():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.1)
                yield f"event: progress\ndata: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                pass

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
            detail = "Invalid API token." if e.response.status_code == 401 else str(e)
            yield f"event: error\ndata: {json.dumps({'detail': detail})}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'detail': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/connections")
async def get_connections(
    station_id: str = Query(..., description="stop_area id of the origin station"),
    date: str = Query(None, description="Date in YYYYMMDD format (default: today)"),
    country: str = Query("fr", description="Country code: 'fr' or 'it'"),
):
    """Return all stations directly reachable from *station_id*."""
    _check_token(country)
    try:
        result = navitia_client.get_direct_connections(station_id, date=date, country=country)
        return {
            "connections": result["connections"],
            "route_paths": result["route_paths"],
            "count": len(result["connections"]),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise HTTPException(status_code=401, detail="Invalid API token.")
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Cannot reach API: {e}")
