"""NJ Transit GTFS-Realtime: trip delays/cancellations, vehicle positions, alerts.

A background task polls the GTFSG2 API and publishes immutable Snapshots. The
planner overlays a snapshot's delays onto the static schedule so routing uses
predicted times.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
from google.transit import gtfs_realtime_pb2 as pb

from .config import Settings
from .gtfs import Timetable
from .njt import NJTClient

log = logging.getLogger(__name__)

STU = pb.TripUpdate.StopTimeUpdate
OCCUPANCY = {v: k.replace("_", " ").capitalize() for k, v in pb.VehiclePosition.OccupancyStatus.items()}


@dataclass(frozen=True)
class TripDelay:
    arr: np.ndarray  # (m,) delay in seconds at each pattern position
    dep: np.ndarray


@dataclass(frozen=True)
class Vehicle:
    trip_id: str
    route_id: str | None
    lat: float
    lon: float
    bearing: float | None
    timestamp: int | None
    label: str | None
    occupancy: str | None


@dataclass(frozen=True)
class Alert:
    id: str
    header: str
    description: str
    url: str | None
    route_ids: tuple[str, ...]
    periods: tuple[tuple[int | None, int | None], ...]

    def active(self, now: float) -> bool:
        if not self.periods:
            return True
        return any((s is None or s <= now) and (e is None or now <= e) for s, e in self.periods)


@dataclass
class Snapshot:
    version: int = 0
    today: date | None = None  # local date when trip updates were fetched
    delays: dict[tuple[str, date], TripDelay] = field(default_factory=dict)
    canceled: frozenset[str] = frozenset()
    vehicles: dict[str, Vehicle] = field(default_factory=dict)
    alerts_by_route: dict[str, list[Alert]] = field(default_factory=dict)
    feed_time: int | None = None
    fetched_at: float | None = None
    expires_at: float | None = None  # after this, the data is too old to trust

    @property
    def stale(self) -> bool:
        return self.expires_at is not None and time.time() > self.expires_at

    def applies_to(self, day: date) -> bool:
        return self.today is not None and not self.stale and abs((day - self.today).days) <= 1

    def is_canceled(self, trip_id: str, service_day: date, overnight: bool) -> bool:
        """NJT's cancellations carry no date: they refer to trips running now, i.e. today's service
        or, for a trip still running after midnight, yesterday's."""
        if trip_id not in self.canceled or self.today is None:
            return False
        return service_day == self.today or (overnight and (self.today - service_day).days == 1)

    def route_alerts(self, route_id: str, now: float | None = None) -> list[Alert]:
        now = time.time() if now is None else now
        return [a for a in self.alerts_by_route.get(route_id, ()) if a.active(now)]


# ----------------------------------------------------------------- parsing


def build_trip_index(tt: Timetable) -> dict[str, tuple[int, int]]:
    return {t: (pi, row) for pi, p in enumerate(tt.patterns) for row, t in enumerate(p.trip_ids)}


def _event_delay(ev, static_t: int) -> tuple[int | None, int | None]:
    """(delay_s, service_midnight_epoch) from a StopTimeEvent."""
    delay = ev.delay if ev.HasField("delay") else None
    sched = ev.scheduled_time if ev.HasField("scheduled_time") else None
    t = ev.time if ev.HasField("time") else None
    if delay is None and t is not None and sched is not None:
        delay = t - sched
    if sched is None and t is not None and delay is not None:
        sched = t - delay
    return delay, (sched - static_t if sched is not None else None)


def _fill(a: np.ndarray) -> np.ndarray:
    """Forward-fill NaNs (delay persists downstream), then back-fill the leading gap."""
    idx = np.where(~np.isnan(a), np.arange(len(a)), 0)
    np.maximum.accumulate(idx, out=idx)
    a = a[idx]
    first = np.flatnonzero(~np.isnan(a))
    if len(first):
        a[: first[0]] = a[first[0]]
    return a


