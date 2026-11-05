import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParkRideLot:
    id: str
    name: str
    lat: float
    lon: float
    capacity: int | None = None
    operator: str | None = None


def load_lots(path: Path | None) -> list[ParkRideLot]:
    """Load lots written by scripts/fetch_park_rides.py (a JSON list)."""
    if path is None or not path.exists():
        log.warning("No park & ride lot data loaded (%s)", path)
        return []
    raw = json.loads(path.read_text())
    lots = []
    for r in raw:
        try:
            cap = r.get("capacity")
            lots.append(ParkRideLot(
                id=str(r["id"]), name=r.get("name") or "Park & Ride",
                lat=float(r["lat"]), lon=float(r["lon"]),
                capacity=int(cap) if cap not in (None, "") else None,
                operator=r.get("operator"),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    log.info("Loaded %d park & ride lots from %s", len(lots), path)
    return lots
