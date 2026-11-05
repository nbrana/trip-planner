"""Load a static GTFS feed into a compact, RAPTOR-friendly timetable.

Trips are grouped into *patterns* (identical ordered stop sequences on the same
route). Each pattern stores dense (trips x stops) arrival/departure matrices in
seconds after service-day midnight (values may exceed 24h, per GTFS).
"""

from __future__ import annotations

import hashlib
import io
import logging
import pickle
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .geo import PointIndex

log = logging.getLogger(__name__)
CACHE_VERSION = 2


@dataclass
class Route:
    route_id: str
    short_name: str
    long_name: str
    color: str
    text_color: str


@dataclass
class Pattern:
    route_idx: int
    stops: np.ndarray  # (m,) int32 stop indices
    arr: np.ndarray  # (n, m) int32
    dep: np.ndarray  # (n, m) int32
    service_idx: np.ndarray  # (n,) int32
    trip_ids: list[str]
    headsigns: list[str]
    shape_id: str | None


@dataclass
class Timetable:
    stop_ids: list[str]
    stop_names: list[str]
    stop_lat: np.ndarray
    stop_lon: np.ndarray
    routes: list[Route]
    patterns: list[Pattern]
    stop_patterns: list[list[tuple[int, int]]]  # stop -> [(pattern, position)]
    transfers: list[list[tuple[int, float]]]  # stop -> [(stop, metres)] sorted by distance
    service_ids: list[str]
    weekly: dict[int, tuple[date, date, tuple[bool, ...]]]
    added: dict[date, list[int]]
    removed: dict[date, list[int]]
    shapes: dict[str, np.ndarray] = field(default_factory=dict)
    feed_start: date | None = None
    feed_end: date | None = None

    @property
    def n_stops(self) -> int:
        return len(self.stop_ids)

    def active_services(self, d: date) -> np.ndarray:
        mask = np.zeros(len(self.service_ids), dtype=bool)
        wd = d.weekday()
        for si, (start, end, days) in self.weekly.items():
            if start <= d <= end and days[wd]:
                mask[si] = True
        for si in self.added.get(d, ()):
            mask[si] = True
        for si in self.removed.get(d, ()):
            mask[si] = False
        return mask


# --------------------------------------------------------------------------- IO


def _member(z: zipfile.ZipFile, name: str) -> str | None:
    for n in z.namelist():
        if n == name or n.endswith("/" + name):
            return n
    return None


def _read(z: zipfile.ZipFile, name: str, cols: list[str], required: bool = True) -> pd.DataFrame | None:
    member = _member(z, name)
    if member is None:
        if required:
            raise FileNotFoundError(f"GTFS feed is missing {name}")
        return None
    with z.open(member) as f:
        df = pd.read_csv(
            io.TextIOWrapper(f, encoding="utf-8-sig"),
            dtype=str,
            usecols=lambda c: c.strip() in cols,
            keep_default_na=False,
        )
    df.columns = [c.strip() for c in df.columns]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df


def _parse_times(s: pd.Series) -> np.ndarray:
    parts = s.str.strip().str.split(":", n=2, expand=True)
    if parts.shape[1] < 3:
        return np.full(len(s), np.nan)
    h = pd.to_numeric(parts[0], errors="coerce")
    m = pd.to_numeric(parts[1], errors="coerce")
    sec = pd.to_numeric(parts[2], errors="coerce")
    return (h * 3600 + m * 60 + sec).to_numpy(dtype=float)


def _ymd(s: str) -> date:
    return datetime.strptime(s.strip(), "%Y%m%d").date()


def _color(c: str, default: str) -> str:
    c = (c or "").strip().lstrip("#")
    return f"#{c}" if len(c) == 6 else default


# ------------------------------------------------------------------------ build


