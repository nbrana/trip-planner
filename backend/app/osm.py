"""Thin async clients for OpenStreetMap-based services (Nominatim, OSRM)."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from .config import Settings
from .schemas import GeocodeResult

log = logging.getLogger(__name__)

# New Jersey plus a margin, used to bias geocoding results.
NJ_VIEWBOX = "-75.65,41.40,-73.85,38.85"


class OSMClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self.http = httpx.AsyncClient(timeout=20, headers={"User-Agent": settings.user_agent})
        self._nominatim_lock = asyncio.Lock()
        self._last_nominatim = 0.0
        self._cache: dict[tuple, list[GeocodeResult]] = {}

    async def close(self) -> None:
        await self.http.aclose()

    async def _nominatim(self, path: str, params: dict) -> httpx.Response:
        # Public Nominatim policy: at most 1 request per second.
        async with self._nominatim_lock:
            wait = 1.0 - (time.monotonic() - self._last_nominatim)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                r = await self.http.get(f"{self.s.nominatim_url}{path}", params=params)
            finally:
                self._last_nominatim = time.monotonic()
        r.raise_for_status()
        return r

    async def geocode(self, q: str) -> list[GeocodeResult]:
        key = ("search", q.strip().lower())
        if key in self._cache:
            return self._cache[key]
        r = await self._nominatim("/search", {
            "q": q, "format": "jsonv2", "limit": 6, "countrycodes": "us", "viewbox": NJ_VIEWBOX,
        })
        out = [GeocodeResult(name=x["display_name"], lat=float(x["lat"]), lon=float(x["lon"])) for x in r.json()]
        self._cache[key] = out
        return out

    async def reverse(self, lat: float, lon: float) -> GeocodeResult | None:
        key = ("reverse", round(lat, 5), round(lon, 5))
        if key in self._cache:
            return self._cache[key][0] if self._cache[key] else None
        r = await self._nominatim("/reverse", {"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 18})
        data = r.json()
        out = [] if "error" in data else [GeocodeResult(name=data["display_name"], lat=lat, lon=lon)]
        self._cache[key] = out
        return out[0] if out else None

    async def drive_table(self, origin: tuple[float, float],
                          dests: list[tuple[float, float]]) -> list[tuple[float, float] | None]:
        """OSRM table: [(distance_m, duration_s)] from origin to each destination."""
        if not dests:
            return []
        coords = ";".join(f"{lon},{lat}" for lat, lon in [origin, *dests])
        r = await self.http.get(f"{self.s.osrm_drive_url}/table/v1/driving/{coords}",
                                params={"sources": "0", "annotations": "distance,duration"})
        r.raise_for_status()
        data = r.json()
        dist, dur = data["distances"][0][1:], data["durations"][0][1:]
        return [(a, b) if a is not None and b is not None else None for a, b in zip(dist, dur)]

    async def route_geometry(self, mode: str, pts: list[tuple[float, float]]) -> list[tuple[float, float]] | None:
        base = self.s.osrm_drive_url if mode == "drive" else self.s.osrm_foot_url
        profile = "driving" if mode == "drive" else "foot"
        coords = ";".join(f"{lon},{lat}" for lat, lon in pts)
        try:
            r = await self.http.get(f"{base}/route/v1/{profile}/{coords}",
                                    params={"overview": "full", "geometries": "geojson"})
            r.raise_for_status()
            route = r.json()["routes"][0]
            return [(lat, lon) for lon, lat in route["geometry"]["coordinates"]]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            log.warning("OSRM route failed: %s", e)
            return None
