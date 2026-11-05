from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .gtfs import load_timetable
from .lots import load_lots
from .osm import OSMClient
from .planner import DriveEstimate, Planner
from .schemas import GeocodeResult, Health, LotOut, PlanRequest, PlanResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("trip-planner")

state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    gtfs = s.resolved_gtfs_path
    if s.using_sample:
        log.warning("Using the synthetic SAMPLE feed. Run scripts/fetch_gtfs.py for real NJ Transit data.")
    tt = await run_in_threadpool(load_timetable, gtfs, s.cache_dir, s.max_transfer_walk_m)
    lots = load_lots(s.resolved_park_ride_path)
    state.update(settings=s, tt=tt, lots=lots, planner=Planner(tt, lots, s), osm=OSMClient(s))
    yield
    await state["osm"].close()


app = FastAPI(title="NJ Transit Bus Trip Planner", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins,
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health", response_model=Health)
def health():
    s, tt = state["settings"], state["tt"]
    return Health(
        status="ok", gtfs_path=str(s.resolved_gtfs_path), using_sample_data=s.using_sample,
        stops=tt.n_stops, routes=len(tt.routes), patterns=len(tt.patterns),
        trips=sum(len(p.trip_ids) for p in tt.patterns), feed_start=tt.feed_start, feed_end=tt.feed_end,
        park_ride_lots=len(state["lots"]), use_osrm=s.use_osrm,
    )


@app.get("/api/geocode", response_model=list[GeocodeResult])
async def geocode(q: str = Query(min_length=3, max_length=200)):
    try:
        return await state["osm"].geocode(q)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Geocoding failed: {e}") from e


@app.get("/api/reverse", response_model=GeocodeResult | None)
async def reverse(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)):
    try:
        return await state["osm"].reverse(lat, lon)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Reverse geocoding failed: {e}") from e


@app.get("/api/park-rides", response_model=list[LotOut])
def park_rides(lat: float, lon: float, radius_m: float = Query(8000, gt=0, le=100000)):
    planner: Planner = state["planner"]
    return [LotOut(id=l.id, name=l.name, lat=l.lat, lon=l.lon, capacity=l.capacity, operator=l.operator,
                   distance_m=round(d, 1)) for l, d in planner.candidate_lots(lat, lon, radius_m)]


@app.post("/api/plan", response_model=PlanResponse)
async def plan(req: PlanRequest):
    s = state["settings"]
    planner: Planner = state["planner"]
    osm: OSMClient = state["osm"]
    if not req.walk_access and not req.park_ride:
        raise HTTPException(422, "Enable walking and/or park & ride access.")
    tz = ZoneInfo(s.timezone)
    depart = req.depart_at or datetime.now(tz)
    depart = depart.astimezone(tz) if depart.tzinfo else depart.replace(tzinfo=tz)

    drive: dict[str, DriveEstimate] | None = None
    if s.use_osrm and req.park_ride:
        lots = planner.candidate_lots(req.origin.lat, req.origin.lon, req.max_drive_m)[:100]
        try:
            table = await osm.drive_table((req.origin.lat, req.origin.lon), [(l.lat, l.lon) for l, _ in lots])
            drive = {l.id: DriveEstimate(t[0], int(t[1])) for (l, _), t in zip(lots, table) if t}
        except (httpx.HTTPError, KeyError, ValueError) as e:
            log.warning("OSRM table failed, using estimates: %s", e)

    result = await run_in_threadpool(planner.plan, req, depart, drive)

    if s.use_osrm:
        sem = asyncio.Semaphore(4)
        legs = [leg for it in result.itineraries for leg in it.legs
                if leg.mode in ("walk", "drive") and leg.distance_m > 30]

        async def enrich(leg):
            async with sem:
                geom = await osm.route_geometry(leg.mode, [leg.geometry[0], leg.geometry[-1]])
                if geom:
                    leg.geometry = geom

        await asyncio.gather(*(enrich(l) for l in legs))
    return result
