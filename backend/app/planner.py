"""RAPTOR-based transit planner with walk and park & ride access.

Access legs (origin -> first stop) are modelled as a set of starting stops each
with a fixed access duration: either a walk, or a drive to a park & ride lot
plus a short walk to a nearby stop. Each access mode is searched separately so
the user sees the best options for each.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import numpy as np

from .config import Settings
from .geo import PointIndex, haversine_m
from .gtfs import Pattern, Timetable
from .lots import ParkRideLot
from .realtime import Snapshot
from .water import WaterIndex
from .schemas import (AlertOut, Itinerary, Leg, Place, PlanRequest, PlanResponse, RouteInfo, StopVisit,
                      VehicleOut)

INF = 1 << 40
DAY = 86400


@dataclass(frozen=True)
class AccessOption:
    stop: int
    duration_s: int
    kind: str  # "walk" | "park_ride"
    walk_m: float
    drive_m: float = 0.0
    drive_s: int = 0
    lot: ParkRideLot | None = None


@dataclass(frozen=True)
class EgressOption:
    stop: int
    duration_s: int
    walk_m: float


@dataclass(frozen=True)
class DriveEstimate:
    distance_m: float
    duration_s: int


@dataclass
class _BusLeg:
    pattern: int
    trip: int  # row in the DayView arrays
    board_pos: int
    alight_pos: int


@dataclass
class _TransferLeg:
    src: int
    dst: int
    dist_m: float
    start: int
    end: int


@dataclass
class _Journey:
    access: AccessOption
    legs: list[_BusLeg | _TransferLeg]
    egress: EgressOption
    arrival: int


@dataclass
class PatternDay:
    """A pattern's trips usable on one service day; times relative to that day's midnight."""
    arr: np.ndarray  # (n, m) predicted (realtime) or scheduled
    dep: np.ndarray
    sched_arr: np.ndarray
    sched_dep: np.ndarray
    rows: np.ndarray  # (n,) row in Pattern arrays
    svc_offset: np.ndarray  # (n,) 0 = this day's service, -1 = previous day's (after-midnight trip)
    live: np.ndarray  # (n,) bool: has realtime predictions


class DayView:
    """Trips of each pattern that run on a service day (incl. yesterday's after-midnight trips),
    with realtime delays and cancellations applied when a snapshot covers that day."""

    def __init__(self, tt: Timetable, day: date, rt: Snapshot | None = None):
        self.tt = tt
        self.day = day
        self.rt = rt if rt is not None and rt.applies_to(day) else None
        self.today = tt.active_services(day)
        self.yday = tt.active_services(day - timedelta(days=1))
        self._cache: dict[int, PatternDay] = {}

    def trips(self, pi: int) -> PatternDay:
        hit = self._cache.get(pi)
        if hit is not None:
            return hit
        p = self.tt.patterns[pi]
        t_idx = np.flatnonzero(self.today[p.service_idx])
        y_idx = np.flatnonzero(self.yday[p.service_idx] & (p.arr[:, -1] >= DAY))
        rows = np.concatenate([t_idx, y_idx])
        offs = np.concatenate([np.zeros(len(t_idx), np.int64), np.full(len(y_idx), -DAY, np.int64)])
        rt = self.rt
        if rt is not None and rt.canceled:
            yday = self.day - timedelta(days=1)
            keep = np.array([
                not rt.is_canceled(p.trip_ids[r], self.day if off == 0 else yday, bool(p.arr[r, -1] >= DAY))
                for r, off in zip(rows.tolist(), offs.tolist())
            ], dtype=bool)
            rows, offs = rows[keep], offs[keep]
        sched_arr = p.arr[rows].astype(np.int64) + offs[:, None]
        sched_dep = p.dep[rows].astype(np.int64) + offs[:, None]
        arr, dep = sched_arr, sched_dep
        live = np.zeros(len(rows), dtype=bool)
        if rt is not None and rt.delays:
            yday = self.day - timedelta(days=1)
            for i, (r, off) in enumerate(zip(rows.tolist(), offs.tolist())):
                d = rt.delays.get((p.trip_ids[r], self.day if off == 0 else yday))
                if d is None:
                    continue
                if not live.any():
                    arr, dep = sched_arr.copy(), sched_dep.copy()
                a = np.maximum.accumulate(sched_arr[i] + d.arr)
                arr[i] = a
                dep[i] = np.maximum.accumulate(np.maximum(sched_dep[i] + d.dep, a))
                live[i] = True
        self._cache[pi] = PatternDay(arr, dep, sched_arr, sched_dep, rows, offs // DAY, live)
        return self._cache[pi]


def _drop_dominated(its: list[Itinerary]) -> list[Itinerary]:
    """Remove options another option matches or beats on leave time, arrival and transfers
    (e.g. same first bus but a later arrival). Exact ties keep the first."""
    def beats(a: Itinerary, b: Itinerary) -> bool:
        return a.start >= b.start and a.end <= b.end and a.transfers <= b.transfers

    keep = []
    for i, it in enumerate(its):
        if not any(beats(o, it) and (not beats(it, o) or j < i) for j, o in enumerate(its) if j != i):
            keep.append(it)
    return keep


class _Direction:
    """Pattern access for RAPTOR. reverse=True presents a time-reversed timetable (stop order reversed,
    times negated) so the same forward search finds the *latest departure* that meets a deadline."""

    def __init__(self, tt: Timetable, view: DayView, reverse: bool):
        self.tt, self.view, self.reverse = tt, view, reverse
        self._cache: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}

    def positions(self, s: int):
        if not self.reverse:
            return self.tt.stop_patterns[s]
        pats = self.tt.patterns
        return [(pi, len(pats[pi].stops) - 1 - pos) for pi, pos in self.tt.stop_patterns[s]]

    def pattern(self, pi: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(stops, arr, dep) in search order."""
        hit = self._cache.get(pi)
        if hit is None:
            pd = self.view.trips(pi)
            stops = self.tt.patterns[pi].stops
            hit = (stops[::-1], -pd.dep[:, ::-1], -pd.arr[:, ::-1]) if self.reverse else (stops, pd.arr, pd.dep)
            self._cache[pi] = hit
        return hit


class Planner:
    def __init__(self, tt: Timetable, lots: list[ParkRideLot], settings: Settings, water: WaterIndex | None = None):
        self.tt = tt
        self.lots = lots
        self.s = settings
        self.water = water
        self.stop_index = PointIndex(tt.stop_lat, tt.stop_lon)
        self.lot_index = PointIndex([l.lat for l in lots], [l.lon for l in lots])
        self._views: dict[tuple[date, int], DayView] = {}
        self.realtime_snapshot: Callable[[], Snapshot | None] = lambda: None

    # ------------------------------------------------------------- helpers

    def _view(self, day: date) -> DayView:
        snap = self.realtime_snapshot()
        key = (day, snap.version if snap is not None and snap.applies_to(day) else -1)
        v = self._views.get(key)
        if v is None:
            if len(self._views) > 8:
                self._views.clear()
            v = self._views[key] = DayView(self.tt, day, snap)
        return v

    def walk_s(self, metres: float) -> int:
        return int(math.ceil(metres / self.s.walk_speed_mps))

    def estimate_drive(self, straight_m: float) -> DriveEstimate:
        m = straight_m * self.s.drive_detour
        return DriveEstimate(m, int(math.ceil(m / self.s.drive_speed_mps)))

    def _stop_place(self, s: int) -> Place:
        return Place(name=self.tt.stop_names[s], lat=float(self.tt.stop_lat[s]),
                     lon=float(self.tt.stop_lon[s]), stop_id=self.tt.stop_ids[s])

    def candidate_lots(self, lat: float, lon: float, max_drive_m: float) -> list[tuple[ParkRideLot, float]]:
        idx, d = self.lot_index.within(lat, lon, max_drive_m)
        return [(self.lots[i], float(dd)) for i, dd in zip(idx.tolist(), d.tolist())]

    # -------------------------------------------------------- access/egress

    def walkable(self, lat1: float, lon1: float, lat2: float, lon2: float) -> bool:
        """False if the straight walking line crosses wide water (a river or bay with no footbridge)."""
        return self.water is None or self.water.water_m(lat1, lon1, lat2, lon2) <= self.s.max_walk_water_m

    def _walkable_stop(self, lat: float, lon: float, s: int) -> bool:
        return self.walkable(lat, lon, float(self.tt.stop_lat[s]), float(self.tt.stop_lon[s]))

    def walk_access(self, lat: float, lon: float, max_walk_m: float) -> dict[int, AccessOption]:
        idx, d = self.stop_index.within(lat, lon, max_walk_m / self.s.walk_detour)
        out = {}
        for s, dist in zip(idx.tolist(), d.tolist()):
            if self.tt.stop_patterns[s] and self._walkable_stop(lat, lon, s):
                m = dist * self.s.walk_detour
                out[s] = AccessOption(s, self.walk_s(m), "walk", m)
        return out

    def park_ride_access(self, lat: float, lon: float, max_drive_m: float,
                         drive: dict[str, DriveEstimate] | None = None) -> dict[int, AccessOption]:
        out: dict[int, AccessOption] = {}
        for lot, straight in self.candidate_lots(lat, lon, max_drive_m):
            est = (drive or {}).get(lot.id) or self.estimate_drive(straight)
            if est.distance_m > max_drive_m:
                continue
            sidx, sd = self.stop_index.within(lot.lat, lot.lon, self.s.park_ride_walk_m / self.s.walk_detour)
            for s, dist in zip(sidx.tolist(), sd.tolist()):
                if not self.tt.stop_patterns[s] or not self._walkable_stop(lot.lat, lot.lon, s):
                    continue
                wm = dist * self.s.walk_detour
                dur = est.duration_s + self.s.park_buffer_s + self.walk_s(wm)
                prev = out.get(s)
                if prev is None or dur < prev.duration_s:
                    out[s] = AccessOption(s, dur, "park_ride", wm, est.distance_m, est.duration_s, lot)
        return out

    def egress(self, lat: float, lon: float, max_walk_m: float) -> dict[int, EgressOption]:
        idx, d = self.stop_index.within(lat, lon, max_walk_m / self.s.walk_detour)
        out = {}
        for s, dist in zip(idx.tolist(), d.tolist()):
            if self.tt.stop_patterns[s] and self._walkable_stop(lat, lon, s):
                m = dist * self.s.walk_detour
                out[s] = EgressOption(s, self.walk_s(m), m)
        return out

    # --------------------------------------------------------------- RAPTOR

    def _raptor(self, dirn: _Direction, access: dict, egress: dict,
                t0: int, max_rounds: int, transfer_m: float) -> list[_Journey]:
        """Earliest-arrival RAPTOR. `access`/`egress` map stop -> option with .duration_s."""
        tt = self.tt
        slack = self.s.min_transfer_s
        best: dict[int, int] = {}
        # labels[k][stop] = (arrival, parent)
        labels: list[dict[int, tuple[int, tuple]]] = [{}]
        for s, a in access.items():
            t = t0 + a.duration_s
            labels[0][s] = (t, ("access", a))
            best[s] = t
        marked = set(access)
        best_target = INF
        found: list[tuple[int, int]] = []  # (round, egress stop)

        def latest(k: int, s: int):
            for r in range(k, -1, -1):
                lab = labels[r].get(s)
                if lab is not None:
                    return lab, r
            return None, -1

        for k in range(1, max_rounds + 1):
            cur: dict[int, tuple[int, tuple]] = {}
            labels.append(cur)
            queue: dict[int, int] = {}
            for s in marked:
                for pi, pos in dirn.positions(s):
                    if pos < queue.get(pi, 1 << 30):
                        queue[pi] = pos
            improved: set[int] = set()

            for pi, pos0 in queue.items():
                stops, arr, dep = dirn.pattern(pi)
                if len(dep) == 0:
                    continue
                trip = -1
                board: tuple[int, int, int] | None = None
                for pos in range(pos0, len(stops)):
                    s = int(stops[pos])
                    if trip >= 0:
                        a = int(arr[trip, pos])
                        if a < best.get(s, INF) and a < best_target:
                            cur[s] = (a, ("trip", pi, trip, board[0], board[1], board[2], pos))
                            best[s] = a
                            improved.add(s)
                    lab, r = latest(k - 1, s)
                    if lab is None:
                        continue
                    thr = lab[0] + (slack if lab[1][0] == "trip" else 0)
                    if trip >= 0 and thr >= dep[trip, pos]:
                        continue
                    col = dep[:, pos]
                    cand = np.where(col >= thr, col, INF)
                    et = int(np.argmin(cand))
                    if cand[et] >= INF:
                        continue
                    if trip < 0 or col[et] < dep[trip, pos]:
                        trip, board = et, (s, pos, r)

            # Foot transfers from stops reached by bus this round
            trip_labels = {s: cur[s] for s in improved}
            for s, lab in trip_labels.items():
                for s2, dist in tt.transfers[s]:
                    if dist > transfer_m:
                        break
                    t = lab[0] + self.walk_s(dist * self.s.walk_detour)
                    if t < best.get(s2, INF) and t < best_target:
                        cur[s2] = (t, ("walk", s, dist * self.s.walk_detour, lab))
                        best[s2] = t
                        improved.add(s2)

            round_best = None
            for s in improved:
                e = egress.get(s)
                if e is not None:
                    t = cur[s][0] + e.duration_s
                    if t < best_target:
                        best_target, round_best = t, s
            if round_best is not None:
                found.append((k, round_best))
            marked = improved
            if not marked:
                break

        journeys = []
        for k, s in found:
            legs: list[_BusLeg | _TransferLeg] = []
            lab = labels[k][s]
            stop = s
            while True:
                t, p = lab
                if p[0] == "access":
                    acc = p[1]
                    break
                if p[0] == "walk":
                    _, src, dist, src_lab = p
                    legs.append(_TransferLeg(src, stop, dist, src_lab[0], t))
                    stop, lab = src, src_lab
                else:
                    _, pi, trip, bs, bpos, bround, apos = p
                    legs.append(_BusLeg(pi, trip, bpos, apos))
                    stop, lab = bs, labels[bround][bs]
            legs.reverse()
            journeys.append(_Journey(acc, legs, egress[s], labels[k][s][0] + egress[s].duration_s))
        return journeys

    # ------------------------------------------------------------ itineraries

    def _bus_geometry(self, p: Pattern, bpos: int, apos: int) -> list[tuple[float, float]]:
        lat, lon = self.tt.stop_lat, self.tt.stop_lon
        pts = [(float(lat[s]), float(lon[s])) for s in p.stops[bpos:apos + 1].tolist()]
        shp = self.tt.shapes.get(p.shape_id) if p.shape_id else None
        if shp is not None and len(shp) > 1:
            k = math.cos(math.radians(pts[0][0]))

            def nearest(arr: np.ndarray, pt) -> int:
                return int(np.argmin((arr[:, 0] - pt[0]) ** 2 + ((arr[:, 1] - pt[1]) * k) ** 2))

            i0 = nearest(shp, pts[0])
            i1 = i0 + nearest(shp[i0:], pts[-1])
            if i1 > i0:
                return [(round(float(a), 6), round(float(b), 6)) for a, b in shp[i0:i1 + 1]]
        return pts

    def _to_itinerary(self, view: DayView, j: _Journey, midnight: datetime,
                      origin: Place, dest: Place) -> Itinerary:
        ts = lambda sec: midnight + timedelta(seconds=int(sec))  # noqa: E731
        tt = self.tt
        legs: list[Leg] = []
        first = j.legs[0]
        assert isinstance(first, _BusLeg)
        board_t = int(view.trips(first.pattern).dep[first.trip, first.board_pos])
        acc = j.access
        leave = board_t - acc.duration_s
        first_stop = self._stop_place(acc.stop)

        if acc.kind == "walk":
            legs.append(Leg(mode="walk", start=ts(leave), end=ts(leave + acc.duration_s),
                            from_place=origin, to_place=first_stop, distance_m=acc.walk_m,
                            geometry=[(origin.lat, origin.lon), (first_stop.lat, first_stop.lon)]))
        else:
            lot = acc.lot
            lot_place = Place(name=lot.name, lat=lot.lat, lon=lot.lon)
            parked = leave + acc.drive_s + self.s.park_buffer_s
            legs.append(Leg(mode="drive", start=ts(leave), end=ts(leave + acc.drive_s),
                            from_place=origin, to_place=lot_place, distance_m=acc.drive_m,
                            geometry=[(origin.lat, origin.lon), (lot.lat, lot.lon)]))
            legs.append(Leg(mode="walk", start=ts(parked), end=ts(parked + self.walk_s(acc.walk_m)),
                            from_place=lot_place, to_place=first_stop, distance_m=acc.walk_m,
                            geometry=[(lot.lat, lot.lon), (first_stop.lat, first_stop.lon)]))

        walk_m = acc.walk_m
        moving = acc.duration_s - (self.s.park_buffer_s if acc.kind == "park_ride" else 0)
        n_bus = 0
        for leg in j.legs:
            if isinstance(leg, _BusLeg):
                n_bus += 1
                p = tt.patterns[leg.pattern]
                pdy = view.trips(leg.pattern)
                arr, dep = pdy.arr, pdy.dep
                row = int(pdy.rows[leg.trip])
                r = tt.routes[p.route_idx]
                stops = p.stops.tolist()
                b, a = leg.board_pos, leg.alight_pos
                t_b, t_a = int(dep[leg.trip, b]), int(arr[leg.trip, a])
                moving += t_a - t_b
                inter = [StopVisit(name=tt.stop_names[stops[i]], lat=float(tt.stop_lat[stops[i]]),
                                   lon=float(tt.stop_lon[stops[i]]), time=ts(arr[leg.trip, i]))
                         for i in range(b + 1, a)]
                dist = sum(haversine_m(tt.stop_lat[stops[i]], tt.stop_lon[stops[i]],
                                       tt.stop_lat[stops[i + 1]], tt.stop_lon[stops[i + 1]]) for i in range(b, a))
                trip_id = p.trip_ids[row]
                live = bool(pdy.live[leg.trip])
                snap = view.rt
                veh = snap.vehicles.get(trip_id) if snap is not None and (live or view.day == snap.today) else None
                legs.append(Leg(
                    mode="bus", start=ts(t_b), end=ts(t_a),
                    from_place=self._stop_place(stops[b]), to_place=self._stop_place(stops[a]),
                    distance_m=dist, geometry=self._bus_geometry(p, b, a),
                    route=RouteInfo(id=r.route_id, short_name=r.short_name, long_name=r.long_name,
                                    color=r.color, text_color=r.text_color),
                    headsign=p.headsigns[row] or None, trip_id=trip_id, intermediate_stops=inter,
                    realtime=live,
                    scheduled_start=ts(pdy.sched_dep[leg.trip, b]), scheduled_end=ts(pdy.sched_arr[leg.trip, a]),
                    delay_s=t_b - int(pdy.sched_dep[leg.trip, b]) if live else None,
                    vehicle=VehicleOut.from_vehicle(veh) if veh else None,
                    alerts=[AlertOut.from_alert(al) for al in snap.route_alerts(r.route_id)] if snap else [],
                ))
            else:
                walk_m += leg.dist_m
                moving += leg.end - leg.start
                f, t = self._stop_place(leg.src), self._stop_place(leg.dst)
                legs.append(Leg(mode="walk", start=ts(leg.start), end=ts(leg.end), from_place=f, to_place=t,
                                distance_m=leg.dist_m, geometry=[(f.lat, f.lon), (t.lat, t.lon)]))

        e = j.egress
        last = self._stop_place(e.stop)
        t_end = j.arrival
        legs.append(Leg(mode="walk", start=ts(t_end - e.duration_s), end=ts(t_end), from_place=last,
                        to_place=dest, distance_m=e.walk_m, geometry=[(last.lat, last.lon), (dest.lat, dest.lon)]))
        walk_m += e.walk_m
        moving += e.duration_s
        duration = t_end - leave
        return Itinerary(
            access_mode=acc.kind, start=ts(leave), end=ts(t_end), duration_s=duration,
            transfers=max(0, n_bus - 1), walk_m=round(walk_m, 1), drive_m=round(acc.drive_m, 1),
            wait_s=max(0, duration - moving - (self.s.park_buffer_s if acc.kind == "park_ride" else 0)),
            park_ride_lot=acc.lot.name if acc.lot else None, legs=legs,
        )

    def _unreverse(self, view: DayView, j: _Journey) -> _Journey:
        """Turn a journey found on the reversed timetable back into forward order."""
        legs: list[_BusLeg | _TransferLeg] = []
        for leg in reversed(j.legs):
            if isinstance(leg, _BusLeg):
                m = len(self.tt.patterns[leg.pattern].stops)
                legs.append(_BusLeg(leg.pattern, leg.trip, m - 1 - leg.alight_pos, m - 1 - leg.board_pos))
            else:
                # Walk right after getting off the previous bus.
                prev = legs[-1]
                start = int(view.trips(prev.pattern).arr[prev.trip, prev.alight_pos])
                legs.append(_TransferLeg(leg.dst, leg.src, leg.dist_m, start, start + self.walk_s(leg.dist_m)))
        last = legs[-1]
        egress = j.access  # the reversed search started from the destination side
        arrival = int(view.trips(last.pattern).arr[last.trip, last.alight_pos]) + egress.duration_s
        return _Journey(j.egress, legs, egress, arrival)

    def _search(self, day: date, access, egress, t: int, req: PlanRequest, origin: Place, dest: Place,
                arrive_by: bool, tz) -> list[Itinerary]:
        """Depart-at: earliest arrivals from t, then later departures.
        Arrive-by: latest departures arriving by t, then earlier ones.
        t is seconds after `day`'s midnight. If a service day runs out of trips, the search continues
        into the next day (depart-at) or the previous day (arrive-by)."""
        transfer_m = min(req.max_walk_m, self.s.max_transfer_walk_m) / self.s.walk_detour
        out: list[Itinerary] = []
        seen: set[tuple] = set()
        for hop in range(2):
            d = day - timedelta(days=hop) if arrive_by else day + timedelta(days=hop)
            shift = hop * DAY if arrive_by else -hop * DAY  # same instant on d's clock = t + shift
            view = self._view(d)
            midnight = datetime.combine(d, time(0), tzinfo=tz)
            dirn = _Direction(self.tt, view, reverse=arrive_by)
            for _ in range(req.num_itineraries * 3):
                tl = t + shift
                if arrive_by:
                    found = self._raptor(dirn, egress, access, -tl, req.max_transfers + 1, transfer_m)
                    journeys = [self._unreverse(view, j) for j in found]
                else:
                    journeys = self._raptor(dirn, access, egress, tl, req.max_transfers + 1, transfer_m)
                if not journeys:
                    break
                nxt = -INF if arrive_by else INF
                for j in journeys:
                    bus = [l for l in j.legs if isinstance(l, _BusLeg)]
                    if arrive_by:  # next search: must arrive strictly earlier
                        nxt = max(nxt, j.arrival - 60)
                    else:  # next search: leave just after this option's latest leave time
                        first = view.trips(bus[0].pattern)
                        nxt = min(nxt, int(first.dep[bus[0].trip, bus[0].board_pos]) - j.access.duration_s + 60)
                    # Same trip on the same service date = same option, whichever day's view found it.
                    key = tuple((l.pattern, int(view.trips(l.pattern).rows[l.trip]),
                                 d.toordinal() + int(view.trips(l.pattern).svc_offset[l.trip])) for l in bus)
                    if key not in seen:
                        seen.add(key)
                        out.append(self._to_itinerary(view, j, midnight, origin, dest))
                out = _drop_dominated(out)
                if len(out) >= req.num_itineraries:
                    break
                t = (min(nxt, tl - 60) if arrive_by else max(nxt, tl + 60)) - shift
            if len(out) >= req.num_itineraries:
                break
        if arrive_by:
            out.sort(key=lambda i: (-i.start.timestamp(), i.duration_s))
        else:
            out.sort(key=lambda i: (i.end, i.duration_s))
        return out[: req.num_itineraries]

    def plan(self, req: PlanRequest, when: datetime, drive: dict[str, DriveEstimate] | None = None,
             arrive_by: bool = False) -> PlanResponse:
        """Plan leaving at `when`, or (arrive_by=True) arriving no later than `when`."""
        notes: list[str] = []
        midnight = datetime.combine(when.date(), time(0), tzinfo=when.tzinfo)
        t0 = int((when.replace(tzinfo=None) - midnight.replace(tzinfo=None)).total_seconds())
        depart = when
        o, d = req.origin, req.destination
        origin = Place(name=o.name or "Origin", lat=o.lat, lon=o.lon)
        dest = Place(name=d.name or "Destination", lat=d.lat, lon=d.lon)

        if self.tt.feed_start and self.tt.feed_end and not (self.tt.feed_start <= when.date() <= self.tt.feed_end):
            notes.append(f"Date is outside the schedule's range ({self.tt.feed_start} to {self.tt.feed_end}).")
        snap = self.realtime_snapshot()
        if snap is not None and snap.stale and abs((when.date() - (snap.today or when.date())).days) <= 1:
            notes.append("Live data is out of date, so times are from the schedule.")

        egress = self.egress(d.lat, d.lon, req.max_walk_m)
        itins: list[Itinerary] = []
        searched = False
        if not egress:
            notes.append("No bus stops within walking distance of the destination.")
        else:
            if req.walk_access:
                acc = self.walk_access(o.lat, o.lon, req.max_walk_m)
                if acc:
                    searched = True
                    itins += self._search(when.date(), acc, egress, t0, req, origin, dest, arrive_by, when.tzinfo)
                else:
                    notes.append("No bus stops within walking distance of the origin.")
            if req.park_ride:
                if not self.lots:
                    notes.append("No park & ride lot data is loaded (run scripts/fetch_park_rides.py).")
                else:
                    acc = self.park_ride_access(o.lat, o.lon, req.max_drive_m, drive)
                    if acc:
                        searched = True
                        itins += self._search(when.date(), acc, egress, t0, req, origin, dest, arrive_by, when.tzinfo)
                    else:
                        notes.append("No park & ride lots with nearby bus stops within driving distance.")
        if searched and not itins:
            notes.append(f"No bus trips found {'arriving by' if arrive_by else 'leaving after'} this time. "
                         "Try another time, more transfers, or longer distances.")

        direct = haversine_m(o.lat, o.lon, d.lat, d.lon) * self.s.walk_detour
        if req.walk_access and direct <= req.max_walk_m and self.walkable(o.lat, o.lon, d.lat, d.lon):
            dur = self.walk_s(direct)
            if arrive_by:
                depart = when - timedelta(seconds=dur)
            itins.append(Itinerary(
                access_mode="walk_only", start=depart, end=depart + timedelta(seconds=dur), duration_s=dur,
                transfers=0, walk_m=round(direct, 1), drive_m=0, wait_s=0,
                legs=[Leg(mode="walk", start=depart, end=depart + timedelta(seconds=dur), from_place=origin,
                          to_place=dest, distance_m=direct, geometry=[(o.lat, o.lon), (d.lat, d.lon)])],
            ))

        if arrive_by:
            itins.sort(key=lambda i: (-i.start.timestamp(), i.duration_s))
        else:
            itins.sort(key=lambda i: (i.end, i.duration_s))
        return PlanResponse(itineraries=itins, notes=notes)