def _build(path: Path, max_transfer_m: float) -> Timetable:
    log.info("Parsing GTFS feed %s", path)
    with zipfile.ZipFile(path) as z:
        stops = _read(z, "stops.txt", ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type"])
        routes = _read(z, "routes.txt", ["route_id", "route_short_name", "route_long_name", "route_color", "route_text_color"])
        trips = _read(z, "trips.txt", ["route_id", "service_id", "trip_id", "trip_headsign", "shape_id"])
        st = _read(z, "stop_times.txt", ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"])
        cal = _read(z, "calendar.txt", ["service_id", "start_date", "end_date", "monday", "tuesday", "wednesday",
                                         "thursday", "friday", "saturday", "sunday"], required=False)
        cd = _read(z, "calendar_dates.txt", ["service_id", "date", "exception_type"], required=False)
        shapes_df = _read(z, "shapes.txt", ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"], required=False)

    # Stops (platforms/stops only, not stations or entrances)
    stops = stops[stops.location_type.str.strip().isin(["", "0"])].copy()
    stops["lat"] = pd.to_numeric(stops.stop_lat, errors="coerce")
    stops["lon"] = pd.to_numeric(stops.stop_lon, errors="coerce")
    stops = stops.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    stop_pos = pd.Series(np.arange(len(stops)), index=stops.stop_id)

    # Routes
    routes = routes.reset_index(drop=True)
    route_pos = pd.Series(np.arange(len(routes)), index=routes.route_id)
    route_objs = [
        Route(r.route_id, r.route_short_name.strip(), r.route_long_name.strip(),
              _color(r.route_color, "#2563eb"), _color(r.route_text_color, "#ffffff"))
        for r in routes.itertuples()
    ]

    # Services
    service_ids = sorted(set(trips.service_id))
    svc_pos = {s: i for i, s in enumerate(service_ids)}
    weekly: dict[int, tuple[date, date, tuple[bool, ...]]] = {}
    added: dict[date, list[int]] = {}
    removed: dict[date, list[int]] = {}
    all_dates: list[date] = []
    if cal is not None:
        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        for r in cal.itertuples():
            if r.service_id not in svc_pos:
                continue
            start, end = _ymd(r.start_date), _ymd(r.end_date)
            weekly[svc_pos[r.service_id]] = (start, end, tuple(getattr(r, d).strip() == "1" for d in days))
            all_dates += [start, end]
    if cd is not None:
        for r in cd.itertuples():
            if r.service_id not in svc_pos:
                continue
            d = _ymd(r.date)
            all_dates.append(d)
            (added if r.exception_type.strip() == "1" else removed).setdefault(d, []).append(svc_pos[r.service_id])

    # Stop times
    st["stop_sequence"] = pd.to_numeric(st.stop_sequence, errors="coerce")
    st["stop_idx"] = st.stop_id.map(stop_pos)
    st = st.dropna(subset=["stop_idx", "stop_sequence"])
    st = st.sort_values(["trip_id", "stop_sequence"], kind="stable").reset_index(drop=True)
    arr = _parse_times(st.arrival_time)
    dep = _parse_times(st.departure_time)
    arr = np.where(np.isnan(arr), dep, arr)
    dep = np.where(np.isnan(dep), arr, dep)
    if np.isnan(arr).any():  # non-timepoint stops: interpolate within each trip
        tmp = pd.DataFrame({"t": st.trip_id, "a": arr, "d": dep})
        tmp[["a", "d"]] = tmp.groupby("t")[["a", "d"]].transform(lambda s: s.interpolate(limit_area="inside"))
        arr, dep = tmp.a.to_numpy(), tmp.d.to_numpy()
    bad_trips = set(st.trip_id[np.isnan(arr) | np.isnan(dep)])
    if bad_trips:
        keep = ~st.trip_id.isin(bad_trips).to_numpy()
        st, arr, dep = st[keep].reset_index(drop=True), arr[keep], dep[keep]
    arr = arr.astype(np.int32)
    dep = dep.astype(np.int32)
    stop_idx = st.stop_idx.to_numpy(dtype=np.int32)
    tid = st.trip_id.to_numpy()
    starts = np.concatenate([[0], np.flatnonzero(tid[1:] != tid[:-1]) + 1, [len(tid)]]) if len(tid) else np.array([0])

    trip_info = {
        r.trip_id: (r.route_id, r.service_id, r.trip_headsign.strip(), r.shape_id.strip())
        for r in trips.drop_duplicates("trip_id").itertuples()
    }
    route_lookup = route_pos.to_dict()

    groups: dict[tuple[int, bytes], list[tuple[int, int, int]]] = {}
    trip_meta: list[tuple[str, int, str, str]] = []
    for k in range(len(starts) - 1):
        s, e = int(starts[k]), int(starts[k + 1])
        if e - s < 2:
            continue
        trip_id = tid[s]
        info = trip_info.get(trip_id)
        if info is None:
            continue
        ri = route_lookup.get(info[0])
        si = svc_pos.get(info[1])
        if ri is None or si is None:
            continue
        trip_meta.append((trip_id, si, info[2], info[3]))
        groups.setdefault((int(ri), stop_idx[s:e].tobytes()), []).append((len(trip_meta) - 1, s, e))

    patterns: list[Pattern] = []
    for (ri, key), members in groups.items():
        A = np.stack([arr[s:e] for _, s, e in members])
        D = np.stack([dep[s:e] for _, s, e in members])
        order = np.argsort(D[:, 0], kind="stable")
        metas = [trip_meta[members[i][0]] for i in order]
        patterns.append(Pattern(
            route_idx=ri,
            stops=np.frombuffer(key, dtype=np.int32).copy(),
            arr=A[order], dep=D[order],
            service_idx=np.array([m[1] for m in metas], dtype=np.int32),
            trip_ids=[m[0] for m in metas],
            headsigns=[m[2] for m in metas],
            shape_id=next((m[3] for m in metas if m[3]), None),
        ))

    n = len(stops)
    stop_patterns: list[list[tuple[int, int]]] = [[] for _ in range(n)]
    for pi, p in enumerate(patterns):
        for pos, s in enumerate(p.stops.tolist()):
            stop_patterns[s].append((pi, pos))

    lat = stops.lat.to_numpy()
    lon = stops.lon.to_numpy()
    index = PointIndex(lat, lon)
    transfers: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    for i in range(n):
        if not stop_patterns[i]:
            continue
        idx, d = index.within(lat[i], lon[i], max_transfer_m)
        transfers[i] = [(int(j), float(dd)) for j, dd in zip(idx, d) if j != i and stop_patterns[j]]

    shapes: dict[str, np.ndarray] = {}
    if shapes_df is not None and len(shapes_df):
        used = {p.shape_id for p in patterns if p.shape_id}
        sd = shapes_df[shapes_df.shape_id.isin(used)].copy()
        sd["seq"] = pd.to_numeric(sd.shape_pt_sequence, errors="coerce")
        sd["la"] = pd.to_numeric(sd.shape_pt_lat, errors="coerce")
        sd["lo"] = pd.to_numeric(sd.shape_pt_lon, errors="coerce")
        sd = sd.dropna(subset=["seq", "la", "lo"]).sort_values(["shape_id", "seq"])
        for sid, g in sd.groupby("shape_id", sort=False):
            shapes[sid] = g[["la", "lo"]].to_numpy(dtype=np.float32)

    tt = Timetable(
        stop_ids=stops.stop_id.tolist(), stop_names=[s.strip() for s in stops.stop_name],
        stop_lat=lat, stop_lon=lon, routes=route_objs, patterns=patterns,
        stop_patterns=stop_patterns, transfers=transfers, service_ids=service_ids,
        weekly=weekly, added=added, removed=removed, shapes=shapes,
        feed_start=min(all_dates) if all_dates else None,
        feed_end=max(all_dates) if all_dates else None,
    )
    log.info("Loaded %d stops, %d routes, %d patterns, %d trips",
             n, len(route_objs), len(patterns), sum(len(p.trip_ids) for p in patterns))
    return tt


def load_timetable(path: Path, cache_dir: Path, max_transfer_m: float) -> Timetable:
    if not path.exists():
        raise FileNotFoundError(f"GTFS feed not found: {path}")
    stat = path.stat()
    key = hashlib.sha1(f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{max_transfer_m}|{CACHE_VERSION}".encode()).hexdigest()[:16]
    cache = cache_dir / f"timetable-{key}.pkl"
    if cache.exists():
        try:
            with cache.open("rb") as f:
                return pickle.load(f)
        except Exception:  # stale/corrupt cache: rebuild
            log.warning("Ignoring unreadable cache %s", cache)
    tt = _build(path, max_transfer_m)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for old in cache_dir.glob("timetable-*.pkl"):
        old.unlink(missing_ok=True)
    with cache.open("wb") as f:
        pickle.dump(tt, f, protocol=pickle.HIGHEST_PROTOCOL)
    return tt
