"""Regression tests for walking over water, day boundaries, notes, cancellations, stale live data
and schedule hot-reload."""

import asyncio
import math
import shutil
import time
import zipfile
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from shapely.geometry import Polygon

from app.config import SAMPLE_GTFS, SAMPLE_LOTS, Settings
from app.gtfs import load_timetable
from app.lots import load_lots
from app.planner import Planner
from app.realtime import Snapshot, TripDelay
from app.schedule import ScheduleManager
from app.schemas import PlanRequest, Point
from app.water import WaterIndex

TZ = ZoneInfo("America/New_York")
WED = date(2026, 11, 4)
WEST, EAST, UPTOWN = (40.801, -74.25), (40.801, -74.16), (40.859, -74.201)


@pytest.fixture(scope="module")
def tt(tmp_path_factory):
    return load_timetable(SAMPLE_GTFS, tmp_path_factory.mktemp("cache"), Settings(_env_file=None).max_transfer_walk_m)


@pytest.fixture
def planner(tt):
    return Planner(tt, load_lots(SAMPLE_LOTS), Settings(_env_file=None))


def at(h, m=0, day=WED):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)


def req(o, d, **kw):
    return PlanRequest(origin=Point(lat=o[0], lon=o[1]), destination=Point(lat=d[0], lon=d[1]), **kw)


def m_per_deg_lon(lat):
    return 111_320 * math.cos(math.radians(lat))


# ------------------------------------------------------------------ water

def river(west: float, east: float, south=39.0, north=42.0) -> list[np.ndarray]:
    """A north-south river as OSM coastline: sea on the right of each line's direction."""
    return [np.array([[west, south], [west, north]]),  # west bank, drawn northward: water to the east
            np.array([[east, north], [east, south]])]  # east bank, drawn southward: water to the west


def test_coastline_water_length():
    w = WaterIndex(river(-74.02, -74.01), [])
    assert w.water_m(40.0, -74.03, 40.0, -74.00) == pytest.approx(0.01 * m_per_deg_lon(40.0), rel=0.01)
    assert w.water_m(40.0, -74.00, 40.0, -74.03) == pytest.approx(0.01 * m_per_deg_lon(40.0), rel=0.01)
    assert w.water_m(40.0, -74.05, 40.1, -74.03) == 0  # stays on the west bank
    # Starting on a pier in the river: only the part in water counts.
    assert w.water_m(40.0, -74.015, 40.0, -74.00) == pytest.approx(0.005 * m_per_deg_lon(40.0), rel=0.01)


def test_lake_polygon_and_overlap_counted_once():
    lake = Polygon([(-74.02, 39.9), (-74.01, 39.9), (-74.01, 40.1), (-74.02, 40.1)])
    w = WaterIndex(river(-74.02, -74.01), [lake])  # lake exactly overlaps the river
    assert w.water_m(40.0, -74.03, 40.0, -74.00) == pytest.approx(0.01 * m_per_deg_lon(40.0), rel=0.01)
    only_lake = WaterIndex([], [lake])
    assert only_lake.water_m(40.0, -74.03, 40.0, -74.00) == pytest.approx(0.01 * m_per_deg_lon(40.0), rel=0.01)


def test_wide_river_blocks_walks_but_narrow_one_does_not(tt):
    s = Settings(_env_file=None)
    o, d = (40.801, -74.172), (40.801, -74.158)  # 1.2 km apart, ~1.5 km walk
    r = req(o, d, max_walk_m=2000)
    no_water = Planner(tt, [], s).plan(r, at(8))
    assert any(i.access_mode == "walk_only" for i in no_water.itineraries)

    wide = Planner(tt, [], s, WaterIndex(river(-74.1685, -74.1615), []))  # ~590 m wide
    res = wide.plan(r, at(8))
    assert not any(i.access_mode == "walk_only" for i in res.itineraries)
    for it in res.itineraries:  # every walk stays on its own bank
        for leg in it.legs:
            if leg.mode == "walk":
                (la1, lo1), (la2, lo2) = leg.geometry[0], leg.geometry[-1]
                assert (lo1 < -74.1685) == (lo2 < -74.1685) or (lo1 > -74.1615) == (lo2 > -74.1615)

    narrow = Planner(tt, [], s, WaterIndex(river(-74.1660, -74.1640), []))  # ~170 m: bridges exist
    assert any(i.access_mode == "walk_only" for i in narrow.plan(r, at(8)).itineraries)


# ------------------------------------------------------------- day boundaries

def test_depart_late_continues_into_next_day(planner):
    # Last S1 departures are 24:00 and 24:15 (Wed service); the third option is Thursday morning.
    res = planner.plan(req(WEST, EAST), at(23, 58))
    starts = [i.start for i in res.itineraries if i.access_mode == "walk"]
    assert len(starts) == 3
    assert starts[2].date() == date(2026, 11, 5) and starts[2].hour == 5
    trips = [next(l.trip_id for l in i.legs if l.mode == "bus") for i in res.itineraries if i.access_mode == "walk"]
    assert len(set(trips)) == 3


def test_arrive_by_after_midnight_uses_previous_day_trips(planner):
    # Arrive by Thu 00:01: the 23:30 Wed trip (ends 23:54, before midnight) is only in Wednesday's schedule.
    deadline = at(0, 1, day=date(2026, 11, 5))
    res = planner.plan(req(WEST, EAST), deadline, arrive_by=True)
    its = [i for i in res.itineraries if i.access_mode == "walk"]
    assert its and all(i.end <= deadline for i in its)
    assert next(l.trip_id for l in its[0].legs if l.mode == "bus") == "S1_0_074"


