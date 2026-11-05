from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from google.transit import gtfs_realtime_pb2 as pb

from app.config import SAMPLE_GTFS, SAMPLE_LOTS, Settings
from app.gtfs import load_timetable
from app.lots import load_lots
from app.planner import Planner
from app.realtime import Snapshot, build_trip_index, parse_alerts, parse_trip_updates, parse_vehicles
from app.schemas import PlanRequest, Point

TZ = ZoneInfo("America/New_York")
DAY = date(2026, 11, 4)
TRIP = "S1_0_012"  # eastbound S1 leaving its first stop at 08:00, S1_1 at 08:02


@pytest.fixture(scope="module")
def tt(tmp_path_factory):
    s = Settings(_env_file=None)
    return load_timetable(SAMPLE_GTFS, tmp_path_factory.mktemp("cache"), s.max_transfer_walk_m)


@pytest.fixture
def planner(tt):
    return Planner(tt, load_lots(SAMPLE_LOTS), Settings(_env_file=None))


def epoch(h, m):
    return int(datetime(2026, 11, 4, h, m, tzinfo=TZ).timestamp())


def feed(*entities):
    msg = pb.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    for i, fill in enumerate(entities):
        e = msg.entity.add(id=str(i))
        fill(e)
    return msg


def delayed(trip_id, seq, sched_h, sched_m, delay):
    def fill(e):
        e.trip_update.trip.trip_id = trip_id
        u = e.trip_update.stop_time_update.add(stop_sequence=seq)
        u.arrival.delay = delay
        u.arrival.scheduled_time = epoch(sched_h, sched_m)
        u.arrival.time = epoch(sched_h, sched_m) + delay
    return fill


def canceled(trip_id):
    def fill(e):
        e.trip_update.trip.trip_id = trip_id
        e.trip_update.trip.schedule_relationship = pb.TripDescriptor.CANCELED
    return fill


def plan_s1(planner, snap):
    planner.realtime_snapshot = lambda: snap
    req = PlanRequest(origin=Point(lat=40.801, lon=-74.25), destination=Point(lat=40.801, lon=-74.16))
    res = planner.plan(req, datetime(2026, 11, 4, 8, 0, tzinfo=TZ))
    return next(l for l in res.itineraries[0].legs if l.mode == "bus")


def test_parse_trip_update_derives_service_day_and_propagates(tt):
    delays, cancel = parse_trip_updates(tt, build_trip_index(tt), feed(delayed(TRIP, 2, 8, 2, 300)), TZ)
    d = delays[(TRIP, DAY)]
    assert not cancel
    assert d.arr.tolist() == [300] * len(d.arr)  # back-filled before, carried forward after


def test_delay_shifts_trip(planner, tt):
    delays, _ = parse_trip_updates(tt, build_trip_index(tt), feed(delayed(TRIP, 2, 8, 2, 300)), TZ)
    leg = plan_s1(planner, Snapshot(version=1, today=DAY, delays=delays))
    assert leg.trip_id == TRIP and leg.realtime
    assert leg.delay_s == 300
    assert leg.start.strftime("%H:%M") == "08:07" and leg.scheduled_start.strftime("%H:%M") == "08:02"


def test_cancellation_skips_trip(planner, tt):
    _, cancel = parse_trip_updates(tt, build_trip_index(tt), feed(canceled(TRIP)), TZ)
    leg = plan_s1(planner, Snapshot(version=2, today=DAY, canceled=cancel))
    assert leg.trip_id == "S1_0_013"
    assert leg.start.strftime("%H:%M") == "08:17"


def test_snapshot_ignored_for_other_days(planner, tt):
    delays, _ = parse_trip_updates(tt, build_trip_index(tt), feed(delayed(TRIP, 2, 8, 2, 300)), TZ)
    leg = plan_s1(planner, Snapshot(version=3, today=date(2026, 11, 10), delays=delays))
    assert not leg.realtime and leg.start.strftime("%H:%M") == "08:02"


def test_vehicle_and_alert_attached(planner, tt):
    def veh(e):
        e.vehicle.trip.trip_id = TRIP
        e.vehicle.trip.route_id = "S1"
        e.vehicle.position.latitude, e.vehicle.position.longitude = 40.8, -74.255
        e.vehicle.vehicle.label = "1602"
        e.vehicle.occupancy_status = pb.VehiclePosition.FEW_SEATS_AVAILABLE

    def alert(e):
        e.alert.informed_entity.add(route_id="S1")
        e.alert.header_text.translation.add(text="Detour on S1")

    def greeting(e):  # stop-only "signage" alerts are dropped
        e.alert.informed_entity.add(stop_id="S1_1")
        e.alert.header_text.translation.add(text="Have a nice day")

    delays, _ = parse_trip_updates(tt, build_trip_index(tt), feed(delayed(TRIP, 2, 8, 2, 60)), TZ)
    snap = Snapshot(version=4, today=DAY, delays=delays, vehicles=parse_vehicles(feed(veh)),
                    alerts_by_route=parse_alerts(feed(alert, greeting)))
    leg = plan_s1(planner, snap)
    assert leg.vehicle and leg.vehicle.label == "1602" and leg.vehicle.occupancy == "Few seats available"
    assert [a.header for a in leg.alerts] == ["Detour on S1"]
