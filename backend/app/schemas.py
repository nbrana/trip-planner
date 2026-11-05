from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    name: str | None = None


class PlanRequest(BaseModel):
    origin: Point
    destination: Point
    depart_at: datetime | None = Field(None, description="Local time; defaults to now")
    walk_access: bool = Field(True, description="Allow walking from the origin to a stop")
    park_ride: bool = Field(False, description="Allow driving to a park & ride lot")
    max_walk_m: float = Field(800, ge=50, le=5000, description="Max walk for each walking leg")
    max_drive_m: float = Field(8000, ge=500, le=80000, description="Max drive to a park & ride lot")
    max_transfers: int = Field(2, ge=0, le=4)
    num_itineraries: int = Field(3, ge=1, le=8, description="Per access mode")


class Place(BaseModel):
    name: str
    lat: float
    lon: float
    stop_id: str | None = None


class RouteInfo(BaseModel):
    id: str
    short_name: str
    long_name: str
    color: str
    text_color: str


class StopVisit(BaseModel):
    name: str
    lat: float
    lon: float
    time: datetime


class Leg(BaseModel):
    mode: Literal["walk", "drive", "bus"]
    start: datetime
    end: datetime
    from_place: Place
    to_place: Place
    distance_m: float
    geometry: list[tuple[float, float]]
    route: RouteInfo | None = None
    headsign: str | None = None
    trip_id: str | None = None
    intermediate_stops: list[StopVisit] = []


class Itinerary(BaseModel):
    access_mode: Literal["walk", "park_ride", "walk_only"]
    start: datetime
    end: datetime
    duration_s: int
    transfers: int
    walk_m: float
    drive_m: float
    wait_s: int
    park_ride_lot: str | None = None
    legs: list[Leg]


class PlanResponse(BaseModel):
    itineraries: list[Itinerary]
    notes: list[str] = []


class GeocodeResult(BaseModel):
    name: str
    lat: float
    lon: float


class LotOut(BaseModel):
    id: str
    name: str
    lat: float
    lon: float
    capacity: int | None = None
    operator: str | None = None
    distance_m: float


class Health(BaseModel):
    status: str
    gtfs_path: str
    using_sample_data: bool
    stops: int
    routes: int
    patterns: int
    trips: int
    feed_start: date | None
    feed_end: date | None
    park_ride_lots: int
    use_osrm: bool
