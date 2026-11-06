from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
SAMPLE_GTFS = DATA_DIR / "sample" / "gtfs.zip"
SAMPLE_LOTS = DATA_DIR / "sample" / "park_rides.json"
DEFAULT_GTFS = DATA_DIR / "njt_bus_gtfs.zip"
DEFAULT_LOTS = DATA_DIR / "park_rides.json"
DEFAULT_WATER = DATA_DIR / "water.json.gz"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # --- Data -------------------------------------------------------------
    # Leave empty for auto: data/njt_bus_gtfs.zip if present, else the sample feed.
    gtfs_path: Path | None = None
    park_ride_path: Path | None = None
    cache_dir: Path = DATA_DIR / "cache"
    timezone: str = "America/New_York"

    # --- NJ Transit developer portal (only needed by scripts/fetch_gtfs.py) --
    njt_username: str | None = None
    njt_password: str | None = None
    njt_bus_api_base: str = "https://pcsdata.njtransit.com/api/GTFSG2"
    # Realtime (needs the credentials above; disabled automatically with sample data)
    realtime_enabled: bool = True
    realtime_poll_s: int = 30
    alerts_poll_s: int = 300
    realtime_max_age_s: int = 300  # ignore live data older than this (e.g. feed outage)
    # Schedule refresh: download a new feed this often (0 = never), and reload when the file changes.
    gtfs_refresh_hours: float = 24
    gtfs_check_minutes: float = 5
    # Walks whose straight line crosses more water than this are rejected (needs data/water.json.gz).
    water_path: Path | None = None
    max_walk_water_m: float = 400

    # --- OpenStreetMap services (no keys required, but respect usage policies)
    user_agent: str = "nj-trip-planner/0.1 (local development)"
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    # Optional: real road/foot routing via OSRM. Off = straight-line estimates.
    use_osrm: bool = False
    osrm_drive_url: str = "https://routing.openstreetmap.de/routed-car"
    osrm_foot_url: str = "https://routing.openstreetmap.de/routed-foot"

    # --- Routing model ----------------------------------------------------
    walk_speed_mps: float = 1.3  # ~2.9 mph
    walk_detour: float = 1.3  # street distance / straight-line distance
    drive_speed_mps: float = 11.0  # ~25 mph average incl. local streets
    drive_detour: float = 1.35
    park_buffer_s: int = 300  # time to park and get out of the car
    park_ride_walk_m: float = 500  # max walk from a lot to a bus stop
    min_transfer_s: int = 60  # slack when transferring at the same stop
    max_transfer_walk_m: float = 400  # cap on walking between stops mid-trip

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    def _abs(self, p: Path) -> Path:
        return p if p.is_absolute() else (BACKEND_DIR / p)

    @property
    def resolved_gtfs_path(self) -> Path:
        if self.gtfs_path:
            return self._abs(self.gtfs_path)
        return DEFAULT_GTFS if DEFAULT_GTFS.exists() else SAMPLE_GTFS

    @property
    def using_sample(self) -> bool:
        return self.resolved_gtfs_path.resolve() == SAMPLE_GTFS.resolve()

    @property
    def resolved_water_path(self) -> Path | None:
        if self.water_path:
            return self._abs(self.water_path)
        return DEFAULT_WATER if DEFAULT_WATER.exists() and not self.using_sample else None

    @property
    def resolved_park_ride_path(self) -> Path | None:
        if self.park_ride_path:
            return self._abs(self.park_ride_path)
        if DEFAULT_LOTS.exists():
            return DEFAULT_LOTS
        return SAMPLE_LOTS if self.using_sample else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
