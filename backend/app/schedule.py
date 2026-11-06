"""Keep the static schedule current: download a fresh NJT feed periodically and hot-reload
the timetable whenever the GTFS file changes (including manual downloads), without a restart."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .config import Settings
from .gtfs import Timetable, load_timetable
from .njt import NJTClient

log = logging.getLogger(__name__)


class ScheduleManager:
    def __init__(self, settings: Settings, path: Path, on_reload: Callable[[Timetable], None],
                 njt: NJTClient | None):
        self.s = settings
        self.path = path
        self.on_reload = on_reload
        self.njt = njt
        self._mtime = self._stat()
        self._state_file = settings.cache_dir / "gtfs_refresh.json"
        self.last_download: float | None = self._load_state()
        self.last_reload: float | None = None
        self.error: str | None = None
        self._task: asyncio.Task | None = None

    @property
    def auto_download(self) -> bool:
        return self.njt is not None and self.s.gtfs_refresh_hours > 0

    def _stat(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
            return st.st_mtime_ns, st.st_size
        except OSError:
            return None

    # Persisted so dev-server restarts don't re-download the 50 MB feed every time.
    def _load_state(self) -> float | None:
        try:
            return float(json.loads(self._state_file.read_text())["last_download"])
        except (OSError, ValueError, KeyError):
            return None

    def _save_state(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps({"last_download": self.last_download}))

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()

    async def check(self) -> None:
        if self.auto_download and (
            self.last_download is None or time.time() - self.last_download >= self.s.gtfs_refresh_hours * 3600
        ):
            log.info("Downloading latest NJ Transit GTFS")
            changed = await self.njt.download_gtfs(self.path)
            self.last_download = time.time()
            self._save_state()
            log.info("GTFS download complete (%s)", "new schedule" if changed else "unchanged")
        mtime = self._stat()
        if mtime is not None and mtime != self._mtime:
            log.info("GTFS file changed; reloading timetable")
            tt = await asyncio.to_thread(load_timetable, self.path, self.s.cache_dir, self.s.max_transfer_walk_m)
            self.on_reload(tt)
            self._mtime = mtime
            self.last_reload = time.time()
            log.info("Timetable reloaded: service %s to %s", tt.feed_start, tt.feed_end)
        self.error = None

    async def _run(self) -> None:
        while True:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # keep the current timetable; retry next cycle
                self.error = str(e)
                log.warning("Schedule refresh failed: %s", e)
            await asyncio.sleep(self.s.gtfs_check_minutes * 60)

    def status(self, tz) -> dict:
        ts = lambda t: datetime.fromtimestamp(t, tz) if t else None  # noqa: E731
        return {"auto_download": self.auto_download, "last_download": ts(self.last_download),
                "last_reload": ts(self.last_reload), "error": self.error}
