import math

import numpy as np

EARTH_R = 6_371_008.8
M_PER_DEG_LAT = 111_320.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def haversine_np(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    p1 = math.radians(lat)
    p2 = np.radians(lats)
    dp = p2 - p1
    dl = np.radians(lons - lon)
    a = np.sin(dp / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_R * np.arcsin(np.sqrt(a))


class PointIndex:
    """Radius search over a static point set using a latitude-sorted window."""

    def __init__(self, lats, lons):
        self.lats = np.asarray(lats, dtype=float)
        self.lons = np.asarray(lons, dtype=float)
        self.order = np.argsort(self.lats, kind="stable")
        self.sorted_lats = self.lats[self.order]

    def __len__(self) -> int:
        return len(self.lats)

    def within(self, lat: float, lon: float, radius_m: float) -> tuple[np.ndarray, np.ndarray]:
        """Indices and distances (m) of points within radius, nearest first."""
        if len(self) == 0 or radius_m <= 0:
            return np.empty(0, dtype=int), np.empty(0)
        dlat = radius_m / M_PER_DEG_LAT
        lo = np.searchsorted(self.sorted_lats, lat - dlat, "left")
        hi = np.searchsorted(self.sorted_lats, lat + dlat, "right")
        cand = self.order[lo:hi]
        d = haversine_np(lat, lon, self.lats[cand], self.lons[cand])
        mask = d <= radius_m
        cand, d = cand[mask], d[mask]
        o = np.argsort(d, kind="stable")
        return cand[o], d[o]
