"""Fetch park & ride lots in New Jersey from OpenStreetMap via Overpass (no key needed).

    uv run python scripts/fetch_park_rides.py
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import DEFAULT_LOTS, get_settings  # noqa: E402

QUERY = """
[out:json][timeout:180];
area["ISO3166-2"="US-NJ"][admin_level=4]->.nj;
(
  nwr["amenity"="parking"]["park_ride"]["park_ride"!="no"](area.nj);
);
out center tags;
"""


# Public Overpass instances are often overloaded (429/502/504); try mirrors with backoff.
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


def query_overpass(urls: list[str], user_agent: str, attempts: int = 2) -> httpx.Response:
    errors = []
    with httpx.Client(timeout=300, headers={"User-Agent": user_agent}) as c:
        for attempt in range(attempts):
            for url in urls:
                try:
                    r = c.post(url, data={"data": QUERY})
                    r.raise_for_status()
                    r.json()  # overloaded servers sometimes return an HTML error page with 200
                    return r
                except (httpx.HTTPError, ValueError) as e:
                    msg = f"{url}: {e.response.status_code if isinstance(e, httpx.HTTPStatusError) else e}"
                    print(f"  {msg}", file=sys.stderr)
                    errors.append(msg)
            if attempt + 1 < attempts:
                print("  all servers failed; retrying in 30s...", file=sys.stderr)
                time.sleep(30)
    sys.exit("Overpass query failed on every server:\n  " + "\n  ".join(errors))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=DEFAULT_LOTS)
    args = ap.parse_args()
    s = get_settings()

    r = query_overpass([s.overpass_url, *(m for m in MIRRORS if m != s.overpass_url)], s.user_agent)
    lots = []
    for el in r.json().get("elements", []):
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