def test_same_trip_on_consecutive_days_is_not_a_duplicate(planner):
    res = planner.plan(req(WEST, EAST, num_itineraries=5), at(23, 50))
    keys = [(i.start.date(), next(l.trip_id for l in i.legs if l.mode == "bus")) for i in res.itineraries]
    assert len(keys) == len(set(keys)) == 5


# ---------------------------------------------------------------------- notes

def test_no_trips_note_even_when_other_notes_exist(planner):
    # Origin has no walkable stops; park & ride works but the destination needs 2 transfers.
    res = planner.plan(req((40.90, -74.35), UPTOWN, park_ride=True, max_drive_m=10000, max_transfers=0), at(8))
    assert not res.itineraries
    assert any("walking distance of the origin" in n for n in res.notes)
    assert any("No bus trips found" in n for n in res.notes)


# --------------------------------------------------------- realtime freshness

def _bus(res):
    return next(l for l in res.itineraries[0].legs if l.mode == "bus")


def test_cancellation_only_applies_to_today(planner):
    planner.realtime_snapshot = lambda: Snapshot(version=1, today=WED, canceled=frozenset({"S1_0_012"}))
    assert _bus(planner.plan(req(WEST, EAST), at(8))).trip_id == "S1_0_013"
    tomorrow = planner.plan(req(WEST, EAST), at(8, day=date(2026, 11, 5)))
    assert _bus(tomorrow).trip_id == "S1_0_012"


def test_stale_live_data_is_ignored(planner, tt):
    m = len(tt.patterns[0].stops)
    delays = {("S1_0_012", WED): TripDelay(np.full(m, 600), np.full(m, 600))}
    fresh = Snapshot(version=2, today=WED, delays=delays, fetched_at=time.time(), expires_at=time.time() + 300)
    planner.realtime_snapshot = lambda: fresh
    assert _bus(planner.plan(req(WEST, EAST), at(8))).realtime

    stale = Snapshot(version=3, today=WED, delays=delays, fetched_at=time.time() - 900, expires_at=time.time() - 600)
    planner.realtime_snapshot = lambda: stale
    res = planner.plan(req(WEST, EAST), at(8))
    assert not _bus(res).realtime and _bus(res).trip_id == "S1_0_012"
    assert any("out of date" in n for n in res.notes)


# ------------------------------------------------------------ schedule reload

def test_schedule_reloads_when_file_changes(tmp_path):
    s = Settings(_env_file=None, cache_dir=tmp_path / "cache")
    feed = tmp_path / "feed.zip"
    shutil.copy(SAMPLE_GTFS, feed)
    reloaded = []
    mgr = ScheduleManager(s, feed, reloaded.append, njt=None)
    asyncio.run(mgr.check())
    assert reloaded == []  # unchanged

    with zipfile.ZipFile(feed, "a") as z:  # e.g. a manual download of a new feed
        z.writestr("feed_info.txt", "feed_publisher_name\nTest\n")
    asyncio.run(mgr.check())
    assert len(reloaded) == 1 and reloaded[0].n_stops > 0
    assert mgr.last_reload is not None and mgr.error is None


def test_schedule_downloads_when_due(tmp_path):
    s = Settings(_env_file=None, cache_dir=tmp_path / "cache", gtfs_refresh_hours=24)
    feed = tmp_path / "feed.zip"
    shutil.copy(SAMPLE_GTFS, feed)

    class FakeNJT:
        calls = 0

        async def download_gtfs(self, out):
            FakeNJT.calls += 1
            return False

    mgr = ScheduleManager(s, feed, lambda tt: None, njt=FakeNJT())
    asyncio.run(mgr.check())
    asyncio.run(mgr.check())
    assert FakeNJT.calls == 1  # second check is within the refresh interval
    # A restart remembers the last download instead of fetching 50 MB again.
    assert ScheduleManager(s, feed, lambda tt: None, njt=FakeNJT()).last_download == mgr.last_download
    mgr.last_download -= 25 * 3600
    asyncio.run(mgr.check())
    assert FakeNJT.calls == 2


def test_water_relations_are_built_from_members():
    """Multipolygon relations (e.g. the tidal Delaware) need member geometry: 'out tags geom' drops it."""
    from scripts.fetch_water import QUERY, to_polygons

    assert "out tags" not in QUERY
    sq = lambda x0, y0, x1, y1: [{"lon": x, "lat": y} for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]]  # noqa: E731
    half_a = [{"lon": -75.0, "lat": 40.0}, {"lon": -74.9, "lat": 40.0}, {"lon": -74.9, "lat": 40.1}]
    half_b = [{"lon": -74.9, "lat": 40.1}, {"lon": -75.0, "lat": 40.1}, {"lon": -75.0, "lat": 40.0}]
    rel = {"type": "relation", "id": 1, "members": [
        {"type": "way", "role": "outer", "geometry": half_a},  # outer ring split across two ways
        {"type": "way", "role": "outer", "geometry": half_b},
        {"type": "way", "role": "inner", "geometry": sq(-74.96, 40.04, -74.94, 40.06)},  # island
    ]}
    (poly,) = to_polygons(rel)
    assert poly.area == pytest.approx(0.01 - 0.0004)
