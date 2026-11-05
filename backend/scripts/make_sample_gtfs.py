"""Generate a small SYNTHETIC GTFS feed + park & ride lots for development.

These routes, stops and lots are invented (labelled "Sample") so the app can be
run and tested before NJ Transit credentials are available.

    uv run python scripts/make_sample_gtfs.py
"""

import csv
import io
import json
import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "sample"


def line(a, b, n):
    return [(a[0] + (b[0] - a[0]) * i / (n - 1), a[1] + (b[1] - a[1]) * i / (n - 1)) for i in range(n)]


def hms(s):
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def main():
    stops: dict[str, tuple[str, float, float]] = {}

    def stop(sid, name, lat, lon):
        stops.setdefault(sid, (name, round(lat, 6), round(lon, 6)))
        return sid

    # S1: west-east along 40.80; S2: south-north along -74.20; they share CENTRAL.
    s1 = [stop("CENTRAL" if i == 6 else f"S1_{i}", "Sample Central Transfer" if i == 6 else f"Sample East-West Ave {i}",
               lat, lon) for i, (lat, lon) in enumerate(line((40.80, -74.26), (40.80, -74.14), 13))]
    s2 = [stop("CENTRAL" if i == 6 else f"S2_{i}", "Sample Central Transfer" if i == 6 else f"Sample North-South St {i}",
               lat, lon) for i, (lat, lon) in enumerate(line((40.74, -74.20), (40.86, -74.20), 13))]
    # S3: feeder from the north-west into S1's western terminal.
    s3 = [stop(s1[0] if i == 6 else f"S3_{i}", stops.get(s1[0], ("",))[0] if i == 6 else f"Sample Hill Rd {i}",
               lat, lon) for i, (lat, lon) in enumerate(line((40.86, -74.30), (40.80, -74.26), 7))]

    routes = [("S1", "S1", "Sample Crosstown", "1D4ED8", s1),
              ("S2", "S2", "Sample Uptown", "B91C1C", s2),
              ("S3", "S3", "Sample Hill Feeder", "047857", s3)]

    trips, stop_times = [], []
    for rid, _, long_name, _, seq in routes:
        for direction, stop_seq in ((0, seq), (1, list(reversed(seq)))):
            head = stops[stop_seq[-1]][0]
            # Every 15 min from 05:00, last departure 24:15 (after midnight, GTFS-style)
            for k, start in enumerate(range(5 * 3600, 24 * 3600 + 15 * 60 + 1, 15 * 60)):
                tid = f"{rid}_{direction}_{k:03d}"
                trips.append((rid, "DAILY", tid, head, direction))
                for i, sid in enumerate(stop_seq):
                    t = start + i * 120
                    stop_times.append((tid, hms(t), hms(t), sid, i + 1))

    files = {
        "agency.txt": [("agency_id", "agency_name", "agency_url", "agency_timezone"),
                       ("SAMPLE", "Sample Transit (synthetic data)", "https://example.com", "America/New_York")],
        "stops.txt": [("stop_id", "stop_name", "stop_lat", "stop_lon")]
                     + [(sid, n, lat, lon) for sid, (n, lat, lon) in stops.items()],
        "routes.txt": [("route_id", "agency_id", "route_short_name", "route_long_name", "route_type", "route_color", "route_text_color")]
                      + [(r[0], "SAMPLE", r[1], r[2], 3, r[3], "FFFFFF") for r in routes],
        "trips.txt": [("route_id", "service_id", "trip_id", "trip_headsign", "direction_id")] + trips,
        "stop_times.txt": [("trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence")] + stop_times,
        "calendar.txt": [("service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"),
                         ("DAILY", 1, 1, 1, 1, 1, 1, 1, "20260101", "20281231")],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT / "gtfs.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for name, rows in files.items():
            buf = io.StringIO()
            csv.writer(buf, lineterminator="\n").writerows(rows)
            z.writestr(name, buf.getvalue())

    lots = [
        {"id": "sample-north", "name": "Sample Hill Park & Ride", "lat": 40.8625, "lon": -74.3015, "capacity": 250},
        {"id": "sample-east", "name": "Sample East Park & Ride", "lat": 40.8025, "lon": -74.1385, "capacity": 120},
    ]
    (OUT / "park_rides.json").write_text(json.dumps(lots, indent=2))
    print(f"Wrote {OUT / 'gtfs.zip'} and {OUT / 'park_rides.json'}")


if __name__ == "__main__":
    main()