def parse_trip_updates(tt: Timetable, index: dict[str, tuple[int, int]], msg: pb.FeedMessage,
                       tz: ZoneInfo) -> tuple[dict[tuple[str, date], TripDelay], frozenset[str]]:
    delays: dict[tuple[str, date], TripDelay] = {}
    canceled: set[str] = set()
    for e in msg.entity:
        if not e.HasField("trip_update"):
            continue
        tu = e.trip_update
        tid = tu.trip.trip_id
        loc = index.get(tid)
        if loc is None:
            continue
        if tu.trip.schedule_relationship == pb.TripDescriptor.CANCELED:
            canceled.add(tid)
            continue
        p = tt.patterns[loc[0]]
        row = loc[1]
        m = len(p.stops)
        arr_d = np.full(m, np.nan)
        dep_d = np.full(m, np.nan)
        midnight = None
        for u in tu.stop_time_update:
            if u.schedule_relationship in (STU.NO_DATA, STU.SKIPPED):
                continue
            pos = u.stop_sequence - 1 if u.HasField("stop_sequence") else -1
            if not (0 <= pos < m) or (u.stop_id and tt.stop_ids[p.stops[pos]] != u.stop_id):
                pos = next((i for i in range(m) if tt.stop_ids[p.stops[i]] == u.stop_id), -1) if u.stop_id else -1
            if pos < 0:
                continue
            if u.HasField("arrival"):
                d, mid = _event_delay(u.arrival, int(p.arr[row, pos]))
                if d is not None:
                    arr_d[pos] = d
                midnight = midnight or mid
            if u.HasField("departure"):
                d, mid = _event_delay(u.departure, int(p.dep[row, pos]))
                if d is not None:
                    dep_d[pos] = d
                midnight = midnight or mid
        if midnight is None or np.isnan(arr_d).all() and np.isnan(dep_d).all():
            continue
        arr_d = np.where(np.isnan(arr_d), dep_d, arr_d)
        dep_d = np.where(np.isnan(dep_d), arr_d, dep_d)
        svc_day = datetime.fromtimestamp(midnight + 7200, tz).date()  # +2h tolerates DST "noon - 12h"
        delays[(tid, svc_day)] = TripDelay(_fill(arr_d).astype(np.int64), _fill(dep_d).astype(np.int64))
    return delays, frozenset(canceled)


def parse_vehicles(msg: pb.FeedMessage) -> dict[str, Vehicle]:
    out = {}
    for e in msg.entity:
        if not e.HasField("vehicle"):
            continue
        v = e.vehicle
        if not v.trip.trip_id or not v.HasField("position"):
            continue
        out[v.trip.trip_id] = Vehicle(
            trip_id=v.trip.trip_id, route_id=v.trip.route_id or None,
            lat=v.position.latitude, lon=v.position.longitude,
            bearing=v.position.bearing if v.position.HasField("bearing") else None,
            timestamp=v.timestamp or None, label=(v.vehicle.label or v.vehicle.id) or None,
            occupancy=OCCUPANCY.get(v.occupancy_status) if v.HasField("occupancy_status") else None,
        )
    return out


def _text(ts) -> str:
    tr = list(ts.translation)
    if not tr:
        return ""
    best = next((t for t in tr if t.language.lower().startswith("en")), tr[0])
    return best.text.strip()


def parse_alerts(msg: pb.FeedMessage) -> dict[str, list[Alert]]:
    """Route-level alerts only: NJT's stop-only alerts are station signage greetings."""
    by_route: dict[str, list[Alert]] = {}
    seen: set[tuple] = set()
    for e in msg.entity:
        if not e.HasField("alert"):
            continue
        a = e.alert
        routes = tuple(sorted({ie.route_id for ie in a.informed_entity if ie.route_id}))
        if not routes:
            continue
        alert = Alert(
            id=e.id, header=_text(a.header_text), description=_text(a.description_text),
            url=_text(a.url) or None, route_ids=routes,
            periods=tuple((p.start or None, p.end or None) for p in a.active_period),
        )
        key = (alert.header, alert.description, routes)
        if key in seen:
            continue
        seen.add(key)
        for r in routes:
            by_route.setdefault(r, []).append(alert)
    return by_route


# ------------------------------------------------------------------ client


class RealtimeClient:
    def __init__(self, settings: Settings, tt: Timetable, njt: NJTClient):
        self.s = settings
        self.tz = ZoneInfo(settings.timezone)
        self.njt = njt
        self.set_timetable(tt)
        self.snapshot = Snapshot()
        self.error: str | None = None
        self._alerts_at = 0.0
        self._task: asyncio.Task | None = None

    def set_timetable(self, tt: Timetable) -> None:
        """Called when the schedule is reloaded; the next refresh maps updates onto it."""
        self.tt = tt
        self.index = build_trip_index(tt)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()

    async def refresh(self) -> None:
        trips_msg = await self.njt.feed("getTripUpdates")
        veh_msg = await self.njt.feed("getVehiclePositions")
        alerts = self.snapshot.alerts_by_route
        if time.monotonic() - self._alerts_at >= self.s.alerts_poll_s:
            alerts = parse_alerts(await self.njt.feed("getAlerts"))
            self._alerts_at = time.monotonic()
        tt, index = self.tt, self.index
        delays, canceled = await asyncio.to_thread(parse_trip_updates, tt, index, trips_msg, self.tz)
        now = time.time()
        self.snapshot = Snapshot(
            version=self.snapshot.version + 1, today=datetime.now(self.tz).date(),
            delays=delays, canceled=canceled, vehicles=parse_vehicles(veh_msg), alerts_by_route=alerts,
            feed_time=trips_msg.header.timestamp or None, fetched_at=now,
            expires_at=now + self.s.realtime_max_age_s,
        )
        self.error = None

    async def _run(self) -> None:
        while True:
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # keep polling; surface the error in /api/health
                self.error = str(e)
                log.warning("Realtime refresh failed: %s", e)
            await asyncio.sleep(self.s.realtime_poll_s)
