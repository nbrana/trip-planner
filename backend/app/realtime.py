"""NJ Transit GTFS-Realtime: trip delays/cancellations, vehicle positions, alerts.

A background task polls the GTFSG2 API and publishes immutable Snapshots. The
planner overlays a snapshot's delays onto the static schedule so routing uses
predicted times.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2 as pb

from .config import Settings
from .gtfs import Timetable

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

    def applies_to(self, day: date) -> bool:
        return self.today is not None and abs((day - self.today).days) <= 1

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
    def __init__(self, settings: Settings, tt: Timetable):
        self.s = settings
        self.tt = tt
        self.tz = ZoneInfo(settings.timezone)
        self.index = build_trip_index(tt)
        self.snapshot = Snapshot()
        self.error: str | None = None
        self._token: str | None = None
        self._token_file = settings.cache_dir / "njt_token.json"
        self._alerts_at = 0.0
        self._http = httpx.AsyncClient(timeout=30, headers={"User-Agent": settings.user_agent})
        self._task: asyncio.Task | None = None

    @property
    def base(self) -> str:
        return self.s.njt_bus_api_base.rstrip("/")

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        await self._http.aclose()

    # NJT limits authentication calls, so the token is reused and cached on disk.
    def _load_token(self) -> str | None:
        try:
            data = json.loads(self._token_file.read_text())
            return data["token"] if data.get("base") == self.base else None
        except (OSError, ValueError, KeyError):
            return None

    async def _authenticate(self) -> str:
        r = await self._http.post(f"{self.base}/authenticateUser",
                                  files={"username": (None, self.s.njt_username), "password": (None, self.s.njt_password)})
        r.raise_for_status()
        body = r.json()
        token = body.get("UserToken")
        if not token or str(body.get("Authenticated", "")).lower() != "true":
            raise RuntimeError(f"NJ Transit authentication failed: {body.get('errorMessage') or body}")
        self._token_file.parent.mkdir(parents=True, exist_ok=True)
        self._token_file.write_text(json.dumps({"base": self.base, "token": token}))
        log.info("Authenticated with NJ Transit realtime API")
        return token

    async def _feed(self, endpoint: str) -> pb.FeedMessage:
        for attempt in range(2):
            if self._token is None:
                self._token = self._load_token() if attempt == 0 else None
                if self._token is None:
                    self._token = await self._authenticate()
            r = await self._http.post(f"{self.base}/{endpoint}", files={"token": (None, self._token)})
            content = r.content
            if r.is_success and not content.lstrip()[:1] in (b"{", b"<", b"["):
                msg = pb.FeedMessage()
                try:
                    msg.ParseFromString(content)
                    return msg
                except DecodeError:
                    pass
            log.info("%s rejected (HTTP %s); re-authenticating", endpoint, r.status_code)
            self._token = None
        raise RuntimeError(f"{endpoint}: could not fetch a valid feed (HTTP {r.status_code}): {r.text[:200]}")

    async def refresh(self) -> None:
        trips_msg = await self._feed("getTripUpdates")
        veh_msg = await self._feed("getVehiclePositions")
        alerts = self.snapshot.alerts_by_route
        if time.monotonic() - self._alerts_at >= self.s.alerts_poll_s:
            alerts = parse_alerts(await self._feed("getAlerts"))
            self._alerts_at = time.monotonic()
        delays, canceled = await asyncio.to_thread(parse_trip_updates, self.tt, self.index, trips_msg, self.tz)
        self.snapshot = Snapshot(
            version=self.snapshot.version + 1, today=datetime.now(self.tz).date(),
            delays=delays, canceled=canceled, vehicles=parse_vehicles(veh_msg), alerts_by_route=alerts,
            feed_time=trips_msg.header.timestamp or None, fetched_at=time.time(),
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
