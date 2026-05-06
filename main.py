"""FastAPI backend for the Train Map application."""

from __future__ import annotations

import json
import asyncio
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from providers import navitia as navitia_client
from providers import flixbus as flixbus_client

app = FastAPI(title="Train Map API")

# Countries supported for train mode — add new entries here to extend coverage.
# Each entry: (country_code, has_token_requirement)
TRAIN_COUNTRIES = ["fr", "it"]

# Serve static frontend files
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main HTML page."""
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text())


def _check_token(country: str, mode: str = "train") -> None:
    """
    Raise HTTPException 503 if a token is required but not configured.
    Italy (ViaggiaTreno) and bus mode (FlixBus) require no token.
    """
    if mode == "bus":
        return  # FlixBus requires no token
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
    mode: str = Query("train", description="Transport mode: 'train' or 'bus'"),
):
    """
    Autocomplete station/city names across all supported countries.
    Returns list of {id, name, lat, lon, country}.
    For bus mode, queries FlixBus (Europe-wide, country='eu').
    For train mode, fans out to all TRAIN_COUNTRIES concurrently, tagging
    each result with its source country. Countries with missing tokens are
    silently skipped rather than failing the whole request.
    """
    if mode == "bus":
        try:
            results = flixbus_client.search_cities(q)
            for r in results:
                r["country"] = "eu"
            return {"stations": results}
        except Exception as e:
            raise HTTPException(status_code=503, detail=str(e))

    # Train mode: fan out to all countries concurrently
    async def _search_country(country: str) -> list[dict]:
        try:
            _check_token(country, "train")
        except HTTPException:
            return []  # token missing — skip silently
        try:
            loop = asyncio.get_event_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as ex:
                results = await loop.run_in_executor(
                    ex,
                    lambda: navitia_client.search_stations(q, count=8, country=country),
                )
            for r in results:
                r["country"] = country
            return results
        except Exception:
            return []

    results_per_country = await asyncio.gather(
        *[_search_country(c) for c in TRAIN_COUNTRIES]
    )

    # Merge, dedup by id, preserve per-country order
    seen: set[str] = set()
    merged: list[dict] = []
    for country_results in results_per_country:
        for r in country_results:
            if r["id"] not in seen:
                seen.add(r["id"])
                merged.append(r)

    return {"stations": merged}



@app.get("/api/connections/stream")
async def stream_connections(
    station_id: str = Query(..., description="stop_area id of the origin station"),
    date: str = Query(None, description="Date in YYYYMMDD format (default: today)"),
    country: str = Query("fr", description="Country code: 'fr' or 'it'"),
    mode: str = Query("train", description="Transport mode: 'train' or 'bus'"),
):
    """
    SSE endpoint — streams progress events then a final 'done' event with the data.

    Event types:
      progress  {"type": "progress", "current": int, "total": int, "message": str}
      route     {"connection": {...}, "route_path": {...}}   (bus mode only, one per route)
      done      {"connections": [...], "route_paths": [...], "count": int}
      error     {"detail": str}
    """
    try:
        _check_token(country, mode)
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

        def on_route(connection, route_path):
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "route", "connection": connection, "route_path": route_path},
            )

        import concurrent.futures
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        if mode == "bus":
            future = loop.run_in_executor(
                executor,
                lambda: flixbus_client.get_direct_connections(
                    station_id, date=date, country=country,
                    progress_callback=on_progress, route_callback=on_route,
                ),
            )
        else:
            future = loop.run_in_executor(
                executor,
                lambda: navitia_client.get_direct_connections(
                    station_id, date=date, progress_callback=on_progress,
                    route_callback=on_route, country=country,
                ),
            )

        while not future.done():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.1)
                if event["type"] == "route":
                    yield f"event: route\ndata: {json.dumps(event)}\n\n"
                else:
                    yield f"event: progress\ndata: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                pass

        while not queue.empty():
            event = queue.get_nowait()
            if event["type"] == "route":
                yield f"event: route\ndata: {json.dumps(event)}\n\n"
            else:
                yield f"event: progress\ndata: {json.dumps(event)}\n\n"

        try:
            result = await future
            payload = {
                "connections": result["connections"],
                "route_paths":  result["route_paths"],
                "count":        len(result["connections"]),
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
    mode: str = Query("train", description="Transport mode: 'train' or 'bus'"),
):
    """Return all stations directly reachable from *station_id*."""
    _check_token(country, mode)
    try:
        if mode == "bus":
            result = flixbus_client.get_direct_connections(station_id, date=date, country=country)
        else:
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
