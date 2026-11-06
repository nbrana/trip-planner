"""Fetch park & ride lots in New Jersey from OpenStreetMap via Overpass (no key needed).

    uv run python scripts/fetch_park_rides.py
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import overpass  # noqa: E402
from app.config import DEFAULT_LOTS, get_settings  # noqa: E402

QUERY = """
[out:json][timeout:180];
area["ISO3166-2"="US-NJ"][admin_level=4]->.nj;
(
  nwr["amenity"="parking"]["park_ride"]["park_ride"!="no"](area.nj);
);
out center tags;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=DEFAULT_LOTS)
    args = ap.parse_args()
    s = get_settings()

    try:
        data = overpass.query(QUERY, s.user_agent, primary=s.overpass_url, attempts=2)
    except RuntimeError as e:
        sys.exit(str(e))
    lots = []
    for el in data.get("elements", []):
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        cap = tags.get("capacity")
        lots.append({
            "id": f"osm-{el['type']}-{el['id']}",
            "name": tags.get("name") or "Park & Ride",
            "lat": lat, "lon": lon,
            "capacity": int(cap) if cap and cap.isdigit() else None,
            "operator": tags.get("operator"),
        })
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(lots, indent=1))
    print(f"Saved {len(lots)} park & ride lots to {args.out}. Data © OpenStreetMap contributors (ODbL).")


if __name__ == "__main__":
    main()
