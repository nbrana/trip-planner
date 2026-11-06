"""How much water a straight walking line crosses.

Walk legs are estimated as straight lines, which happily cross the Hudson. Using OSM
coastlines (sea on the right of each line) and lake/river polygons, we compute the metres
of water along a segment so planners can reject walks that cross wide water (bridges over
narrow rivers stay allowed).
"""

from __future__ import annotations

import gzip
import json
import logging
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from shapely import STRtree
from shapely.geometry import LineString, Polygon

log = logging.getLogger(__name__)
CELL = 0.01  # degrees; grid cell for coastline edge lookup (~1 km)


class WaterIndex:
    def __init__(self, coast: list[np.ndarray], areas: list[Polygon]):
        # Coastline edges as (x1, y1, x2, y2) in lon/lat; sea lies to the right of x1y1 -> x2y2.
        segs = [np.column_stack([c[:-1], c[1:]]) for c in coast if len(c) >= 2]
        self.edges = np.concatenate(segs) if segs else np.empty((0, 4))
        self.grid: dict[tuple[int, int], np.ndarray] = {}
        buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
        e = self.edges
        x0 = np.floor(np.minimum(e[:, 0], e[:, 2]) / CELL).astype(int)
        x1 = np.floor(np.maximum(e[:, 0], e[:, 2]) / CELL).astype(int)
        y0 = np.floor(np.minimum(e[:, 1], e[:, 3]) / CELL).astype(int)
        y1 = np.floor(np.maximum(e[:, 1], e[:, 3]) / CELL).astype(int)
        for i in range(len(e)):
            for cx in range(x0[i], x1[i] + 1):
                for cy in range(y0[i], y1[i] + 1):
                    buckets[(cx, cy)].append(i)
        self.grid = {k: np.array(v) for k, v in buckets.items()}
        self.areas = areas
        self.tree = STRtree(areas) if areas else None

    @classmethod
    def load(cls, path: Path | None) -> "WaterIndex | None":
        if path is None or not path.exists():
            log.warning("No water data (%s): walks may cross rivers. Run scripts/fetch_water.py", path)
            return None
        with gzip.open(path, "rt") as f:
            data = json.load(f)
        coast = [np.asarray(c, dtype=float) for c in data["coast"]]
        areas = [Polygon(a["o"], a["i"]) for a in data["areas"]]
        idx = cls(coast, areas)
        log.info("Loaded water data: %d coastline edges, %d water areas", len(idx.edges), len(areas))
        return idx

    def _coast_intervals(self, ax: float, ay: float, bx: float, by: float) -> list[tuple[float, float]]:
        cx0, cx1 = sorted((math.floor(ax / CELL), math.floor(bx / CELL)))
        cy0, cy1 = sorted((math.floor(ay / CELL), math.floor(by / CELL)))
        cand = [self.grid[k] for cx in range(cx0, cx1 + 1) for cy in range(cy0, cy1 + 1)
                if (k := (cx, cy)) in self.grid]
        if not cand:
            return []
        e = self.edges[np.unique(np.concatenate(cand))]
        dx, dy = bx - ax, by - ay
        ex, ey = e[:, 2] - e[:, 0], e[:, 3] - e[:, 1]
        denom = dx * ey - dy * ex
        ok = np.abs(denom) > 1e-15
        px, py = e[:, 0] - ax, e[:, 1] - ay
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (px * ey - py * ex) / denom
            u = (px * dy - py * dx) / denom
        hit = ok & (t >= 0) & (t <= 1) & (u >= 0) & (u < 1)  # u < 1: count shared vertices once
        if not hit.any():
            return []
        # cross(edge, walk) < 0  <=> walking to the edge's right, i.e. into the sea
        into_sea = (ex * dy - ey * dx)[hit] < 0
        order = np.argsort(t[hit], kind="stable")
        ts, ins = t[hit][order], into_sea[order]
        # Start state: if the first crossing leaves the sea, we started in it.
        in_sea, start = (not ins[0]), 0.0
        out = []
        for tt, entering in zip(ts.tolist(), ins.tolist()):
            if entering and not in_sea:
                in_sea, start = True, tt
            elif not entering and in_sea:
                out.append((start, tt))
                in_sea = False
        if in_sea:
            out.append((start, 1.0))
        return out

    def _area_intervals(self, seg: LineString) -> list[tuple[float, float]]:
        if self.tree is None:
            return []
        out = []
        for i in self.tree.query(seg, predicate="intersects"):
            inter = self.areas[i].intersection(seg)
            parts = getattr(inter, "geoms", [inter])
            for part in parts:
                if part.geom_type == "LineString" and not part.is_empty:
                    a, b = (seg.project(part.interpolate(d), normalized=True) for d in (0, part.length))
                    out.append((min(a, b), max(a, b)))
        return out

    def water_m(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Metres of water along the straight line between two points."""
        if lat1 == lat2 and lon1 == lon2:
            return 0.0
        ivs = self._coast_intervals(lon1, lat1, lon2, lat2)
        ivs += self._area_intervals(LineString([(lon1, lat1), (lon2, lat2)]))
        if not ivs:
            return 0.0
        ivs.sort()
        total, cur_a, cur_b = 0.0, *ivs[0]
        for a, b in ivs[1:]:
            if a > cur_b:
                total += cur_b - cur_a
                cur_a, cur_b = a, b
            else:
                cur_b = max(cur_b, b)
        total += cur_b - cur_a
        k = math.cos(math.radians((lat1 + lat2) / 2))
        length = math.hypot((lon2 - lon1) * 111_320 * k, (lat2 - lat1) * 110_540)
        return total * length
