from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    name: str | None = None


class PlanRequest(BaseModel):
    origin: Point
    destination: Point
    depart_at: datetime | None = Field(None, description="Leave at this local time; defaults to now")
    arrive_by: datetime | None = Field(None, description="Arrive no later than this local time (instead of depart_at)")
    walk_access: bool = Field(True, description="Allow walking from the origin to a stop")
    park_ride: bool = Field(False, description="Allow driving to a park & ride lot")
    max_walk_m: float = Field(800, ge=50, le=5000, description="Max walk for each walking leg")
    max_drive_m: float = Field(8000, ge=500, le=80000, description="Max drive to a park & ride lot")
    max_transfers: int = Field(2, ge=0, le=4)
    num_itineraries: int = Field(3, ge=1, le=8, description="Per access mode")

    @model_validator(mode="after")
    def _one_time(self):
        if self.depart_at is not None and self.arrive_by is not None:
            raise ValueError("Give depart_at or arrive_by, not both")
        return self


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


class VehicleOut(BaseModel):
    trip_id: str
    route_id: str | None
    lat: float
    lon: float
    bearing: float | None = None
    timestamp: int | None = None
    label: str | None = None
    occupancy: str | None = None

    @classmethod
    def from_vehicle(cls, v) -> "VehicleOut":
        return cls(trip_id=v.trip_id, route_id=v.route_id, lat=v.lat, lon=v.lon, bearing=v.bearing,
                   timestamp=v.timestamp, label=v.label, occupancy=v.occupancy)


class AlertOut(BaseModel):
    id: str
    header: str
    description: str
    url: str | None = None
    route_ids: list[str]

    @classmethod
    def from_alert(cls, a) -> "AlertOut":
        return cls(id=a.id, header=a.header, description=a.description, url=a.url, route_ids=list(a.route_ids))


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
    # Realtime (bus legs only)
    realtime: bool = False
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    delay_s: int | None = Field(None, description="Predicted minus scheduled departure; + is late")
    vehicle: VehicleOut | None = None
    alerts: list[AlertOut] = []


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


class RealtimeStatus(BaseModel):
    enabled: bool
    stale: bool = False
    last_update: datetime | None = None
    feed_time: datetime | None = None
    trips_with_predictions: int = 0
    canceled_trips: int = 0
    vehicles: int = 0
    alerts: int = 0
    error: str | None = None


class ScheduleStatus(BaseModel):
    auto_download: bool
    last_download: datetime | None = None
    last_reload: datetime | None = None
    error: str | None = None


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
    realtime: RealtimeStatus
    water_data: bool
    schedule: ScheduleStatus
