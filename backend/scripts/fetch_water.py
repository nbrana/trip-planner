"""Fetch water geometry for the NJ region from OpenStreetMap, so walks can't cross rivers and bays.

    uv run python scripts/fetch_water.py      # -> data/water.json.gz (takes a few minutes)

Two sources are needed:
- natural=coastline lines. The tidal Hudson, Delaware, Newark Bay, the Kills and the
  ocean bays are "sea" in OSM: they exist only as coastline (land on the left).
- Lake / reservoir / non-tidal river polygons.

The region is fetched in tiles because one big query times out on public servers.
Finished tiles are cached in data/cache/water_tiles/, so an interrupted run resumes.
Data © OpenStreetMap contributors (ODbL).
"""

import argparse
import gzip
import json
import math
import shutil
import sys
from pathlib import Path

from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import polygonize, unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import overpass  # noqa: E402
from app.config import DEFAULT_WATER, get_settings  # noqa: E402

BBOX = (38.85, -75.65, 41.40, -73.85)  # S, W, N, E: NJ plus neighbouring NYC/Philadelphia
TILE = 0.5
MIN_AREA_M2 = 150_000  # smaller water bodies can't block a 400 m crossing

QUERY = """[out:json][timeout:300][bbox:{s},{w},{n},{e}];
(
  way["natural"="coastline"];
  wr["natural"="water"]["water"~"^(river|lake|reservoir|lagoon|basin|canal|oxbow)$"];
  wr["waterway"="riverbank"];
);
out geom qt;"""


def coords(geom) -> list[tuple[float, float]]:
    return [(round(p["lon"], 6), round(p["lat"], 6)) for p in geom if p]


def area_m2(poly: Polygon) -> float:
    lat = poly.centroid.y
    return poly.area * 111_320 * math.cos(math.radians(lat)) * 110_540


def to_polygons(el) -> list[Polygon]:
    if el["type"] == "way":
        pts = coords(el.get("geometry", []))
        return [Polygon(pts)] if len(pts) >= 4 and pts[0] == pts[-1] else []
    rings = {"outer": [], "inner": []}
    for m in el.get("members", []):
        if m.get("type") == "way" and m.get("geometry"):
            rings["inner" if m.get("role") == "inner" else "outer"].append(LineString(coords(m["geometry"])))
    outer = list(polygonize(unary_union(rings["outer"]))) if rings["outer"] else []
    inner = list(polygonize(unary_union(rings["inner"]))) if rings["inner"] else []
    if not outer:
        return []
    shape = unary_union(outer)
    if inner:
        shape = shape.difference(unary_union(inner))
    return list(shape.geoms) if isinstance(shape, MultiPolygon) else [shape] if isinstance(shape, Polygon) else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=DEFAULT_WATER)
    args = ap.parse_args()
    s = get_settings()

    coast: dict[int, list] = {}
    areas_raw: dict[tuple[str, int], dict] = {}
    south, west, north, east = BBOX
    tiles = [(la, lo) for la in frange(south, north) for lo in frange(west, east)]
    tile_dir = s.cache_dir / "water_tiles"
    tile_dir.mkdir(parents=True, exist_ok=True)
    for k, (la, lo) in enumerate(tiles, 1):
        q = QUERY.format(s=la, w=lo, n=min(la + TILE, north), e=min(lo + TILE, east))
        cached = tile_dir / f"{la:.2f}_{lo:.2f}.json.gz"
        if cached.exists():
            with gzip.open(cached, "rt") as f:
                data = json.load(f)
            print(f"tile {k}/{len(tiles)} ({la:.2f}, {lo:.2f}) cached", file=sys.stderr, flush=True)
        else:
            print(f"tile {k}/{len(tiles)} ({la:.2f}, {lo:.2f}) fetching", file=sys.stderr, flush=True)
            try:
                data = overpass.query(q, s.user_agent, primary=s.overpass_url)
            except RuntimeError as e:
                sys.exit(f"{e}\n{k - 1}/{len(tiles)} tiles cached; re-run to resume.")
            with gzip.open(cached, "wt") as f:
                json.dump(data, f)
        for el in data.get("elements", []):
            tags = el.get("tags", {})
            if el["type"] == "way" and tags.get("natural") == "coastline":
                coast[el["id"]] = coords(el.get("geometry", []))
            else:
                areas_raw[(el["type"], el["id"])] = el

    areas = []
    for el in areas_raw.values():
        try:
            polys = to_polygons(el)
        except Exception:  # broken multipolygons in OSM: skip
            continue
        for p in polys:
            if p.is_valid and area_m2(p) >= MIN_AREA_M2:
                areas.append({"o": [list(c) for c in p.exterior.coords],
                              "i": [[list(c) for c in r.coords] for r in p.interiors]})

    out = {"version": 1, "bbox": BBOX, "coast": [c for c in coast.values() if len(c) >= 2], "areas": areas}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.out, "wt") as f:
        json.dump(out, f, separators=(",", ":"))
    shutil.rmtree(tile_dir, ignore_errors=True)
    print(f"Saved {len(out['coast'])} coastline ways and {len(areas)} water areas to {args.out} "
          f"({args.out.stat().st_size / 1e6:.1f} MB). Data © OpenStreetMap contributors (ODbL).")


def frange(a: float, b: float):
    x = a
    while x < b - 1e-9:
        yield round(x, 4)
        x += TILE


if __name__ == "__main__":
    main()
