from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import SAMPLE_GTFS, SAMPLE_LOTS, Settings
from app.gtfs import load_timetable
from app.lots import load_lots
from app.planner import Planner
from app.schemas import PlanRequest, Point

TZ = ZoneInfo("America/New_York")


@pytest.fixture(scope="module")
def planner(tmp_path_factory):
    s = Settings(_env_file=None)
    tt = load_timetable(SAMPLE_GTFS, tmp_path_factory.mktemp("cache"), s.max_transfer_walk_m)
    return Planner(tt, load_lots(SAMPLE_LOTS), s)


def at(h, m=0, day=4):
    return datetime(2026, 11, day, h, m, tzinfo=TZ)


def req(o, d, **kw):
    return PlanRequest(origin=Point(lat=o[0], lon=o[1]), destination=Point(lat=d[0], lon=d[1]), **kw)


def bus_routes(it):
    return [l.route.short_name for l in it.legs if l.mode == "bus"]


def test_direct_bus(planner):
    res = planner.plan(req((40.801, -74.25), (40.801, -74.16)), at(8))
    it = res.itineraries[0]
    assert bus_routes(it) == ["S1"]
    assert it.transfers == 0
    assert it.start >= at(8)
    assert it.legs[0].mode == "walk" and it.legs[-1].mode == "walk"


def test_transfer_at_shared_stop(planner):
    res = planner.plan(req((40.801, -74.25), (40.859, -74.201)), at(8))
    assert any(bus_routes(i) == ["S1", "S2"] for i in res.itineraries)
    it = next(i for i in res.itineraries if bus_routes(i) == ["S1", "S2"])
    buses = [l for l in it.legs if l.mode == "bus"]
    assert buses[1].start >= buses[0].end
    assert it.transfers == 1


def test_multiple_departures(planner):
    res = planner.plan(req((40.801, -74.25), (40.801, -74.16), num_itineraries=3), at(8))
    starts = [i.start for i in res.itineraries if i.access_mode == "walk"]
    assert len(starts) == 3 and len(set(starts)) == 3


def test_park_and_ride_reaches_stops_beyond_walk(planner):
    origin, dest = (40.90, -74.35), (40.801, -74.16)
    walk_only = planner.plan(req(origin, dest, max_walk_m=800), at(8))
    assert not walk_only.itineraries
    res = planner.plan(req(origin, dest, max_walk_m=800, park_ride=True, max_drive_m=10000), at(8))
    it = res.itineraries[0]
    assert it.access_mode == "park_ride"
    assert it.park_ride_lot == "Sample Hill Park & Ride"
    assert [l.mode for l in it.legs[:2]] == ["drive", "walk"]
    assert 0 < it.drive_m <= 10000


def test_drive_limit_respected(planner):
    res = planner.plan(req((40.90, -74.35), (40.801, -74.16), park_ride=True, max_drive_m=2000), at(8))
    assert not res.itineraries


def test_after_midnight_uses_previous_service_day(planner):
    res = planner.plan(req((40.801, -74.25), (40.801, -74.16)), at(0, 5, day=5))
    assert res.itineraries and res.itineraries[0].start.hour == 0


def test_walk_only_when_close(planner):
    res = planner.plan(req((40.80, -74.20), (40.802, -74.201)), at(8))
    assert any(i.access_mode == "walk_only" for i in res.itineraries)


# ------------------------------------------------------------------ arrive-by

WEST, EAST, UPTOWN = (40.801, -74.25), (40.801, -74.16), (40.859, -74.201)


def test_arrive_by_picks_latest_trip_that_makes_it(planner):
    deadline = at(9)
    res = planner.plan(req(WEST, EAST), deadline, arrive_by=True)
    its = [i for i in res.itineraries if i.access_mode == "walk"]
    assert len(its) == 3
    assert all(i.end <= deadline for i in its)
    # Latest departure first; buses every 15 min arriving S1_10 at :50/:05, so the 08:30 trip is the last that works.
    assert its[0].legs[1].trip_id == "S1_0_014"
    assert its[0].start > its[1].start > its[2].start
    assert len({i.legs[1].trip_id for i in its}) == 3


def test_arrive_by_with_transfer(planner):
    deadline = at(9)
    res = planner.plan(req(WEST, UPTOWN), deadline, arrive_by=True)
    it = next(i for i in res.itineraries if bus_routes(i) == ["S1", "S2"])
    buses = [l for l in it.legs if l.mode == "bus"]
    assert it.end <= deadline
    assert buses[1].start >= buses[0].end
    walk = [l for l in it.legs if l.mode == "walk"]
    assert walk[0].end <= buses[0].start and walk[-1].start >= buses[-1].end


def test_arrive_by_park_and_ride(planner):
    deadline = at(9)
    res = planner.plan(req((40.90, -74.35), EAST, park_ride=True, max_drive_m=10000), deadline, arrive_by=True)
    it = res.itineraries[0]
    assert it.access_mode == "park_ride" and it.end <= deadline
    assert [l.mode for l in it.legs[:2]] == ["drive", "walk"]


@pytest.mark.parametrize("deadline_h,deadline_m", [(7, 3), (8, 41), (12, 0), (23, 30)])
@pytest.mark.parametrize("o,d", [(WEST, EAST), (WEST, UPTOWN), (UPTOWN, (40.831, -74.281))])  # last: S2 -> S1 -> S3
def test_arrive_by_consistent_with_depart_at(planner, o, d, deadline_h, deadline_m):
    """Leaving when arrive-by says to must get you there by the deadline, and a forward search from that
    departure can't arrive later than the deadline."""
    deadline = at(deadline_h, deadline_m)
    ab = [i for i in planner.plan(req(o, d), deadline, arrive_by=True).itineraries if i.access_mode == "walk"]
    assert ab, "expected an arrive-by option"
    best = ab[0]
    assert best.end <= deadline
    fwd = [i for i in planner.plan(req(o, d), best.start).itineraries if i.access_mode == "walk"]
    assert fwd[0].end <= deadline


def test_arrive_by_walk_only_ends_at_deadline(planner):
    res = planner.plan(req((40.80, -74.20), (40.802, -74.201)), at(9), arrive_by=True)
    w = next(i for i in res.itineraries if i.access_mode == "walk_only")
    assert w.end == at(9)


def test_request_rejects_both_times():
    with pytest.raises(ValueError):
        req(WEST, EAST, depart_at=at(8), arrive_by=at(9))
